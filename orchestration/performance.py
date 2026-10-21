"""Measured scaling and controlled Spark query comparisons (ENG-2/ENG-3)."""
from copy import copy
from pathlib import Path
import shutil
import statistics
import time
from datetime import datetime, timezone
from pyspark.sql import functions as F
from pipeline.common import json_write


def benchmark_optimisations(spark, root):
    root = Path(root)
    out = root.parent / 'results/performance'
    out.mkdir(parents=True, exist_ok=True)
    encounters = spark.read.parquet(str(root / 'lake/silver/encounters'))
    facilities = spark.read.parquet(str(root / 'lake/silver/facilities')).select('facility_id', 'beds')
    results = []

    def measure(label, query):
        query.explain(mode='formatted')
        (out / f'{label}_plan.txt').write_text(query._jdf.queryExecution().executedPlan().toString(), encoding='utf-8')
        times = []
        for _ in range(3):
            start = time.monotonic()
            value = [r.asDict() for r in query.collect()]
            times.append(time.monotonic()-start)
        return {'label':label,'seconds':times,'median_seconds':statistics.median(times),'result':value}

    old_aqe = spark.conf.get('spark.sql.adaptive.enabled')
    old_broadcast = spark.conf.get('spark.sql.autoBroadcastJoinThreshold')
    try:
        spark.conf.set('spark.sql.adaptive.enabled', 'false')
        spark.conf.set('spark.sql.autoBroadcastJoinThreshold', '-1')
        before = encounters.hint('merge').join(facilities.hint('merge'), 'facility_id').agg(F.sum('beds').alias('bed_weighted_encounters'))
        results.append(measure('join_before_sort_merge', before))
        after = encounters.join(F.broadcast(facilities), 'facility_id').agg(F.sum('beds').alias('bed_weighted_encounters'))
        results.append(measure('join_after_broadcast', after))
        # Identical output: predicate expressed through an opaque Python UDF
        # prevents partition pruning in the reference query. The optimized
        # native predicate prunes all nonselected epi_year directories.
        year = encounters.agg(F.max('epi_year')).first()[0]
        opaque_year = F.udf(lambda value: value == year, 'boolean')
        results.append(measure('pruning_before_opaque_predicate', encounters.filter(opaque_year('epi_year')).agg(F.count('*').alias('encounters'))))
        results.append(measure('pruning_after_native_predicate', encounters.filter(F.col('epi_year') == year).agg(F.count('*').alias('encounters'))))
    finally:
        spark.conf.set('spark.sql.adaptive.enabled', old_aqe)
        spark.conf.set('spark.sql.autoBroadcastJoinThreshold', old_broadcast)
    comparisons = []
    for i in (0, 2):
        a, b = results[i:i+2]
        comparisons.append({'before':a['label'],'after':b['label'],'identical_result':a['result']==b['result'],
                            'speedup':a['median_seconds']/b['median_seconds']})
    result = {'experiments':results,'comparisons':comparisons,'spark_ui':'http://127.0.0.1:4040',
              'note':'Three executions per query, medians shown. These are controlled query comparisons, not whole-pipeline speedups. Filesystem cache and laptop contention remain confounders.'}
    json_write(out / 'optimisations.json', result)
    return result


def run_scaling(args):
    from orchestration.cli import REPOSITORY, run
    base = Path(args.root).resolve()
    results = []
    experiment_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    for scale in (.25, .5, 1.):
        profile = REPOSITORY / 'runs' / experiment_id / ('sf' + str(scale).replace('.', '_'))
        target = profile / 'data'
        target.mkdir(parents=True, exist_ok=True)
        if not (target / 'reference').exists():
            shutil.copytree(base / 'reference', target / 'reference')
        # Original immutable downloads are reused, never refetched or faked.
        cache = base / 'bronze/reference'
        if cache.exists() and not (target / 'bronze/reference').exists():
            shutil.copytree(cache, target / 'bronze/reference')
        scenario = copy(args)
        scenario.root, scenario.scale, scenario.action = str(target), scale, 'all'
        result = run(scenario)
        if any(stage.get('result',{}).get('idempotent_noop') for stage in result['stages']):
            raise AssertionError('A scaling run cannot substitute an idempotent no-op for full transformations')
        result['experiment_id'] = experiment_id
        result['measurement'] = 'fresh generated feed and lake; cached public references'
        result['under_45_minutes'] = result['total_seconds'] <= 2700
        result['input_bytes'] = sum(p.stat().st_size for p in (target/'incoming').rglob('*') if p.is_file())
        public_keys=('status','started_at','completed_at','total_seconds','hardware','scale','seed',
                     'generated_encounter_rows','generated_unique_encounters','facilities','stock_rows',
                     'output_bytes','input_bytes','under_45_minutes','experiment_id','measurement')
        public={key:result.get(key) for key in public_keys}
        public['profile']=str(profile.relative_to(REPOSITORY))
        public['stages']=[{key:stage[key] for key in ('stage','seconds','attempt','status')} for stage in result['stages']]
        results.append(public)
        json_write(REPOSITORY / 'results/scalability.json', {'experiment_id':experiment_id,'runs':results})
    return results

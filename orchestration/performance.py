"""Measured scaling and controlled Spark query comparisons (ENG-2/ENG-3)."""
from pathlib import Path
import json
import hashlib
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pipeline.common import json_write


QUERY_TIMING_SCOPE = (
    'Each sample builds a new DataFrame/QueryExecution before timing. The timer covers '
    'collect(), including lazy optimisation/physical planning, Spark work and aggregate '
    'result conversion. Factory construction, plan inspection/export and UI capture '
    'are excluded. No DataFrame is persisted; operating-system and JVM caches remain warm.'
)


def measure_query_samples(spark, label, query_factory, out):
    """Measure fresh executions rather than reusing a materialised shuffle plan."""
    out = Path(out)
    spark.sparkContext.setJobGroup(label, label)
    times, samples = [], []
    execution_ids, plan_ids = set(), set()
    reference = None
    for sample in range(1, 4):
        # A factory is essential: repeated collect() on one DataFrame can reuse
        # its ShuffleExchangeExec shuffleDependency/cachedShuffleRDD.
        query = query_factory()
        execution = query._jdf.queryExecution()
        execution_id = int(execution.id())
        if execution_id in execution_ids:
            raise ValueError('Each timing sample must have a fresh QueryExecution')
        execution_ids.add(execution_id)
        spark.sparkContext.setJobDescription(f'{label}: sample {sample}/3')
        started = time.monotonic()
        value = [row.asDict() for row in query.collect()]
        times.append(time.monotonic() - started)
        if sample == 1:
            reference = value
        elif value != reference:
            raise ValueError('Controlled query samples produced different aggregate results')
        # Inspect only after the timer so all three actions include lazy planning.
        plan = execution.executedPlan()
        plan_id = int(plan.id())
        if plan_id in plan_ids:
            raise ValueError('Each timing sample must have a fresh physical plan')
        plan_ids.add(plan_id)
        plan_text = plan.toString()
        plan_file = f'{label}_sample-{sample}_plan.txt'
        plan_path = out / plan_file
        plan_path.write_text(plan_text, encoding='utf-8', newline='\n')
        samples.append({'sample':sample, 'query_execution_id':execution_id,
                        'physical_plan_id':plan_id, 'plan_file':plan_file,
                        'plan_sha256':hashlib.sha256(plan_path.read_bytes()).hexdigest()})
    # Preserve the conventional representative plan, now the final fresh sample.
    (out / f'{label}_plan.txt').write_text(plan_text, encoding='utf-8', newline='\n')
    query.explain(mode='formatted')
    return {'label':label, 'seconds':times, 'median_seconds':statistics.median(times),
            'result':reference, 'sample_results_identical':True, 'samples':samples}


def benchmark_optimisations(spark, root):
    from pyspark.sql import functions as F
    root = Path(root)
    out = root.parent / 'results/performance'
    out.mkdir(parents=True, exist_ok=True)
    encounters = spark.read.parquet(str(root / 'lake/silver/encounters'))
    facilities = spark.read.parquet(str(root / 'lake/silver/facilities')).select('facility_id', 'beds')
    results = []

    old_aqe = spark.conf.get('spark.sql.adaptive.enabled')
    old_broadcast = spark.conf.get('spark.sql.autoBroadcastJoinThreshold')
    try:
        spark.conf.set('spark.sql.adaptive.enabled', 'false')
        spark.conf.set('spark.sql.autoBroadcastJoinThreshold', '-1')
        before = lambda: encounters.hint('merge').join(facilities.hint('merge'), 'facility_id').agg(F.sum('beds').alias('bed_weighted_encounters'))
        results.append(measure_query_samples(spark, 'join_before_sort_merge', before, out))
        after = lambda: encounters.join(F.broadcast(facilities), 'facility_id').agg(F.sum('beds').alias('bed_weighted_encounters'))
        results.append(measure_query_samples(spark, 'join_after_broadcast', after, out))
        # Identical output: predicate expressed through an opaque Python UDF
        # prevents partition pruning in the reference query. The optimized
        # native predicate prunes all nonselected epi_year directories.
        spark.sparkContext.setJobGroup('pruning_year_selection', 'Select the latest stored epi-year')
        spark.sparkContext.setJobDescription('Select the latest stored epi-year outside query timings')
        year = encounters.agg(F.max('epi_year')).first()[0]
        opaque_year = F.udf(lambda value: value == year, 'boolean')
        results.append(measure_query_samples(spark, 'pruning_before_opaque_predicate',
                       lambda: encounters.filter(opaque_year('epi_year')).agg(F.count('*').alias('encounters')), out))
        results.append(measure_query_samples(spark, 'pruning_after_native_predicate',
                       lambda: encounters.filter(F.col('epi_year') == year).agg(F.count('*').alias('encounters')), out))
    finally:
        spark.sparkContext.setLocalProperty('spark.jobGroup.id', None)
        spark.sparkContext.setLocalProperty('spark.job.description', None)
        spark.conf.set('spark.sql.adaptive.enabled', old_aqe)
        spark.conf.set('spark.sql.autoBroadcastJoinThreshold', old_broadcast)
    comparisons = []
    for i in (0, 2):
        a, b = results[i:i+2]
        comparisons.append({'before':a['label'],'after':b['label'],'identical_result':a['result']==b['result'],
                            'speedup':a['median_seconds']/b['median_seconds']})
    if not all(row['identical_result'] for row in comparisons):
        raise ValueError('Before/after queries did not produce identical aggregate results')
    result = {'experiments':results,'comparisons':comparisons,'spark_ui':'http://127.0.0.1:4040',
              'sampling':{'fresh_query_execution_per_sample':True, 'timing_scope':QUERY_TIMING_SCOPE},
              'note':'Three fresh query executions per case, medians shown. These are controlled query comparisons, not whole-pipeline speedups. Case order is fixed; filesystem/JVM cache and host contention remain confounders.'}
    json_write(out / 'optimisations.json', result)
    return result


def run_scaling(args):
    from orchestration.cli import REPOSITORY
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
        # Each profile gets its own Python/JVM lifetime. A stopped SparkContext
        # otherwise leaves the gateway heap resident during the next generator.
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        subprocess.run([sys.executable, '-m', 'orchestration.cli', 'all',
                        '--root', str(target), '--scale', str(scale),
                        '--seed', str(args.seed)], check=True, cwd=REPOSITORY)
        process_seconds = time.monotonic() - started
        completed_at = datetime.now(timezone.utc).isoformat()
        result = json.loads((profile/'results/run_all.json').read_text(encoding='utf-8'))
        if any(stage.get('result',{}).get('idempotent_noop') for stage in result['stages']):
            raise AssertionError('A scaling run cannot substitute an idempotent no-op for full transformations')
        result['experiment_id'] = experiment_id
        result['measurement'] = 'fresh generated feed and lake; cached public references'
        result['orchestrator_seconds'] = result['total_seconds']
        result['total_seconds'] = process_seconds
        result['started_at'], result['completed_at'] = started_at, completed_at
        result['timer_scope'] = 'child process start through exit, including Python/JVM startup and shutdown; reference cache copy excluded'
        result['under_45_minutes'] = process_seconds <= 2700
        result['input_bytes'] = sum(p.stat().st_size for p in (target/'incoming').rglob('*') if p.is_file())
        public_keys=('status','started_at','completed_at','total_seconds','hardware','scale','seed',
                     'generated_encounter_rows','generated_unique_encounters','facilities','stock_rows',
                     'output_bytes','input_bytes','under_45_minutes','experiment_id','measurement',
                     'orchestrator_seconds','timer_scope','source_revision')
        public={key:result.get(key) for key in public_keys}
        public['profile']=str(profile.relative_to(REPOSITORY))
        public['stages']=[{key:stage[key] for key in ('stage','seconds','attempt','status')} for stage in result['stages']]
        results.append(public)
        json_write(REPOSITORY / 'results/scalability.json', {'experiment_id':experiment_id,'runs':results})
    return results

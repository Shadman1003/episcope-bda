"""Immutable ingestion, quarantine, idempotent revisions and dense gold marts."""
from pathlib import Path
from datetime import datetime, timezone
import csv
import hashlib
import json
import shutil
import time
from pyspark import StorageLevel
from pyspark.sql import functions as F, Window
from .common import checksum, get_salt, json_write, publish
from .quality import clean_encounters


def ingest(root, drop=None):
    root = Path(root).resolve()
    manifest_path = root / 'bronze' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    known = {(item['source'], item['checksum']) for item in manifest}
    paths = []
    incoming = Path(drop).resolve() if drop else root / 'incoming'
    if incoming.is_file():
        candidates = [incoming]
    else:
        candidates = sorted(p for p in incoming.rglob('*') if p.is_file() and p.suffix.lower() in ('.csv', '.json', '.jsonl', '.ndjson'))
    for source_path in candidates:
        lowered = str(source_path.relative_to(incoming) if incoming.is_dir() else source_path.name).lower()
        source = 'stocks' if any(k in lowered for k in ('stock', 'occupancy', 'snapshot')) else 'encounters'
        # Hidden drops may have arbitrary filenames; identify the contract by header.
        with open(source_path, encoding='utf-8-sig') as stream:
            first = stream.readline()
        if 'encounter_id' in first:
            source = 'encounters'
        elif 'occupied_beds' in first or 'iv_saline' in first:
            source = 'stocks'
        elif 'facility_type' in first:
            source = 'facilities'
        else:
            raise ValueError(f'Unrecognised incremental file schema: {source_path.name}')
        paths.append((source_path, source))
    # All public and generated reference sources also retain their original bytes in bronze.
    paths += [(p, 'reference') for p in sorted((root / 'reference').rglob('*')) if p.is_file() and not p.name.startswith('.')]
    new = []
    for source_path, source in paths:
        sha = checksum(source_path)
        if (source, sha) in known:
            continue
        batch = sha[:20]
        dest = root / 'bronze' / source / batch / source_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, dest)
        row = {'batch_id': batch, 'source': source, 'loaded_at': datetime.now(timezone.utc).isoformat(),
               'checksum': sha, 'original_name': source_path.name, 'path': str(dest.relative_to(root)),
               'bytes': source_path.stat().st_size}
        manifest.append(row)
        new.append(row)
        known.add((source, sha))
    json_write(manifest_path, manifest)
    return manifest, new


def read_raw(spark, root, manifest, source):
    from pyspark.sql.types import StructType, StructField, StringType
    frames = []
    grouped = {}
    for item in manifest:
        if item['source'] == source:
            extension = Path(item['path']).suffix.lower()
            header = ()
            if extension == '.csv':
                with open(root / item['path'], encoding='utf-8-sig', newline='') as stream:
                    header = tuple(next(csv.reader(stream)))
            key = (extension, header)
            grouped.setdefault(key, []).append(str(root / item['path']))
    for (extension, _header), paths in grouped.items():
        if extension == '.csv':
            df = spark.read.option('header', True).option('mode', 'PERMISSIVE').csv(paths)
        else:
            df = spark.read.option('primitivesAsString', True).json(paths)
        df = (df.withColumn('_source_file', F.input_file_name())
              .withColumn('_batch_id', F.regexp_extract('_source_file', r'/([a-f0-9]{20})/[^/]+$', 1)))
        frames.append(df)
    if not frames:
        return None
    result = frames[0]
    for frame in frames[1:]:
        result = result.unionByName(frame, allowMissingColumns=True)
    loads = [(m['batch_id'], m['loaded_at']) for m in manifest if m['source'] == source]
    return result.join(F.broadcast(spark.createDataFrame(loads, ['_batch_id', '_loaded_at']).dropDuplicates()), '_batch_id')


def read_facilities(spark, root, manifest):
    f = spark.read.option('header', True).csv(str(root / 'reference' / 'facilities.csv'))
    revisions = read_raw(spark, root, manifest, 'facilities')
    if revisions is not None:
        f = f.withColumn('_loaded_at', F.lit('')).unionByName(revisions.drop('_batch_id', '_source_file'), allowMissingColumns=True)
        f = f.withColumn('_rn', F.row_number().over(Window.partitionBy('facility_id').orderBy(F.desc('_loaded_at')))).filter('_rn=1').drop('_rn', '_loaded_at')
    return f.withColumn('district_id', F.lpad('district_id', 2, '0')).withColumn('beds', F.col('beds').cast('int')).withColumn('icu_beds', F.col('icu_beds').cast('int'))


def build_gold(spark, root):
    silver = spark.read.parquet(str(root / 'lake/silver/encounters'))
    facilities = spark.read.parquet(str(root / 'lake/silver/facilities'))
    districts = spark.read.option('header', True).csv(str(root / 'reference/districts.csv'))
    districts = districts.withColumn('district_id', F.lpad('district_id', 2, '0')).withColumn('population', F.col('population').cast('long'))
    weather = spark.read.parquet(str(root / 'reference/weather.parquet')).withColumn('date', F.to_date('date')).withColumn('district_id', F.lpad('district_id', 2, '0'))
    bounds = silver.agg(F.min('onset_date'), F.max('onset_date'), F.max('report_date')).first()
    if bounds[0] is None:
        raise ValueError('No valid encounters remain after quality rules')
    dates = spark.sql(f"SELECT explode(sequence(date'{bounds[0]}',date'{bounds[1]}', interval 1 day)) as date")
    dense = districts.select('district_id', 'district_name', 'division', 'population').crossJoin(F.broadcast(spark.createDataFrame([(d,) for d in ('dengue', 'diarrhoea', 'influenza', 'malaria')], ['disease']))).crossJoin(F.broadcast(dates))
    grouped = silver.filter("disease <> 'other'").groupBy('district_id', 'disease', 'onset_date').agg(
        F.count('*').alias('cases'), F.sum(F.col('admitted').cast('long')).alias('admissions'),
        F.sum(F.lower('outcome').isin('died', 'death').cast('long')).alias('deaths'),
        F.avg(F.datediff('report_date', 'onset_date')).alias('mean_delay_days'),
        F.collect_set('_batch_id').alias('bronze_batch_ids')).withColumnRenamed('onset_date', 'date')
    reports = silver.filter("disease <> 'other'").groupBy('district_id', 'disease', 'report_date').count().withColumnRenamed('report_date', 'date').withColumnRenamed('count', 'report_count')
    facility_counts = facilities.groupBy('district_id').count().withColumnRenamed('count', 'registered_facilities')
    reporting = (silver.groupBy('district_id', 'report_date').agg(F.countDistinct('facility_id').alias('reporting_facilities'))
                 .withColumnRenamed('report_date', 'date').join(facility_counts, 'district_id')
                 .withColumn('facility_reporting_fraction', F.col('reporting_facilities') / F.col('registered_facilities'))
                 .select('district_id', 'date', 'facility_reporting_fraction'))
    reference_batches = [m['batch_id'] for m in json.loads((root / 'bronze/manifest.json').read_text())
                         if m['source'] == 'reference' and m['original_name'] in ('districts.csv', 'weather.parquet')]
    refs = F.array(*[F.lit(item) for item in reference_batches]).cast('array<string>')
    daily = (dense.join(grouped, ['district_id', 'disease', 'date'], 'left')
             .join(reports, ['district_id', 'disease', 'date'], 'left')
             .join(reporting, ['district_id', 'date'], 'left')
             .join(weather.select('district_id', 'date', 'rainfall_mm', 'temperature_c', 'humidity_pct'), ['district_id', 'date'], 'left')
             .fillna(0, ['cases', 'admissions', 'deaths', 'report_count'])
             .withColumn('bronze_batch_ids', F.array_union(F.coalesce('bronze_batch_ids', F.array().cast('array<string>')), refs))
             .fillna(0.0, ['facility_reporting_fraction'])
             .withColumn('reporting_maturity', F.least(F.lit(1.0), F.greatest(F.lit(0.0), F.datediff(F.lit(bounds[2]), 'date') / F.lit(21.0))))
             .withColumn('_recent_reporting', F.avg('facility_reporting_fraction').over(Window.partitionBy('district_id', 'disease').orderBy('date').rowsBetween(-28, -1)))
             .withColumn('completeness', F.least(F.lit(1.0), F.col('facility_reporting_fraction') / F.greatest(F.lit(.01), F.coalesce('_recent_reporting', F.col('facility_reporting_fraction')))))
             .drop('_recent_reporting')
             .withColumn('epi_year', F.year(F.date_add(F.to_date(F.date_trunc('week', 'date')), 3))))
    publish(daily.repartition(4, 'epi_year'), root, 'gold', 'daily', ['epi_year'])
    daily = spark.read.parquet(str(root / 'lake/gold/daily'))
    weekly = (daily.withColumn('week_start', F.to_date(F.date_trunc('week', 'date')))
              .groupBy('district_id', 'district_name', 'division', 'population', 'disease', 'week_start')
              .agg(F.sum('cases').alias('cases'), F.sum('admissions').alias('admissions'), F.sum('deaths').alias('deaths'),
                   F.avg('completeness').alias('completeness'), F.sum('rainfall_mm').alias('rainfall_mm'),
                   F.avg('temperature_c').alias('temperature_c'), F.avg('humidity_pct').alias('humidity_pct'),
                   F.array_distinct(F.flatten(F.collect_list('bronze_batch_ids'))).alias('bronze_batch_ids'))
              .withColumn('epi_year', F.year(F.date_add('week_start', 3)))
              .withColumn('epi_week', F.weekofyear('week_start')))
    publish(weekly.repartition(2), root, 'gold', 'weekly', ['epi_year'])
    stockpath = root / 'lake/silver/stocks'
    if stockpath.exists():
        stocks = spark.read.parquet(str(stockpath))
        stocks = stocks.withColumn('_rn', F.row_number().over(Window.partitionBy('facility_id').orderBy(F.desc('date'), F.desc('_loaded_at')))).filter('_rn=1')
        stocks = stocks.join(F.broadcast(facilities.select('facility_id', 'district_id', 'beds', 'icu_beds')), 'facility_id')
        stock = stocks.groupBy('district_id').agg(*[F.sum(c).alias(c) for c in ('iv_saline', 'ns1_kits', 'ors', 'antimalarials', 'occupied_beds', 'beds', 'icu_beds')], F.max('date').alias('as_of'))
        publish(stock.coalesce(1), root, 'gold', 'stock')
    return {'start_date': str(bounds[0]), 'end_date': str(bounds[1]), 'latest_report': str(bounds[2]), 'weekly_rows': weekly.count()}


def run_pipeline(spark, root, drop=None):
    root = Path(root).resolve()
    started = time.monotonic()
    manifest, new = ingest(root, drop)
    signature = hashlib.sha256(json.dumps(sorted((m['source'], m['checksum']) for m in manifest)).encode())
    for path in sorted(Path(__file__).parent.glob('*.py')):
        signature.update(path.read_bytes())
    signature.update(get_salt(root).encode())
    signature = signature.hexdigest()
    state = root / 'lake/processed_state.json'
    processed = json.loads(state.read_text()) if state.exists() else {}
    if processed.get('input_signature') == signature and (root / 'lake/gold/weekly').exists():
        return {'idempotent_noop': True, 'new_batches': 0, 'seconds': time.monotonic() - started}
    facilities = read_facilities(spark, root, manifest)
    if facilities.filter(F.col('facility_id').isNull() | ~F.col('district_id').rlike(r'^(0[1-9]|[1-5][0-9]|6[0-4])$') | (F.col('beds') < 0)).limit(1).count():
        raise ValueError('Facility registry violates its schema; correct the reference source')
    publish(facilities.coalesce(1), root, 'silver', 'facilities')
    raw = read_raw(spark, root, manifest, 'encounters')
    if raw is None:
        raise ValueError('No encounter sources found; run generator or supply --drop')
    cleaned, rules = clean_encounters(raw, facilities, get_salt(root))
    cleaned = cleaned.persist(StorageLevel.DISK_ONLY)
    stats = cleaned.groupBy('_batch_id').agg(F.count('*').alias('rows_in'),
             F.sum((F.size('reason_codes') > 0).cast('long')).alias('quarantined'),
             *[F.sum(F.array_contains('reason_codes', rule).cast('long')).alias(rule) for rule in rules]).collect()
    quarantine = cleaned.filter(F.size('reason_codes') > 0).drop('age_band')
    publish(quarantine.repartition(2), root, 'silver', 'quarantine')
    valid = cleaned.filter(F.size('reason_codes') == 0).drop('reason_codes')
    latest = Window.partitionBy('encounter_id').orderBy(F.desc('_loaded_at'), F.desc('_batch_id'))
    valid = valid.withColumn('_rn', F.row_number().over(latest)).filter('_rn=1').drop('_rn')
    valid = valid.join(F.broadcast(facilities.select('facility_id', 'district_id').dropDuplicates()), ['facility_id', 'district_id'])
    valid = valid.withColumn('epi_year', F.year(F.date_add(F.to_date(F.date_trunc('week', 'onset_date')), 3)))
    publish(valid.repartition(8, 'epi_year'), root, 'silver', 'encounters', ['epi_year'])
    valid_out = spark.read.parquet(str(root / 'lake/silver/encounters')).groupBy('_batch_id').count().collect()
    out_counts = {r['_batch_id']: r['count'] for r in valid_out}
    quality = []
    meta = {m['batch_id']: m for m in manifest if m['source'] == 'encounters'}
    for row in stats:
        row = row.asDict()
        batch = row['_batch_id']
        quality.append({'batch_id': batch, 'source': 'encounters', 'loaded_at': meta[batch]['loaded_at'],
                        'rows_in': row['rows_in'], 'rows_out': out_counts.get(batch, 0),
                        'quarantined': row['quarantined'],
                        'duplicates': row['rows_in'] - row['quarantined'] - out_counts.get(batch, 0),
                        'rule_counts_json': json.dumps({rule: row[rule] for rule in rules}),
                        'rule_pass_rates_json': json.dumps({rule: 1-row[rule]/max(1,row['rows_in']) for rule in rules}),
                        'checksum': meta[batch]['checksum']})
    cleaned.unpersist()
    stocks = read_raw(spark, root, manifest, 'stocks')
    if stocks is not None:
        fields = ('iv_saline', 'ns1_kits', 'ors', 'antimalarials', 'occupied_beds')
        stocks = stocks.withColumn('date', F.to_date('date'))
        for c in fields:
            stocks = stocks.withColumn(c, F.col(c).cast('long'))
        stocks = stocks.join(F.broadcast(facilities.select('facility_id').distinct()), 'facility_id', 'left_semi')
        condition = F.col('date').isNotNull()
        for c in fields:
            condition = condition & F.col(c).isNotNull() & (F.col(c) >= 0)
        bad = stocks.filter(~F.coalesce(condition, F.lit(False))).withColumn('reason_code', F.lit('invalid_stock_snapshot'))
        publish(bad.coalesce(1), root, 'silver', 'stock_quarantine')
        stocks = stocks.filter(condition).withColumn('_rn', F.row_number().over(Window.partitionBy('facility_id', 'date').orderBy(F.desc('_loaded_at')))).filter('_rn=1').drop('_rn')
        publish(stocks.repartition(4).withColumn('year', F.year('date')), root, 'silver', 'stocks', ['year'])
    publish(spark.createDataFrame(quality).coalesce(1), root, 'gold', 'quality')
    result = build_gold(spark, root)
    result.update({'new_batches': len(new), 'seconds': time.monotonic() - started,
                   'rows_in': sum(x['rows_in'] for x in quality), 'rows_out': sum(x['rows_out'] for x in quality),
                   'quarantined': sum(x['quarantined'] for x in quality), 'duplicates': sum(x['duplicates'] for x in quality),
                   'rule_counts': {rule: sum(json.loads(x['rule_counts_json'])[rule] for x in quality) for rule in rules}})
    json_write(root.parent / 'results/quality_latest.json', result)
    json_write(state, {'input_signature':signature, 'processed_at':datetime.now(timezone.utc).isoformat()})
    return result

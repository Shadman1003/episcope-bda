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


def source_contract(path):
    """Identify record contracts from a bounded sample, never from filenames."""
    with open(path, encoding='utf-8-sig', newline='') as stream:
        sample = stream.read(65536)
    if path.suffix.lower() == '.csv':
        fields = set(next(csv.reader(sample.splitlines()), []))
        multiline = False
    else:
        text = sample.lstrip()
        multiline = text.startswith('[')
        if not multiline and text:
            try:
                json.JSONDecoder().raw_decode(text.splitlines()[0])
            except json.JSONDecodeError:
                multiline = True
        if text.startswith('['):
            text = text[1:].lstrip()
        try:
            record, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f'Cannot inspect JSON record contract in {path.name}') from exc
        if not isinstance(record, dict):
            raise ValueError(f'Expected JSON object records in {path.name}')
        fields = set(record)
    if fields & {'encounter_id','patient_id','onset_date','icd10_code'}:
        return 'encounters', multiline
    if fields & {'occupied_beds','iv_saline','ns1_kits','antimalarials'}:
        return 'stocks', multiline
    if fields & {'facility_type','facility_name','icu_beds'}:
        return 'facilities', multiline
    if fields & {'files','encounter_files','stock_files','schema_version','manifest'}:
        return None, multiline  # Nonrecord sidecar metadata, not a patient feed.
    raise ValueError(f'Unrecognised incremental record schema in {path.name}')


def ingest(root, drop=None):
    root = Path(root).resolve()
    manifest_path = root / 'bronze' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    known = {(item['source'], item['checksum']) for item in manifest}
    paths = []
    formats = {}
    incoming = Path(drop).resolve() if drop else root / 'incoming'
    if incoming.is_file():
        candidates = [incoming]
    else:
        candidates = sorted(p for p in incoming.rglob('*') if p.is_file() and p.suffix.lower() in ('.csv', '.json', '.jsonl', '.ndjson'))
    for source_path in candidates:
        source, multiline = source_contract(source_path)
        if source is None:
            continue
        formats[str(source_path)] = multiline
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
        row['json_multiline'] = formats.get(str(source_path), False)
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
            key = (extension, header, item.get('json_multiline',False))
            grouped.setdefault(key, []).append(str(root / item['path']))
    for (extension, _header, multiline), paths in grouped.items():
        if extension == '.csv':
            df = spark.read.option('header', True).option('mode', 'PERMISSIVE').csv(paths)
        else:
            df = spark.read.option('primitivesAsString', True).option('multiLine',multiline).json(paths)
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


def validate_facilities(facilities):
    invalid = F.col('facility_id').isNull() | (F.length(F.trim('facility_id')) == 0)
    invalid = invalid | F.col('district_id').isNull() | ~F.col('district_id').rlike(r'^(0[1-9]|[1-5][0-9]|6[0-4])$')
    invalid = invalid | F.col('beds').isNull() | (F.col('beds') < 0)
    invalid = invalid | F.col('icu_beds').isNull() | (F.col('icu_beds') < 0) | (F.col('icu_beds') > F.col('beds'))
    if facilities.filter(invalid).limit(1).count():
        raise ValueError('Facility registry has missing/invalid identifiers, geography or bed capacities')
    if facilities.groupBy('facility_id').count().filter('count > 1').limit(1).count():
        raise ValueError('Facility registry must have one unambiguous row per facility_id')


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
    return {'start_date': str(bounds[0]), 'end_date': str(bounds[1]), 'latest_report': str(bounds[2]),
            'weekly_rows': spark.read.parquet(str(root / 'lake/gold/weekly')).count()}


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
    validate_facilities(facilities)
    publish(facilities.coalesce(1), root, 'silver', 'facilities')
    raw = read_raw(spark, root, manifest, 'encounters')
    if raw is None:
        raise ValueError('No encounter sources found; run generator or supply --drop')
    cleaned, rules = clean_encounters(raw, facilities, get_salt(root))
    cleaned = cleaned.persist(StorageLevel.DISK_ONLY)
    ranked = None
    try:
        stats = cleaned.groupBy('_batch_id').agg(F.count('*').alias('rows_in'),
                 F.sum((F.size('reason_codes') > 0).cast('long')).alias('quarantined'),
                 *[F.sum(F.array_contains('reason_codes', rule).cast('long')).alias(rule) for rule in rules]).collect()
        valid = cleaned.filter(F.size('reason_codes') == 0).drop('reason_codes')
        latest = Window.partitionBy('encounter_id').orderBy(F.desc('_loaded_at'), F.desc('revision'), F.desc('_payload_hash'), F.desc('_batch_id'))
        ranked = valid.withColumn('_rn', F.row_number().over(latest)).persist(StorageLevel.DISK_ONLY)
        removed = ranked.filter('_rn > 1').drop('_rn').withColumn('reason_codes', F.array(F.lit('duplicate_or_superseded_encounter_id')))
        quarantine = cleaned.filter(F.size('reason_codes') > 0).unionByName(removed)
        # This complete write materialises ranked for both duplicate and retained
        # rows, so the retained publication can reuse the same shuffle/window.
        publish(quarantine.repartition(2), root, 'silver', 'quarantine')
        cleaned.unpersist(blocking=True)
        valid = ranked.filter('_rn=1').drop('_rn')
        valid = valid.join(F.broadcast(facilities.select('facility_id', 'district_id').dropDuplicates()), ['facility_id', 'district_id'])
        valid = valid.withColumn('epi_year', F.year(F.date_add(F.to_date(F.date_trunc('week', 'onset_date')), 3)))
        publish(valid.repartition(8, 'epi_year'), root, 'silver', 'encounters', ['epi_year'])
    finally:
        try:
            if ranked is not None:
                ranked.unpersist(blocking=True)
        finally:
            cleaned.unpersist(blocking=True)
    valid_out = spark.read.parquet(str(root / 'lake/silver/encounters')).groupBy('_batch_id').count().collect()
    out_counts = {r['_batch_id']: r['count'] for r in valid_out}
    quality = []
    meta = {m['batch_id']: m for m in manifest if m['source'] == 'encounters'}
    for row in stats:
        row = row.asDict()
        batch = row['_batch_id']
        duplicates = row['rows_in'] - row['quarantined'] - out_counts.get(batch, 0)
        rule_values = {rule:row[rule] for rule in rules}
        rule_values['duplicate_or_superseded_encounter_id'] = duplicates
        quality.append({'batch_id': batch, 'source': 'encounters', 'loaded_at': meta[batch]['loaded_at'],
                        'rows_in': row['rows_in'], 'rows_out': out_counts.get(batch, 0),
                        'quarantined': row['quarantined'] + duplicates,
                        'duplicates': duplicates,
                        'rule_counts_json': json.dumps(rule_values),
                        'rule_pass_rates_json': json.dumps({rule: 1-value/max(1,row['rows_in']) for rule,value in rule_values.items()}),
                        'checksum': meta[batch]['checksum']})
    stocks = read_raw(spark, root, manifest, 'stocks')
    if stocks is not None:
        fields = ('iv_saline', 'ns1_kits', 'ors', 'antimalarials', 'occupied_beds')
        stocks = stocks.withColumn('date', F.to_date('date'))
        for c in fields:
            stocks = stocks.withColumn(c, F.col(c).cast('long'))
        stocks = stocks.join(F.broadcast(facilities.select('facility_id').distinct().withColumn('_known_facility',F.lit(True))), 'facility_id', 'left')
        condition = F.col('date').isNotNull() & F.coalesce(F.col('_known_facility'),F.lit(False))
        for c in fields:
            condition = condition & F.col(c).isNotNull() & (F.col(c) >= 0)
        stocks = stocks.withColumn('_valid_stock', F.coalesce(condition,F.lit(False))).persist(StorageLevel.DISK_ONLY)
        stock_stats = stocks.groupBy('_batch_id').agg(F.count('*').alias('rows_in'),
                        F.sum((~F.col('_valid_stock')).cast('long')).alias('invalid_stock_snapshot'),
                        F.sum(F.col('_known_facility').isNull().cast('long')).alias('orphan_stock_facility')).collect()
        bad = stocks.filter(~F.coalesce(condition, F.lit(False))).withColumn('reason_code', F.when(F.col('_known_facility').isNull(),F.lit('orphan_stock_facility')).otherwise(F.lit('invalid_stock_snapshot'))).drop('_known_facility')
        publish(bad.coalesce(1), root, 'silver', 'stock_quarantine')
        ranked_stock = stocks.filter('_valid_stock').drop('_known_facility','_valid_stock').withColumn('_rn', F.row_number().over(Window.partitionBy('facility_id', 'date').orderBy(F.desc('_loaded_at'),F.desc('_batch_id'))))
        publish(ranked_stock.filter('_rn > 1').drop('_rn').withColumn('reason_code',F.lit('duplicate_or_superseded_stock_snapshot')).coalesce(1),root,'silver','stock_duplicate_quarantine')
        publish(ranked_stock.filter('_rn=1').drop('_rn').repartition(4).withColumn('year', F.year('date')), root, 'silver', 'stocks', ['year'])
        stock_out = {r['_batch_id']:r['count'] for r in spark.read.parquet(str(root/'lake/silver/stocks')).groupBy('_batch_id').count().collect()}
        stock_meta = {m['batch_id']:m for m in manifest if m['source']=='stocks'}
        for record in stock_stats:
            values=record.asDict(); batch=values['_batch_id']; duplicates=values['rows_in']-values['invalid_stock_snapshot']-stock_out.get(batch,0)
            rule_values={key:values[key] for key in ('invalid_stock_snapshot','orphan_stock_facility')}
            rule_values['duplicate_or_superseded_stock_snapshot']=duplicates
            quality.append({'batch_id':batch,'source':'stocks','loaded_at':stock_meta[batch]['loaded_at'],
                            'rows_in':values['rows_in'],'rows_out':stock_out.get(batch,0),
                            'quarantined':values['invalid_stock_snapshot']+duplicates,'duplicates':duplicates,
                            'rule_counts_json':json.dumps(rule_values),
                            'rule_pass_rates_json':json.dumps({key:1-value/max(1,values['rows_in']) for key,value in rule_values.items()}),
                            'checksum':stock_meta[batch]['checksum']})
        stocks.unpersist()
    publish(spark.createDataFrame(quality).coalesce(1), root, 'gold', 'quality')
    result = build_gold(spark, root)
    result.update({'new_batches': len(new), 'seconds': time.monotonic() - started,
                   'rows_in': sum(x['rows_in'] for x in quality), 'rows_out': sum(x['rows_out'] for x in quality),
                   'quarantined': sum(x['quarantined'] for x in quality), 'duplicates': sum(x['duplicates'] for x in quality),
                   'rule_counts': {rule: sum(json.loads(x['rule_counts_json']).get(rule,0) for x in quality) for rule in rules}})
    json_write(root.parent / 'results/quality_latest.json', result)
    json_write(state, {'input_signature':signature, 'processed_at':datetime.now(timezone.utc).isoformat()})
    return result

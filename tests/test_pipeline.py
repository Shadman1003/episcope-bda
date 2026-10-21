"""Small adversarial fixtures exercise production quality and privacy code."""
import csv
import json
from pathlib import Path
import pytest


@pytest.fixture(scope='module')
def spark(tmp_path_factory):
    pytest.importorskip('pyspark')
    from pipeline.common import make_spark
    session = make_spark(tmp_path_factory.mktemp('spark'), app='EpiScope invariant tests', cores=1)
    session.sparkContext.setLogLevel('ERROR')
    yield session
    session.stop()


def fixture_row(**overrides):
    row = {'encounter_id':'E1','patient_id':'SYNTH123','facility_id':'F1','onset_date':'2025-12-01',
           'report_date':'2025-12-03','age_years':'31','sex':'M','icd10_code':'A90','lab_confirmed':'true',
           'admitted':'false','outcome':'recovered','_batch_id':'a'*20,'_loaded_at':'2026-01-01T00:00:00Z','_source_file':'fixture.csv'}
    row.update(overrides)
    return row


def test_quality_rules_and_deidentification(spark):
    from pipeline.quality import clean_encounters
    rows = [fixture_row(), fixture_row(encounter_id='E2',age_years='212'),
            fixture_row(encounter_id='E3',facility_id='ORPHAN'),
            fixture_row(encounter_id='E4',onset_date='2025-12-10',report_date='2025-12-03'),
            fixture_row(encounter_id='E5',sex='bad',icd10_code='XXX',lab_confirmed='?',admitted='?',outcome='?')]
    refs = spark.createDataFrame([('F1','01')],['facility_id','district_id'])
    result, rules = clean_encounters(spark.createDataFrame(rows), refs, 'x'*64)
    assert len(rules) >= 12
    output = {r['encounter_id']:r.asDict() for r in result.collect()}
    assert output['E1']['reason_codes'] == []
    assert 'invalid_age' in output['E2']['reason_codes']
    assert 'orphan_facility' in output['E3']['reason_codes']
    assert 'onset_after_report' in output['E4']['reason_codes']
    assert {'invalid_sex','invalid_icd10','invalid_lab_flag','invalid_admission_flag','invalid_outcome'} <= set(output['E5']['reason_codes'])
    assert 'patient_id' not in result.columns and 'age_years' not in result.columns
    assert len(output['E1']['patient_hash']) == 64
    other, _ = clean_encounters(spark.createDataFrame([fixture_row()]), refs, 'y'*64)
    assert other.first()['patient_hash'] != output['E1']['patient_hash']


def test_nulls_are_quarantined(spark):
    from pipeline.quality import clean_encounters
    rows = [fixture_row(), fixture_row(encounter_id='E2',onset_date='',patient_id='',report_date='invalid')]
    refs = spark.createDataFrame([('F1','01')],['facility_id','district_id'])
    result,_ = clean_encounters(spark.createDataFrame(rows), refs, 'x'*64)
    bad = result.filter("encounter_id='E2'").first()
    assert {'invalid_onset_date','invalid_report_date','missing_patient_id'} <= set(bad.reason_codes)


def test_ingest_idempotency_and_immutable_bytes(tmp_path):
    from pipeline.run import ingest
    incoming=tmp_path/'incoming/encounters'
    incoming.mkdir(parents=True)
    (tmp_path/'reference').mkdir()
    content='encounter_id,patient_id\nE1,SYNTH123\n'
    (incoming/'day.csv').write_text(content)
    manifest,new=ingest(tmp_path)
    assert len(new)==1
    assert (tmp_path/manifest[0]['path']).read_text()==content
    second,new=ingest(tmp_path)
    assert new==[] and second==manifest


def test_reordered_drop_columns(spark,tmp_path):
    from pipeline.run import ingest,read_raw
    incoming=tmp_path/'incoming/encounters'
    incoming.mkdir(parents=True)
    (tmp_path/'reference').mkdir()
    (incoming/'a.csv').write_text('encounter_id,patient_id,age_years\nE1,SYNTH1,30\n')
    (incoming/'b.csv').write_text('age_years,patient_id,encounter_id\n40,SYNTH2,E2\n')
    manifest,_=ingest(tmp_path)
    rows={r.encounter_id:r.age_years for r in read_raw(spark,tmp_path,manifest,'encounters').collect()}
    assert rows=={'E1':'30','E2':'40'}


def test_registry_rejects_null_capacity_and_ambiguous_keys(spark):
    from pipeline.run import validate_facilities
    columns='facility_id string, district_id string, beds int, icu_beds int'
    valid=spark.createDataFrame([('F1','01',20,2)],columns)
    validate_facilities(valid)
    for rows in ([('F1','01',None,2)],[('F1',None,20,2)],[('F1','01',20,21)],[('F1','01',20,2),('F1','02',20,2)]):
        with pytest.raises(ValueError):
            validate_facilities(spark.createDataFrame(rows,columns))


def test_drop_contracts_allow_pretty_json_arrays_and_sidecar_metadata(tmp_path):
    from pipeline.run import ingest
    incoming=tmp_path/'incoming'
    incoming.mkdir();(tmp_path/'reference').mkdir()
    (incoming/'arbitrary-one.json').write_text(json.dumps([fixture_row()],indent=2))
    (incoming/'arbitrary-two.json').write_text(json.dumps({'facility_id':'F1','date':'2026-01-14','occupied_beds':5,'iv_saline':100},indent=2))
    (incoming/'inline-first-field.json').write_text('{ "encounter_id":"E2",\n "patient_id":"SYNTH2"\n}')
    (incoming/'sidecar.json').write_text(json.dumps({'synthetic':True,'files':{'arbitrary-one.json':'abc'}}))
    manifest,new=ingest(tmp_path)
    assert len(new)==3
    assert {row['source'] for row in manifest}=={'encounters','stocks'}
    assert all(row['json_multiline'] for row in manifest)


def test_gold_records_link_to_immutable_bronze_manifest(spark,tmp_path):
    from pipeline.common import publish
    (tmp_path/'bronze').mkdir()
    batches=[{'batch_id':'a'*20,'source':'encounters','checksum':'a'*64,'path':'bronze/encounters/file.csv'}]
    (tmp_path/'bronze/manifest.json').write_text(json.dumps(batches))
    path=publish(spark.createDataFrame([('01',15)],['district_id','cases']),tmp_path,'gold','lineage_test')
    row=spark.read.parquet(str(path)).first()
    lineage=json.loads((tmp_path/'lake/lineage'/(row.lineage_id+'.json')).read_text())
    assert lineage['bronze_batches']==batches
    assert lineage['granularity']=='conservative run-level dependencies'

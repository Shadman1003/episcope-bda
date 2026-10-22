"""Measure one fresh profile and package only bounded aggregate evidence."""
import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPOSITORY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPOSITORY))
from serving.export import validate_release
from scripts.summarise_evaluation import (build_summary, FORECAST_NUMBERS, DETECTION_NUMBERS,
                                          ALLOCATION_NUMBERS)

MAX_ARTIFACT_BYTES=30_000_000
MAX_FILE_BYTES=10_000_000
EXPECTED_STAGES=('public_references','synthetic_generator','bronze_silver_gold','analytics','dashboard_export')
SAFE_RESULTS=('run_all.json','analytics_latest.json','quality_latest.json','forecast_summary.csv',
              'detection_tradeoff.csv','allocation_scenarios.csv')
CSV_FIELDS={'forecast_summary.csv':FORECAST_NUMBERS|{'model','disease'},
            'detection_tradeoff.csv':DETECTION_NUMBERS|{'method','evaluation_period'},
            'allocation_scenarios.csv':ALLOCATION_NUMBERS}


def sha256(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024): result.update(block)
    return result.hexdigest()


def write_json(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def assert_fresh(root):
    """Cached public inputs are allowed; generated or transformed feeds are not."""
    if (root/'generation_manifest.json').exists() or (root/'lake').exists():
        raise ValueError('A fresh scale run requires no generation manifest or existing lake')
    incoming=root/'incoming'
    if incoming.exists() and any(path.is_file() for path in incoming.rglob('*')):
        raise ValueError('A fresh scale run cannot reuse an existing incoming feed')


def validate_completed(actual,generation,scale,revision):
    if actual.get('status')!='ok' or actual.get('scale')!=scale or generation.get('scale')!=scale:
        raise ValueError('Completed run does not match the requested successful scale')
    if generation.get('synthetic') is not True or actual.get('source_revision')!=revision:
        raise ValueError('Synthetic provenance or exact source revision is missing')
    if actual.get('seed')!=generation.get('seed'):
        raise ValueError('Run and generation seeds differ')
    minimum=math.ceil(25_000_000*scale)
    unique=generation.get('unique_encounters',0)
    submitted=generation.get('encounter_rows',0)
    if unique<minimum or submitted!=unique+generation.get('duplicate_resubmissions',0):
        raise ValueError('Encounter counts do not meet the requested scale or duplicate balance')
    for key,expected in [('generated_unique_encounters',unique),('generated_encounter_rows',submitted),
                         ('facilities',generation.get('facilities')),
                         ('stock_rows',sum(row['rows'] for row in generation.get('stock_files',[])))]:
        if actual.get(key)!=expected:
            raise ValueError(f'Run and generation manifest disagree on {key}')
    stages=actual.get('stages',[])
    for name in EXPECTED_STAGES:
        attempts=[stage for stage in stages if stage.get('stage')==name]
        if not attempts or attempts[-1].get('status')!='ok':
            raise ValueError(f'Required stage did not complete: {name}')
    if set(stage.get('stage') for stage in stages)!=set(EXPECTED_STAGES):
        raise ValueError('Unexpected pipeline stage in full-run evidence')
    for stage in stages:
        seconds=stage.get('seconds')
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0:
            raise ValueError('Stage timing must be finite and nonnegative')
        if stage.get('result',{}).get('idempotent_noop'):
            raise ValueError('A fresh scaling profile cannot use a transformation no-op')


def nationalise_analytics(value):
    """Retain national evaluation diagnostics without an unsuppressed district cell."""
    result=copy.deepcopy(value)
    artefact=result.get('detection',{}).get('artifact',{})
    rows=artefact.get('naive_report_date_comparator',[])
    if rows:
        artefact['nationalised_comparator_districts']=len(rows)
        artefact['naive_report_date_comparator']=[{
            key:sum(row.get(key,0) for row in rows)
            for key in ('naive_report_date_c2_alerts','backfill_day_report_cases')}]
    return result


def reject_record_identifiers(value):
    """Evaluation JSON must stay national; protected dashboard has its own validator."""
    forbidden={'district_id','facility_id','encounter_id','patient_id','patient_name',
               'patient_hash','patient_key','phone','phone_number','email','salt'}
    if isinstance(value,dict):
        if forbidden & set(value):
            raise ValueError('An unexpected record-level field appears in aggregate evidence')
        for item in value.values(): reject_record_identifiers(item)
    elif isinstance(value,list):
        for item in value: reject_record_identifiers(item)


def validate_artifact(output):
    total=0
    files=[]
    secret=os.getenv('EPISCOPE_SALT')
    for path in sorted(output.rglob('*')):
        if path.is_symlink():
            raise ValueError('Evidence cannot include symbolic links')
        if not path.is_file(): continue
        size=path.stat().st_size
        if size>MAX_FILE_BYTES:
            raise ValueError(f'Evidence file exceeds the {MAX_FILE_BYTES}-byte cap: {path.name}')
        if secret and secret.encode() in path.read_bytes():
            raise ValueError('Patient salt unexpectedly appears in an evidence file')
        total+=size
        files.append({'path':path.relative_to(output).as_posix(),'bytes':size,'sha256':sha256(path)})
    if total>MAX_ARTIFACT_BYTES:
        raise ValueError(f'Aggregate evidence exceeds the {MAX_ARTIFACT_BYTES}-byte cap: {total}')
    return total,files


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale',required=True,type=float,choices=[.25,.5,1.])
    parser.add_argument('--output',type=Path,default=Path('ci-public'))
    args=parser.parse_args()
    root=REPOSITORY/'data'
    output=args.output.resolve()
    if output.exists():
        raise ValueError('Evidence output already exists; use a fresh profile/workspace')
    if output.is_relative_to(root):
        raise ValueError('Evidence output must remain outside the generated data root')
    assert_fresh(root)
    cache=REPOSITORY/'resources/public-reference-cache.zip'
    cache_manifest=json.loads(cache.with_suffix('.manifest.json').read_text())
    if sha256(cache)!=cache_manifest['archive_sha256']:
        raise ValueError('Public reference archive checksum does not match its manifest')
    # Verify extracted public files too; measuring another cache is a different experiment.
    for member in cache_manifest['files']:
        source=(root/member['path']).resolve(strict=True)
        if not source.is_relative_to(root) or source.is_symlink() or sha256(source)!=member['sha256']:
            raise ValueError('Extracted public reference differs from the archived cache')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPOSITORY,text=True).strip()
    if os.getenv('GITHUB_SHA') and revision!=os.environ['GITHUB_SHA']:
        raise ValueError('Checked-out revision differs from GitHub workflow provenance')
    started_at=datetime.now(timezone.utc).isoformat()
    started=time.monotonic()
    subprocess.run(['bash','run_wsl.sh','all','--scale',str(args.scale),'--root',str(root)],
                   check=True,cwd=REPOSITORY)
    seconds=time.monotonic()-started
    completed_at=datetime.now(timezone.utc).isoformat()
    results=REPOSITORY/'results'
    actual=json.loads((results/'run_all.json').read_text())
    generation=json.loads((root/'generation_manifest.json').read_text())
    validate_completed(actual,generation,args.scale,revision)
    payload_path=REPOSITORY/'dashboard/data/dashboard.json'
    payload=json.loads(payload_path.read_text())
    validate_release(payload)
    if len(payload.get('districts',[]))!=64 or any(not payload.get(key) for key in
            ('observed','forecasts','signals','allocations','calendar','tradeoff','scenarios')):
        raise ValueError('Protected dashboard release is incomplete')
    dashboard_manifest=json.loads((payload_path.parent/'manifest.json').read_text())
    if dashboard_manifest.get('sha256')!=sha256(payload_path) or dashboard_manifest.get('bytes')!=payload_path.stat().st_size:
        raise ValueError('Dashboard manifest does not identify the exported release')
    export=[stage for stage in actual['stages'] if stage['stage']=='dashboard_export'][-1]['result']
    if export.get('sha256')!=dashboard_manifest['sha256']:
        raise ValueError('Dashboard does not match this pipeline run')
    public_keys=['status','hardware','source_revision','scale','seed','generated_encounter_rows',
                 'generated_unique_encounters','facilities','stock_rows','output_bytes']
    measurement={key:actual.get(key) for key in public_keys}
    measurement.update(started_at=started_at,completed_at=completed_at,total_seconds=seconds,
        orchestrator_seconds=actual['total_seconds'],under_45_minutes=seconds<=2700,
        measurement='fresh generated feed and lake; cached public references',
        timer_scope='child command start through exit, including Python/JVM startup and shutdown; dependency install and reference-cache extraction excluded',
        input_bytes=sum(path.stat().st_size for path in (root/'incoming').rglob('*') if path.is_file()),
        profile=f"github-actions/{os.getenv('GITHUB_RUN_ID','local')}/sf{args.scale}",
        github={key:os.getenv(key) for key in ['GITHUB_RUN_ID','GITHUB_JOB','GITHUB_SHA','RUNNER_OS','RUNNER_ARCH']},
        cache_archive_sha256=cache_manifest['archive_sha256'],
        source_files=[{'path':f'results/{name}','bytes':(results/name).stat().st_size,'sha256':sha256(results/name)}
                      for name in SAFE_RESULTS],
        diagnostic_policy='National aggregates only; district artefact comparators are summed in packaged analytics/run_all copies; source file checksums above identify originals',
        dashboard={'sha256':sha256(payload_path),'bytes':payload_path.stat().st_size},
        stages=[{key:stage[key] for key in ['stage','seconds','attempt','status']} for stage in actual['stages']])
    output.mkdir(parents=True)
    write_json(output/'measurement.json',measurement)
    (output/'results').mkdir()
    for name in SAFE_RESULTS:
        if name=='analytics_latest.json':
            value=nationalise_analytics(json.loads((results/name).read_text()))
        elif name=='run_all.json':
            value=copy.deepcopy(actual)
            for stage in value['stages']:
                # Retry errors can contain an ingestion plan or rejected record.
                stage.pop('error',None)
                if stage['stage']=='analytics' and stage['status']=='ok':
                    stage['result']=nationalise_analytics(stage['result'])
        elif name.endswith('.json'):
            value=json.loads((results/name).read_text())
        else:
            with (results/name).open(encoding='utf-8-sig',newline='') as stream:
                reader=csv.reader(stream)
                fields=next(reader,[])
                if len(fields)!=len(set(fields)) or set(fields)!=CSV_FIELDS[name]:
                    raise ValueError(f'Unexpected columns in national aggregate table: {name}')
            shutil.copy2(results/name,output/'results'/name)
            continue
        reject_record_identifiers(value)
        write_json(output/'results'/name,value)
    (output/'dashboard/data').mkdir(parents=True)
    for name in ['dashboard.json','manifest.json']:
        shutil.copy2(REPOSITORY/'dashboard/data'/name,output/'dashboard/data'/name)
    shutil.copy2(root/'generation_manifest.json',output/'generation_manifest.json')
    shutil.copy2(root/'reference/provenance.json',output/'reference_provenance.json')
    evaluation=build_summary(output/'results',output/'generation_manifest.json')
    write_json(output/'results/evaluation_summary.json',evaluation)
    size,files=validate_artifact(output)
    write_json(output/'artifact_manifest.json',{'schema_version':'1.0','source_revision':revision,
        'scope':'Bounded national diagnostics, public reference provenance and protected dashboard release; no raw clinical or lake files',
        'files':files,'payload_bytes':size,'evaluation_status':evaluation['status']})
    size,_=validate_artifact(output)
    if summary:=os.getenv('GITHUB_STEP_SUMMARY'):
        with open(summary,'a',encoding='utf-8') as stream:
            stream.write(f'### EpiScope SF {args.scale}\n\n'
                         f'- Submitted encounters: {measurement["generated_encounter_rows"]:,}\n'
                         f'- Complete command: {seconds:.3f} s\n'
                         f'- Within 45 minutes: {measurement["under_45_minutes"]}\n'
                         f'- Lake output: {measurement["output_bytes"]:,} bytes\n'
                         f'- Protected dashboard: {payload_path.stat().st_size:,} bytes\n'
                         f'- Aggregate evidence: {size:,} bytes; raw records excluded\n')
    print(json.dumps({'measurement':measurement,'aggregate_artifact_bytes':size},indent=2))


if __name__=='__main__':
    main()

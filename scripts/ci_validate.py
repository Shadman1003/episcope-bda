"""Measure one fresh profile and package only bounded aggregate evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPOSITORY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPOSITORY))
from serving.export import validate_release

MAX_ARTIFACT_BYTES=30_000_000


def sha256(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024): result.update(block)
    return result.hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scale',required=True,type=float,choices=[.25,.5,1.])
    parser.add_argument('--output',type=Path,default=Path('ci-public'))
    args=parser.parse_args()
    root=REPOSITORY/'data'
    output=args.output.resolve()
    if output.exists():
        raise ValueError('Evidence output already exists; use a fresh profile/workspace')
    cache=REPOSITORY/'resources/public-reference-cache.zip'
    cache_manifest=json.loads(cache.with_suffix('.manifest.json').read_text())
    if sha256(cache)!=cache_manifest['archive_sha256']:
        raise ValueError('Public reference archive checksum does not match its manifest')
    started_at=datetime.now(timezone.utc).isoformat()
    started=time.monotonic()
    subprocess.run(['bash','run_wsl.sh','all','--scale',str(args.scale),'--root',str(root)],
                   check=True,cwd=REPOSITORY)
    seconds=time.monotonic()-started
    completed_at=datetime.now(timezone.utc).isoformat()
    results=REPOSITORY/'results'
    actual=json.loads((results/'run_all.json').read_text())
    generation=json.loads((root/'generation_manifest.json').read_text())
    if actual['status']!='ok' or generation['scale']!=args.scale:
        raise ValueError('Completed run does not match the requested scale')
    if any(stage.get('result',{}).get('idempotent_noop') for stage in actual['stages']):
        raise ValueError('A fresh scaling profile cannot use a transformation no-op')
    payload_path=REPOSITORY/'dashboard/data/dashboard.json'
    payload=json.loads(payload_path.read_text())
    validate_release(payload)
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
        dashboard={'sha256':sha256(payload_path),'bytes':payload_path.stat().st_size},
        stages=[{key:stage[key] for key in ['stage','seconds','attempt','status']} for stage in actual['stages']])
    output.mkdir(parents=True)
    (output/'measurement.json').write_text(json.dumps(measurement,indent=2)+'\n')
    safe_results=['run_all.json','analytics_latest.json','quality_latest.json','forecast_summary.csv',
                  'detection_tradeoff.csv','allocation_scenarios.csv']
    (output/'results').mkdir()
    for name in safe_results:
        shutil.copy2(results/name,output/'results'/name)
    (output/'dashboard/data').mkdir(parents=True)
    for name in ['dashboard.json','manifest.json']:
        shutil.copy2(REPOSITORY/'dashboard/data'/name,output/'dashboard/data'/name)
    shutil.copy2(root/'generation_manifest.json',output/'generation_manifest.json')
    shutil.copy2(root/'reference/provenance.json',output/'reference_provenance.json')
    size=sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
    if size>MAX_ARTIFACT_BYTES:
        raise ValueError(f'Aggregate evidence exceeds the {MAX_ARTIFACT_BYTES}-byte cap: {size}')
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

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from pipeline.common import json_write, make_spark

REPOSITORY = Path(__file__).resolve().parents[1]


def stage(name, function, log, retries=0):
    for attempt in range(retries + 1):
        started = time.monotonic()
        print(f'[{datetime.now(timezone.utc).isoformat()}] {name}: start attempt {attempt+1}', flush=True)
        try:
            value = function()
            item = {'stage': name, 'seconds': time.monotonic()-started, 'attempt': attempt+1, 'status': 'ok', 'result': value}
            log.append(item)
            print(f'[{datetime.now(timezone.utc).isoformat()}] {name}: complete in {item["seconds"]:.2f}s', flush=True)
            return value
        except Exception as exc:
            log.append({'stage': name, 'seconds': time.monotonic()-started, 'attempt': attempt+1, 'status': 'failed', 'error': str(exc)})
            if attempt == retries:
                raise
            time.sleep(min(2 ** attempt, 5))


def command(args):
    subprocess.run([sys.executable, '-m', *args], check=True, cwd=REPOSITORY)
    return {'command': 'python -m ' + ' '.join(map(str, args))}


def run(args):
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    output = root.parent / 'results'
    log = []
    start = time.monotonic()
    spark = None
    try:
        if args.action == 'all':
            stage('public_references', lambda: command(['generator.acquire', '--root', str(root)]), log, retries=2)
            generator_args = ['generator.generate', '--root', str(root), '--scale', str(args.scale), '--seed', str(args.seed)]
            if args.regenerate:
                if (root / 'lake').exists():
                    raise ValueError('Use a separate --root for regeneration after ingestion; this preserves the immutable bronze history.')
                generator_args.append('--force')
            stage('synthetic_generator', lambda: command(generator_args), log)
        spark = make_spark(root)
        spark.sparkContext.setLogLevel('WARN')
        if args.action in ('all', 'pipeline', 'incremental'):
            from pipeline.run import run_pipeline
            stage('bronze_silver_gold', lambda: run_pipeline(spark, root, args.drop), log, retries=1)
        if args.action in ('all', 'analytics', 'incremental'):
            from analytics.run import run_analytics
            stage('analytics', lambda: run_analytics(spark, root), log, retries=1)
        if args.action in ('all', 'export', 'incremental'):
            from serving.export import export_dashboard
            stage('dashboard_export', lambda: export_dashboard(spark, root), log, retries=1)
        if args.action == 'optimisations':
            from orchestration.performance import benchmark_optimisations
            stage('spark_optimisations', lambda: benchmark_optimisations(spark, root), log)
        result = {'status': 'ok', 'started_at': datetime.now(timezone.utc).isoformat(), 'total_seconds': time.monotonic()-start,
                  'hardware': hardware(), 'scale': args.scale, 'seed': args.seed, 'root': str(root), 'stages': log,
                  'output_bytes': tree_size(root / 'lake'), 'application_id': spark.sparkContext.applicationId}
        json_write(output / f'run_{args.action}.json', result)
        return result
    except BaseException:
        json_write(output / f'run_{args.action}_failed.json', {'status':'failed','stages':log,'total_seconds':time.monotonic()-start})
        raise
    finally:
        if spark:
            spark.stop()


def hardware():
    import psutil
    return {'platform': platform.platform(), 'python':platform.python_version(), 'logical_cpus':os.cpu_count(),
            'memory_bytes':psutil.virtual_memory().total, 'available_memory_bytes':psutil.virtual_memory().available,
            'spark_master': os.getenv('EPISCOPE_SPARK_MASTER','local[2]'), 'driver_memory':os.getenv('EPISCOPE_DRIVER_MEMORY','1g')}


def tree_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.exists() else 0


def main():
    parser = argparse.ArgumentParser(description='Rebuild EpiScope without changing source code for new data.')
    parser.add_argument('action', choices=['all','pipeline','analytics','export','incremental','optimisations','benchmark','serve','test'])
    parser.add_argument('--root', default=str(REPOSITORY / 'data'))
    parser.add_argument('--scale', type=float, default=1.0)
    parser.add_argument('--seed', type=int, default=4345)
    parser.add_argument('--drop', type=Path)
    parser.add_argument('--regenerate', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.action == 'serve':
        from serving.server import main as serve
        sys.argv = ['serving.server', '--port',str(args.port)]
        serve()
    elif args.action == 'test':
        subprocess.run([sys.executable,'-m','pytest','-q','tests'],cwd=REPOSITORY,check=True)
    elif args.action == 'benchmark':
        from orchestration.performance import run_scaling
        run_scaling(args)
    else:
        if args.action == 'incremental' and not args.drop:
            parser.error('incremental requires --drop PATH')
        print(json.dumps(run(args), indent=2, default=str))


if __name__ == '__main__':
    main()

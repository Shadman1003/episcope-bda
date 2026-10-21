"""Runtime configuration and safe publication of generated tables."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import time


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


def checksum(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def remove_generated(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or root not in path.parents or path.is_symlink():
        raise ValueError(f'Unsafe generated-directory removal: {path}')
    if path.exists():
        shutil.rmtree(path)


def make_spark(root, app='EpiScope', cores=None):
    from pyspark.sql import SparkSession
    root = Path(root).resolve()
    events = root / 'spark-events'
    events.mkdir(parents=True, exist_ok=True)
    scratch = root / 'spark-tmp'
    scratch.mkdir(exist_ok=True)
    os.environ.setdefault('SPARK_LOCAL_IP', '127.0.0.1')
    os.environ.setdefault('PYSPARK_PYTHON', os.sys.executable)
    return (SparkSession.builder.appName(app)
            .master(os.getenv('EPISCOPE_SPARK_MASTER', f'local[{cores or 2}]'))
            .config('spark.driver.memory', os.getenv('EPISCOPE_DRIVER_MEMORY', '1g'))
            .config('spark.sql.shuffle.partitions', os.getenv('EPISCOPE_SHUFFLE_PARTITIONS', '8'))
            .config('spark.default.parallelism', '4')
            .config('spark.sql.adaptive.enabled', 'true')
            .config('spark.sql.adaptive.skewJoin.enabled', 'true')
            .config('spark.sql.session.timeZone', 'UTC')
            .config('spark.sql.parquet.compression.codec', 'snappy')
            .config('spark.sql.files.maxPartitionBytes', '67108864')
            .config('spark.sql.execution.arrow.pyspark.enabled', 'true')
            .config('spark.sql.warehouse.dir', str(root / 'warehouse'))
            .config('spark.local.dir', str(scratch))
            .config('spark.eventLog.enabled', 'true')
            .config('spark.eventLog.dir', str(events))
            .config('spark.ui.retainedJobs', '30')
            .config('spark.ui.retainedStages', '50')
            .config('spark.driver.bindAddress', '127.0.0.1')
            .getOrCreate())


def publish(df, root, layer, table, partitions=()):
    root = Path(root).resolve()
    parent = root / 'lake' / layer
    parent.mkdir(parents=True, exist_ok=True)
    target = parent / table
    stage = parent / ('.' + table + '-stage')
    previous = parent / ('.' + table + '-previous')
    remove_generated(stage, root)
    writer = df.write.mode('overwrite')
    if partitions:
        writer = writer.partitionBy(*partitions)
    writer.parquet(str(stage))
    remove_generated(previous, root)
    if target.exists():
        target.rename(previous)
    stage.rename(target)
    remove_generated(previous, root)
    # Persisted Parquet plus session SQL catalogue, isolated by lake root.
    spark = df.sparkSession
    database = 'episcope_' + hashlib.sha256(str(root).encode()).hexdigest()[:10]
    spark.sql(f'CREATE DATABASE IF NOT EXISTS {database}')
    qualified = f'{database}.{layer}_{table}'
    spark.sql(f'DROP TABLE IF EXISTS {qualified}')
    escaped = str(target).replace("'", "''")
    spark.sql(f"CREATE TABLE {qualified} USING PARQUET LOCATION '{escaped}'")
    spark.read.parquet(str(target)).createOrReplaceTempView(f'{layer}_{table}')
    return target


def get_salt(root):
    """Keep the deployment salt outside the Git working tree."""
    supplied = os.getenv('EPISCOPE_SALT')
    if supplied:
        if len(supplied) < 32:
            raise ValueError('EPISCOPE_SALT must contain at least 32 characters')
        return supplied
    import secrets
    repository = Path(root).resolve().parent
    folder = repository.parent / '.episcope-secrets'
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / 'patient-salt'
    if not path.exists():
        path.write_text(secrets.token_hex(32), encoding='ascii')
        path.chmod(0o600)
    return path.read_text(encoding='ascii').strip()

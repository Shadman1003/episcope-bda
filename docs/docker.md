# Optional Linux Docker environment

**UNTESTED: Docker is unavailable on the implementation host. The image has not been built, Compose has not been run, and container runtime/performance results have not been measured.** The executed validation in `validation.md` applies to Ubuntu WSL. These files provide a portability option for later verification.

## Environment

The Dockerfile selects Python **3.12.12**, Eclipse Temurin OpenJDK **17.0.16+8**, and the exact Python package versions in `requirements.txt`. Java is copied from its version-tagged image into the Python image. Debian support packages are obtained from the configured Bookworm repositories; image tags and OS package repositories are not a byte-for-byte frozen supply chain. A verified release can additionally record resolved image digests.

This runs Spark in local mode, with two worker threads and a 1 GiB driver by default. It uses mounted local Parquet storage. It does not implement HDFS, MinIO, a Spark cluster, or a claim that SF=1 meets the 45-minute target in Docker.

Use a Linux Docker Engine with the Compose plugin. The dashboard service uses host networking because the existing server permits only a loopback bind. Docker Desktop requires its supported host-networking feature to be enabled; this option is not a validated Windows/WSL1 execution path. Docker describes the platform support in its [host networking documentation](https://docs.docker.com/engine/network/drivers/host/).

## Build and run

Run these commands from the repository directory in a Linux shell. Building requires network access for the base images and pinned Python dependencies; the initial `all` run also downloads public reference inputs.

```bash
docker compose build
docker compose run --rm pipeline all --scale 1
docker compose up dashboard
```

Open [the local dashboard](http://127.0.0.1:8765). Stop the foreground dashboard with Ctrl+C. There is no `ports` mapping: host networking makes the application's loopback listener reachable on the Linux host while retaining its loopback restriction.

For an initial smaller experiment, replace `--scale 1` with `--scale 0.01` **before generating this checkout's data**. Once generated, keep the same scale and seed for that data root. Use a separate checkout or the benchmark command for separate profiles; do not relabel the demo as SF=1.

```bash
docker compose run --rm pipeline test
docker compose run --rm pipeline pipeline
docker compose run --rm pipeline benchmark
```

The first `pipeline` after `--rm` is the Compose service name; the second is the CLI action. The benchmark action retains separate measured profiles under `runs/`. It is a substantial computation and has not been executed inside this container.

For a hidden drop stored in `work/hidden_drop` on the host, add a read-only mount to the batch container. Repeating the command exercises the same checksum/idempotency path as WSL:

```bash
docker compose run --rm -v "$PWD/work/hidden_drop:/drop:ro" pipeline incremental --drop /drop
docker compose run --rm -v "$PWD/work/hidden_drop:/drop:ro" pipeline incremental --drop /drop
```

## Persistent paths and secrets

| Host path, relative to this checkout | Container path | Purpose |
|---|---|---|
| `data/` | `/workspace/data` | Public cache, immutable bronze, silver/gold, Spark scratch and events |
| `results/` | `/workspace/results` | Quality, analytics and performance evidence |
| `dashboard/data/` | `/workspace/dashboard/data` | Suppressed aggregate dashboard release |
| `runs/` | `/workspace/runs` | Separate benchmark profiles |
| `../.episcope-secrets/` | `/.episcope-secrets` | Persistent salt outside the repository |

Compose creates missing bind-mount directories. Generated data is excluded from the image build context. The secret mount matches `pipeline.common.get_salt()`: the repository is `/workspace`, so its parent secret folder is `/.episcope-secrets`. For the current host checkout, the corresponding salt is in the parent `Assignment/.episcope-secrets` directory. Keep the salt directory when removing containers or rebuilding images; losing or changing it changes patient hashes.

An optional ignored `.env` file can set the `EPISCOPE_SALT`, `EPISCOPE_DRIVER_MEMORY`, `EPISCOPE_SPARK_MASTER` and `EPISCOPE_SHUFFLE_PARTITIONS` values interpolated by Compose. Copy `.env.example` if needed. A blank salt selects the persistent generated file; an explicit salt must contain at least 32 characters. The `.env` file is excluded from the image and must remain outside Git. Do not paste the output of a resolved Compose configuration into public evidence if it contains a salt.

The default dashboard is a local national development session. Existing bearer-token behavior can be exercised by exporting valid `EPISCOPE_TOKENS` JSON in the host shell and running `docker compose run --rm -e EPISCOPE_TOKENS dashboard`; pass-through is explicit and no token is embedded in these files.

The image intentionally runs as UID 0 for a simple teaching setup. On a native Linux host it can create root-owned files in these mounted directories. It is not a hardened multi-user deployment. Run one batch process per data root and avoid running the WSL pipeline and container pipeline against the same root simultaneously.

## Verification still required

On a Docker-capable Linux host, check `docker compose config --quiet`, build the image, run the tests, execute an `all` run, verify the loopback dashboard and hidden-drop rerun, and record the actual image/runtime versions and stage timings. The commands above are instructions, not evidence that these checks have passed.

Base image references: [Python 3.12.12 Bookworm image](https://hub.docker.com/layers/library/python/3.12.12-slim-bookworm/images/sha256-2986c55feb36e6cae00fa1fefb454283e4b33f35e75ff8bdd123b134130be301) and [Temurin 17.0.16+8 JRE image](https://hub.docker.com/layers/library/eclipse-temurin/17.0.16_8-jre-jammy/images/sha256-c761ce0db175aaf0c9774e09c932a6c4328cfad25bb24a2789e177759279b2a3).

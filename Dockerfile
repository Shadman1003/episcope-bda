# Optional Linux container; see docs/docker.md for its untested status.
FROM eclipse-temurin:17.0.16_8-jre-jammy AS java

FROM python:3.12.12-slim-bookworm

COPY --from=java /opt/java/openjdk/ /opt/java/openjdk/
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/java/openjdk/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYSPARK_PYTHON=/usr/local/bin/python \
    SPARK_LOCAL_IP=127.0.0.1 \
    OPENBLAS_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash procps libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --requirement requirements.txt \
    && python --version \
    && java -version

COPY . .
RUN mkdir -p data results dashboard/data runs /.episcope-secrets

ENTRYPOINT ["python", "-m", "orchestration.cli"]
CMD ["all", "--scale", "1"]

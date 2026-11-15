FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY object-storage/client/run_state.py /app/
COPY clickhouse/s3-tiered-storage/app/*.py /app/
ENTRYPOINT ["python", "/app/workload.py"]

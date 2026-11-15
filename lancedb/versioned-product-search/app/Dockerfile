FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 RAYON_NUM_THREADS=2
WORKDIR /app
COPY datasets/esci/client/requirements.txt /tmp/common.txt
COPY lancedb/versioned-product-search/app/requirements.txt /tmp/engine.txt
RUN pip install --no-cache-dir -r /tmp/common.txt -r /tmp/engine.txt
COPY datasets/esci/client/workload.py /app/workload.py
COPY lancedb/versioned-product-search/app/ /app/
ENTRYPOINT ["python", "/app/client.py"]

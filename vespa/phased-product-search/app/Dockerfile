FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2
WORKDIR /app
COPY datasets/esci/client/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY datasets/esci/client/workload.py /app/workload.py
COPY vespa/phased-product-search/app/client.py /app/client.py
ENTRYPOINT ["python", "/app/client.py"]

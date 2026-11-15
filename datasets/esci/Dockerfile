FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 TOKENIZERS_PARALLELISM=false
WORKDIR /work
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY manifest.json /work/manifest.json
COPY app /work/app
ENTRYPOINT ["python", "/work/app/prepare.py"]

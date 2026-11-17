FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY *.py /app/
ENTRYPOINT ["python", "/app/client.py"]

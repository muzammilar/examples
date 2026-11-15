FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 AWS_EC2_METADATA_DISABLED=true
WORKDIR /app
COPY object-storage/client/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY object-storage/client/*.py /app/
ENTRYPOINT ["python", "/app/workload.py"]

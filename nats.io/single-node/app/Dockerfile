FROM python:3.13-slim
RUN pip install --no-cache-dir nats-py==2.16.0
WORKDIR /app
COPY *.py ./
# unbuffered so output interleaves in order with `make app` headers
ENV PYTHONUNBUFFERED=1

FROM python:3.13-slim
# the client version should match the cluster's (clients may be older, not newer)
ARG TIGERBEETLE_VERSION
RUN pip install --no-cache-dir "tigerbeetle==${TIGERBEETLE_VERSION}"
COPY demo.py /demo.py
CMD ["python", "-u", "/demo.py"]

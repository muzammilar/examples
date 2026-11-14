FROM python:3.12.11-slim-bookworm
RUN useradd --uid 10001 --create-home app
WORKDIR /app
COPY runner.py history.py ./
USER app
ENTRYPOINT ["python", "-u", "runner.py"]

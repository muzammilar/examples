FROM debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc
RUN apt-get update && apt-get install -y --no-install-recommends iptables \
 && rm -rf /var/lib/apt/lists/*
LABEL org.examples.owner="iceberg-multi-system"
STOPSIGNAL SIGTERM
ENTRYPOINT ["sleep", "infinity"]

# syntax=docker/dockerfile:1

# Both stages use the same OTP tag: they share a Debian release, and the
# bundled ERTS finds the libraries it was built against.
ARG OTP_VERSION=27.3

FROM erlang:${OTP_VERSION} AS build
WORKDIR /src
# Resolve dependencies first so this layer is cached across source edits.
COPY rebar.config rebar.lock ./
RUN rebar3 get-deps
COPY config config
COPY src src
RUN rebar3 as prod release

FROM erlang:${OTP_VERSION}-slim
RUN useradd --system --create-home --uid 10001 orchestrator
COPY --from=build --chown=orchestrator /src/_build/prod/rel/orchestrator /opt/orchestrator
USER 10001
ENV PORT=5555 \
    WORKERS=4 \
    MAX_QUEUE=100 \
    JOB_TIMEOUT_MS=5000 \
    NODE_NAME=orchestrator \
    PEERS=
# 5555 for clients; 4369 (epmd) and the distribution port only matter
# between nodes, inside the compose network.
EXPOSE 5555
HEALTHCHECK --interval=10s --timeout=3s CMD ["/opt/orchestrator/bin/orchestrator", "ping"]
ENTRYPOINT ["/opt/orchestrator/bin/orchestrator"]
CMD ["foreground"]

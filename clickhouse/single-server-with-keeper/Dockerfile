ARG CLICKHOUSE_VERSION

###############################################
# add custom runtime configs to clickhouse
###############################################
FROM clickhouse/clickhouse-server:${CLICKHOUSE_VERSION} AS chbuilder
ADD ./configs /etc/clickhouse-server/config.d

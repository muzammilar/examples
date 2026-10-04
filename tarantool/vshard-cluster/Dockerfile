# tarantool/tarantool:3.8.1 does not ship vshard, and `tt rocks install vshard` needs git
# (the rockspec source is git+https). vshard is pure Lua, so copy its module from the release
# tarball onto Tarantool's default module path.
ARG TARANTOOL_VERSION=3.8.1
FROM tarantool/tarantool:${TARANTOOL_VERSION}
ARG VSHARD_VERSION=0.1.42
ADD https://github.com/tarantool/vshard/archive/refs/tags/${VSHARD_VERSION}.tar.gz /tmp/vshard.tar.gz
RUN mkdir -p /usr/local/share/tarantool \
    && tar -xzf /tmp/vshard.tar.gz -C /tmp \
    && cp -r /tmp/vshard-${VSHARD_VERSION}/vshard /usr/local/share/tarantool/vshard \
    && find /usr/local/share/tarantool/vshard -name CMakeLists.txt -delete \
    && rm -rf /tmp/vshard.tar.gz /tmp/vshard-${VSHARD_VERSION} \
    && tarantool -e "require('vshard') os.exit(0)"  # fails the build if the module does not load

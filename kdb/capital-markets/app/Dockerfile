# The client: main.cpp linked against KX's C API (k.h + c.o for linux arm64 or amd64)
# from github.com/KxSystems/kdb at a pinned commit. The C API needs no license.
FROM debian:trixie-slim AS build
ARG TARGETARCH
ARG KX_C_API_COMMIT=3af0d471926df9e04af19bb5b470174d234a4fef
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl g++ \
	&& rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN set -eu; \
	case "$TARGETARCH" in \
	arm64) d=l64arm sum=ac17d1a79bd56e8ad69bf3fbd4f1e4e49d3d3805f6bcd364899ce659228a90fc ;; \
	amd64) d=l64 sum=c96c0735d288331234412a8c4c813886de6978b79abfe836b0b1d55ada44205b ;; \
	*) echo "unsupported arch $TARGETARCH" >&2; exit 1 ;; \
	esac; \
	base="https://raw.githubusercontent.com/KxSystems/kdb/$KX_C_API_COMMIT"; \
	curl -fsSL -o k.h "$base/c/c/k.h"; curl -fsSL -o c.o "$base/$d/c.o"; \
	printf '%s  k.h\n%s  c.o\n' 7da364e03c053942ab35ea52da28c6fe75dda69aba6dc25fbd7503e42a72209a "$sum" | sha256sum -c -
COPY main.cpp .
RUN g++ -O2 -std=c++20 -Wall -Wextra -I. main.cpp c.o -lpthread -o capital-markets

FROM debian:trixie-slim
COPY --from=build /build/capital-markets /usr/local/bin/capital-markets
CMD ["capital-markets"]

FROM golang:1.27.4-bookworm@sha256:ce59b41415e2524ae99926fe1f37ce48ba8c194dcbd2a56a65436cdcefa2bd34 AS build
ENV GOMAXPROCS=2
WORKDIR /src
COPY go.mod go.sum ./
RUN --mount=type=cache,id=obs-platform-gomod,target=/go/pkg/mod go mod download
COPY *.go ./
RUN sha256sum *.go go.mod go.sum > /source.sha256
RUN --mount=type=cache,id=obs-platform-gomod,target=/go/pkg/mod --mount=type=cache,id=obs-platform-gobuild,target=/root/.cache/go-build go vet ./... && go test ./... && CGO_ENABLED=0 go build -trimpath -o /incident .
FROM scratch
COPY --from=build /incident /incident
COPY --from=build /source.sha256 /source.sha256
ENTRYPOINT ["/incident"]

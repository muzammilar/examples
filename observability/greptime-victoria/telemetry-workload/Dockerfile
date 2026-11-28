FROM golang:1.27.4-bookworm@sha256:ce59b41415e2524ae99926fe1f37ce48ba8c194dcbd2a56a65436cdcefa2bd34 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go *.yaml ./
RUN sha256sum *.go *.yaml go.mod go.sum > /source.sha256
RUN go vet ./... && go test ./... && CGO_ENABLED=0 go build -trimpath -o /telemetry .
FROM scratch
COPY --from=build /telemetry /telemetry
COPY --from=build /source.sha256 /source.sha256
USER 65532:65532
ENTRYPOINT ["/telemetry"]

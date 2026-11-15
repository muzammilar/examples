FROM golang:1.27.4-bookworm AS build
WORKDIR /src
COPY go.mod *.go ./
RUN CGO_ENABLED=0 go build -trimpath -o /out/lab .
FROM debian:bookworm-slim
COPY --from=build /out/lab /usr/local/bin/lab
USER 10001
ENTRYPOINT ["lab"]

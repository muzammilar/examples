FROM golang:1.25.4-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY main.go .
RUN gofmt -w main.go && go vet -mod=readonly ./... \
 && CGO_ENABLED=0 go build -mod=readonly -trimpath -o /workload .

FROM postgres:17.6-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
 && rm -rf /var/lib/apt/lists/* \
 && python3 -m venv /opt/client
ENV PATH="/opt/client/bin:$PATH"
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY --from=build /workload /usr/local/bin/workload
ENTRYPOINT ["sh", "/app/entrypoint.sh"]

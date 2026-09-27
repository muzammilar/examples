ARG GO_VERSION=1.23
#build stage
FROM golang:${GO_VERSION}-alpine AS base
# gcc needs musl-dev on alpine, alternatively use libc6-compat (not preferred)
RUN apk add --no-cache --update musl-dev
RUN apk add --no-cache --update gcc
# install make and git
RUN apk add --no-cache --update git make
WORKDIR /go/src/app
COPY . .
# Run go mod init on the first build
RUN go mod init github.com/muzammilar/sqlcstudents
# mostly for container cache and performance
RUN make modtidy
# mostly for container cache and performance
RUN make goget
# RUN make test # Tests don't need to be run at this stage (for now)
# RUN go install -v ./... # We don't install any package (for now)
CMD make

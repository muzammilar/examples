SHELL := /bin/sh
DOCUMENTS ?= 100000
QUERIES ?= 1000
SEED ?= 20261115
IMAGE := esci-corpus:dev
.NOTPARALLEL:
.PHONY: build download prepare clean-image
build:
	docker build -t $(IMAGE) .
download:
	python3 app/download.py
prepare: build
	@mkdir -p cache prepared
	docker run --rm --cpus=4 --memory=8g --user $(shell id -u):$(shell id -g) -e HOME=/cache -e DOCUMENTS=$(DOCUMENTS) -e QUERIES=$(QUERIES) -e SEED=$(SEED) -v "$(CURDIR)/cache:/cache" -v "$(CURDIR)/prepared:/prepared" $(IMAGE)
clean-image:
	@if docker image inspect $(IMAGE) >/dev/null 2>&1; then docker image rm $(IMAGE); fi

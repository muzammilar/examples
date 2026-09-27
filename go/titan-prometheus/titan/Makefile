# tool macros
GO:=go
GOTEST:=$(GO) test
GOFORMAT:=GOFORMAT

# version macros
DATE := $(shell /bin/date -u +"%Y-%m-%d~%H:%M:%S")
COMMIT := $(shell git describe --always)

# install path macros
INSTALL_PREFIX := /go
DIST_PATH := dist

# src code path macros
SRC_PATH := cmd/titan
SRC_NAME := titan.go
SRC := $(SRC_PATH)/$(SRC_NAME)

# target macros
TARGET_NAME := titan
BIN_PATH := bin
TARGET := $(BIN_PATH)/$(TARGET_NAME)

# clean files list
DIST_CLEAN_LIST := $(DIST_PATH)
CLEAN_LIST := $(TARGET) \
			  $(DIST_CLEAN_LIST)

# default goal for Makefile
.PHONY: all makedir format lint-deps lint test mod-init mod-deps clean distclean install uninstall
all: clean lint test makedir $(TARGET)

# non-phony targets
$(TARGET):
	$(GO) build -ldflags "-X main.date=$(DATE) -X main.commit=$(COMMIT)" -o $(TARGET) $(SRC)

# phony rules
makedir:
	@mkdir -p $(BIN_PATH) $(DIST_PATH)

format:
	$(GOFORMAT) ./...

lint-deps:
	$(GO) install honnef.co/go/tools/cmd/staticcheck@latest

lint:
	$(GO) vet ./...

test:
	$(GOTEST) ./...
	$(GOTEST) -bench=. ./...

mod-init:
	$(GO) mod init github.com/muzammilar/examples-go/titan-prometheus/titan

mod-deps:
	$(GO) get -d -v ./...

clean:
	@echo CLEAN $(CLEAN_LIST)
	@rm -f $(CLEAN_LIST)

distclean:
	@echo CLEAN $(DIST_CLEAN_LIST)
	@rm -f $(DIST_CLEAN_LIST)

# usually used for installing packages
install: $(TARGET)
	install -v -d $(DESTDIR)$(INSTALL_PREFIX)/bin
	install -v $(TARGET) $(DESTDIR)$(INSTALL_PREFIX)/bin

uninstall:
	-rm -rf $(DESTDIR)$(INSTALL_PREFIX)/bin

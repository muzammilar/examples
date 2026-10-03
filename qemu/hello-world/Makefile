.PHONY: build run clean

# guest architecture: x86_64 (default; emulated with TCG everywhere) or aarch64
# (QEMU's virt machine; much faster to emulate on Apple silicon)
ARCH ?= x86_64
IMAGE ?= qemu-hello-world:$(ARCH)
# extra kernel command line; KERNEL_ARGS= shows the full boot log
KERNEL_ARGS ?= quiet

build:
	docker build --build-arg ARCH=$(ARCH) -t $(IMAGE) .

# boot the VM; /init prints Hello World and powers off, so QEMU (and the container) exit
run: build
	docker run --rm -e KERNEL_ARGS='$(KERNEL_ARGS)' $(IMAGE)

clean:
	-docker rmi qemu-hello-world:x86_64 qemu-hello-world:aarch64 2>/dev/null

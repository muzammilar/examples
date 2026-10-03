# QEMU — Hello World

Boot a Linux kernel in [QEMU](https://www.qemu.org/docs/master/) whose only process, PID 1, is a
tiny C program that prints "Hello World" and powers the VM off. QEMU runs in Docker, so the host
needs only Docker.

## Quick start

```bash
make run                 # build the image, boot an x86_64 VM, print Hello World, exit
make run ARCH=aarch64    # same with an arm64 guest (QEMU virt machine): ~2x faster on Apple silicon
make run KERNEL_ARGS=    # full kernel boot log instead of `quiet`
make clean               # remove the images
```

## What it does

[`Dockerfile`](Dockerfile), build stage (Debian 13 trixie):

- Compiles [`main.c`](main.c) **statically** (`-static`) for the guest `ARCH`, with a cross
  compiler when the build host is a different architecture. The VM has no root filesystem, libc or
  dynamic loader, so a dynamically linked binary could not run.
- Packs it as `/init` into an initramfs (`cpio -o -H newc`, gzip): 0.3 MB.
- Downloads a pinned kernel, the Debian 13 cloud kernel `6.12.111+deb13`
  (`linux-image-6.12.111+deb13-cloud-{amd64,arm64}`), from snapshot.debian.org, whose URLs are
  permanent, checks its sha256 and extracts `/boot/vmlinuz`. The cloud flavour has the serial
  console, initramfs support and ACPI/PSCI power-off built in.

Runtime stage: `qemu-system-x86` or `qemu-system-arm` (QEMU 10.0 from Debian) plus
[`hello.sh`](hello.sh), which boots:

```bash
qemu-system-x86_64 -M q35 -accel tcg -m 256M -smp 1 \
  -kernel /boot/vmlinuz -initrd /boot/initramfs.cpio.gz \
  -append "console=ttyS0 rdinit=/init panic=-1 quiet" \
  -display none -serial stdio -no-reboot -nic none
# ARCH=aarch64: qemu-system-aarch64 -M virt -cpu max ... console=ttyAMA0
```

- No disk image: the kernel unpacks the initramfs into a RAM filesystem and runs `rdinit=/init`
  as PID 1. That needs no `qemu-img`, `mkfs`, `guestmount`/libguestfs or sudo.
- `/init` prints Hello World, then the kernel version and how long the kernel took to get there
  (`CLOCK_BOOTTIME`). PID 1 must never exit (the kernel panics if it does), so it calls
  `sync()` and `reboot(RB_POWER_OFF)`. QEMU exits and the container goes away.
- `-display none -serial stdio` puts the guest's serial console on stdout. `-nographic` would too,
  but then SeaBIOS writes its banner and terminal escape codes there as well.
- `panic=-1` plus `-no-reboot`: if the kernel panics anyway, QEMU exits instead of hanging.
- `-accel tcg` is pure emulation, so the same command runs on any host. QEMU 5.2 removed
  `-no-kvm`; use `-accel tcg`. On a Linux host of the guest's architecture,
  `docker run --rm --device /dev/kvm -e ACCEL=kvm qemu-hello-world:x86_64` should use hardware
  virtualization (not tested here: no `/dev/kvm` on macOS).

`hello.sh` takes `ARCH`, `ACCEL`, `MEM`, `SMP` and `KERNEL_ARGS` from the environment.

## Sample output

2026-10-03, Docker Desktop 29.5.3 on an Apple M4 (arm64 Docker VM), QEMU 10.0.13, TCG.

`make run`:

```
+ qemu-system-x86_64 -M q35 -append console=ttyS0 rdinit=/init panic=-1 quiet -accel tcg -m 256M -smp 1 -kernel /boot/vmlinuz -initrd /boot/initramfs.cpio.gz -display none -serial stdio -no-reboot -nic none
Hello World
pid 1, Linux 6.12.111+deb13-cloud-amd64 x86_64, 1.532 s after the kernel started
[    1.560725] reboot: Power down
QEMU exited after 2412 ms (QEMU emulator version 10.0.13 (Debian 1:10.0.13+ds-0+deb13u1))
```

`make run ARCH=aarch64`:

```
+ qemu-system-aarch64 -M virt -cpu max -append console=ttyAMA0 rdinit=/init panic=-1 quiet -accel tcg -m 256M -smp 1 -kernel /boot/vmlinuz -initrd /boot/initramfs.cpio.gz -display none -serial stdio -no-reboot -nic none
Hello World
pid 1, Linux 6.12.111+deb13-cloud-arm64 aarch64, 0.925 s after the kernel started
[    1.008310] reboot: Power down
QEMU exited after 1206 ms (QEMU emulator version 10.0.13 (Debian 1:10.0.13+ds-0+deb13u1))
```

Over five runs each:

| guest | kernel start to Hello World | QEMU start to exit | `docker run` total |
|-------|----------------------------:|-------------------:|-------------------:|
| x86_64 (`-M q35`, emulated) | 1.48-1.54 s | 2.31-2.38 s | ~2.6 s |
| aarch64 (`-M virt -cpu max`) | 0.85-0.90 s | 1.13-1.17 s | ~1.5 s |

The first `make run` takes 1 to 2 minutes, mostly apt and the 28-35 MB kernel download from
snapshot.debian.org. After that the image is cached.

## Known issues

- No KVM on macOS: Docker Desktop's Linux VM exposes no `/dev/kvm`, so the guest is emulated
  (TCG). This is why the x86_64 guest is slower than aarch64 on Apple silicon.
- The kernel is pinned to a security update on snapshot.debian.org. If that archive is slow or
  down, the build fails at the download step; the sha256 check guards against a changed file.
- The VM has no `/proc`, `/sys`, `/dev` mounts or devices beyond `/dev/console`, which comes from
  the kernel's built-in initramfs. A real init would mount them first.

## Useful resources

- [Kernel docs: ramfs, rootfs and initramfs](https://docs.kernel.org/filesystems/ramfs-rootfs-initramfs.html)
- [QEMU removed features](https://www.qemu.org/docs/master/about/removed-features.html) (`-no-kvm`)
- [QEMU `virt` machine](https://www.qemu.org/docs/master/system/arm/virt.html)

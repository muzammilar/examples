#!/usr/bin/env sh
# Boot /boot/vmlinuz with /boot/initramfs.cpio.gz in QEMU; /init prints Hello World and powers off.
# Runs inside the container built from the Dockerfile (see the Makefile).
set -eu

ARCH="${ARCH:-x86_64}"
ACCEL="${ACCEL:-tcg}"        # tcg = pure emulation; kvm needs /dev/kvm and ARCH = host arch
MEM="${MEM:-256M}"
SMP="${SMP:-1}"
# -display none -serial stdio: guest console on stdout (unlike -nographic, SeaBIOS stays quiet)
# rdinit=/init: run /init from the initramfs; panic=-1 + -no-reboot: exit QEMU on a kernel panic
KERNEL_ARGS="${KERNEL_ARGS-quiet}"  # empty: full boot log

case "$ARCH" in
  x86_64)  set -- qemu-system-x86_64 -M q35 -append "console=ttyS0 rdinit=/init panic=-1 $KERNEL_ARGS" ;;
  aarch64) set -- qemu-system-aarch64 -M virt -cpu max -append "console=ttyAMA0 rdinit=/init panic=-1 $KERNEL_ARGS" ;;
  *) echo "unsupported ARCH=$ARCH (x86_64 or aarch64)" >&2; exit 1 ;;
esac

set -- "$@" -accel "$ACCEL" -m "$MEM" -smp "$SMP" \
  -kernel /boot/vmlinuz -initrd /boot/initramfs.cpio.gz \
  -display none -serial stdio -no-reboot -nic none

echo "+ $*"
start=$(date +%s%N)
"$@"
end=$(date +%s%N)
echo "QEMU exited after $(( (end - start) / 1000000 )) ms ($($1 --version | head -1))"

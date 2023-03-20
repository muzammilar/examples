#!/usr/bin/env sh

# Exit on non-zero status. See: https://www.gnu.org/savannah-checkouts/gnu/bash/manual/bash.html#The-Set-Builtin
set -xe

## Variables
# QH prefix is for QEMU Hello (World)
QH_DIR=$(pwd)  # current directory
QH_LINUX_DIR="$QH_DIR/linuximage"
QH_LINUX_BZIMG="${QH_LINUX_DIR}/boot/vmlinuz-6.2.7-060207-generic"
QH_KERNEL_IMG_URL='https://kernel.ubuntu.com/~kernel-ppa/mainline/v6.2.7/amd64/linux-image-unsigned-6.2.7-060207-generic_6.2.7-060207.202303170542_amd64.deb'
#QH_KERNEL_VERSION='6.2.7'
# Disk: https://www.qemu.org/docs/master/system/images.html
QH_IMG_NAME='qemu_hello_world.img'
QH_DISK_SIZE='6G' # 6 GB disk space
QH_DISK_FORMAT='raw'
# Memory and CPU
QH_MEM_SIZE='2G'
QH_SMP_SIZE=2 # multiprocessing

## Download the linux kernel and build it; --no-clobber allows to skip the download again
# Skip hash check for now (todo: simpler way to get the kernel)
wget --no-clobber --retry-connrefused --waitretry=1 --read-timeout=20 --tries=5 $QH_KERNEL_IMG_URL
dpkg -x linux-image-unsigned-*.deb "${QH_LINUX_DIR}"

#rm -rf ${QH_DEST_FILE}

## Create a disk image
echo "Creating an image: $QH_IMG_NAME"
# Remove image if it already exists
rm -rf ${QH_IMG_NAME} || echo "$QH_IMG_NAME does not exist"
# Create the image and then the filesystem (skip partition tables for now)
qemu-img create ${QH_IMG_NAME} ${QH_DISK_SIZE} -f ${QH_DISK_FORMAT}
mkfs.ext4 -F ${QH_IMG_NAME}


## Compile an init program
gcc main.c -o initprog
# copy the init program to the disk without copying the OS
# mkdir -p "${QH_DIR}/fakemount"
# guestmount -a "${QH_IMG_NAME}" -i "${QH_DIR}/fakemount"
# cp initprog "${QH_DIR}/fakemount"
# guestunmount "${QH_DIR}/fakemount"

## Boot the Image (and start the image)
qemu-system-x86_64 \
        -m ${QH_MEM_SIZE} \
        -smp ${QH_SMP_SIZE} \
        -kernel "${QH_LINUX_BZIMG}" \
        -append "console=ttyS0 root=/dev/sda init=/initprog" \
        -drive "file=${QH_IMG_NAME},format=raw" \
        -no-kvm \
        -nographic \
        -pidfile vm.pid \
        2>&1 | tee vm.log

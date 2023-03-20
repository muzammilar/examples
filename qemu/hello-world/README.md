# Hello World

A simple "Hello World" example using [QEMU](https://www.qemu.org/docs/master/).

### Installation

#### Ubunutu 20.04

```sh
sudo apt install -y qemu qemu-utils qemu-system-x86 # utils for qemu-img and qemu-system-x86 for qemu-system-x86_64
sudo apt install -y xz-utils # in case tar is not installed already
sudo apt install -y  libguestfs-tools # virt-copy-in or guestmount
```

#### Development

```sh
./hello.sh
```

### Documentation

#### Known Issues

According to [this](https://askubuntu.com/questions/1046828/how-to-run-libguestfs-tools-tools-such-as-virt-make-fs-without-sudo) and [this](https://bugs.launchpad.net/ubuntu/+source/linux/+bug/759725), the kernel image should be readable before `guestmount` or `virt-copy-in` can be used.
```
sudo chmod +r /boot/vmlinuz-*
```

#### Current State
While the system is able to load an empty filesystem, it fails to run an `init` program.

#### Useful Resource

* https://github.com/google/syzkaller/blob/master/docs/linux/setup_ubuntu-host_qemu-vm_x86-64-kernel.md
* https://vccolombo.github.io/cybersecurity/linux-kernel-qemu-setup/
* https://www.codingame.com/playgrounds/84444/running-u-boot-linux-kernel-in-qemu/preparing-bootable-linux-kernel-storage-media

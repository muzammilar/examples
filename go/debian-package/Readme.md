# Simple Debian Packaging

A basic example of debian packaging with dh-sysuser (to create a user) and dh-systemd (to run a daemon using systemd).

```sh
# add build dependencies
apt-get install -y build-essential fakeroot debhelper dh-systemd dh-sysuser

# make the debian
make deb

# install the deb on the target machine
```

Note: This repo is a work-in-progress and mostly untested for typos.

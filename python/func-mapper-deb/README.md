# Simple Debian Packaging

A basic example of debian packaging with dh-sysuser (to create a user).

```sh
# Use docker to build container (it contains all build dependencies)
# (for ease of use, it's a make command shortcut)
make buildcontainer

# make the debian
make deb

# check the contents of the debian
dpkg --contents ../*.deb
dpkg -I ../*.deb

# install the deb on the target machine (example)
apt install -y ../periodicprinterpkg_0.0.1-0_amd64.deb
```

## Note for Windows Docker users
Please make sure that you are using `LF` line ending and not `CRLF` for *all* files in `debian/` directory, otherwise, you will run into errors.

Example error (or `postinst` failures):
```
: No such file or directory
cc      -o .o
cc: fatal error: no input files
```

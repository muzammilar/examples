## Base Build Image
# Build on Ubuntu 20.04
FROM ubuntu:20.04 AS meson-ubuntu-2004
# Install g++/gcc (and related build essestials) using a single package
RUN apt update
RUN apt install -y build-essential
# Install meson build system
RUN apt-get install -y python3 python3-pip python3-setuptools python3-wheel ninja-build
RUN pip3 install meson
# Label the image
LABEL Name=meson-ubuntu-focal Version=0.0.1


## Build your code
FROM meson-ubuntu-2004 AS build-evens-odds
# These commands copy your files into the specified directory in the image
# and set that as the working location
COPY . /usr/src/myapp
WORKDIR /usr/src/myapp
# Check if gtest actually exist. `meson wrap search gtest` is good for search but doesn't return error on failure.
RUN meson wrap info gtest
# Add gtest support
RUN mkdir -p subprojects
RUN meson wrap install gtest
# This command sets up the builddir for meson (or use `meson --reconfigure builddir` if gtest is added afterwards)
RUN meson setup builddir
# This command compiles the code with meson
RUN meson compile -C builddir
# This command tests the code with meson
RUN meson test -C builddir
# This command runs your application, comment out this line to compile only
# CMD ["./builddir/oddchecker"]
# Label the image
LABEL Name=buildimg-evens-odds Version=0.0.1

## Run your code
FROM ubuntu:20.04
COPY --from=build-evens-odds /usr/src/myapp/builddir/src/oddnumbers /oddnumbers
ENTRYPOINT ./oddnumbers
LABEL Name=even-odd-numbers Version=0.0.1

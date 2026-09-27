## Base Your Code
FROM gcc:latest AS gcc-image
# and set that as the working location
COPY . /usr/src/myapp
WORKDIR /usr/src/myapp
# compile the code
RUN gcc -Wall -o map map.c
## Run your code
ENTRYPOINT /usr/src/myapp/map

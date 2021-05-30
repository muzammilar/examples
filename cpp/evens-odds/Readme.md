# Evens-Odds

A basic example of even/odd numbers that uses Meson Build system with Google Test and builds a shared and a static libray for checking odd numbers (with tests).

```sh
docker-compose up --detach --build

# ssh to the app container
docker exec -it chstructingest-1 bash  # if it's running
docker run -it chstructingest bash  # if it's not running

```

## Setting Up Meson with GoogleTest

Check out the `Dockerfile` for details on setup. Current Meson version is `0.57.0`.

```sh
# Use wrap to check if gtest exists
meson wrap search gtest
meson wrap info gtest

# Add gtest if it exists as a subproject
mkdir -p subprojects
meson wrap install gtest

# Reconfigure the builddir if it has already been created
meson --reconfigure builddir
```

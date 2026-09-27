
## Development

#### Docker Builds

```sh
# build the applications
docker build --debug --tag helloapp-web --file ./Dockerfile.webapp .
# debugging
docker build --no-cache --debug --progress=plain --tag helloapp-web --file ./Dockerfile.webapp . 2>&1 | tee build.log
# run the web application
docker run -p 8080:8080 helloapp-web

# curl
curl http://localhost:8080/

```


## Project Layout

```
.
├── Cargo.lock
├── Cargo.toml
├── src/
│   ├── lib.rs
│   ├── main.rs
│   └── bin/
│       ├── named-executable.rs
│       ├── another-executable.rs
│       └── multi-file-executable/
│           ├── main.rs
│           └── some_module.rs
├── benches/
│   ├── large-input.rs
│   └── multi-file-bench/
│       ├── main.rs
│       └── bench_module.rs
├── examples/
│   ├── simple.rs
│   └── multi-file-example/
│       ├── main.rs
│       └── ex_module.rs
└── tests/
    ├── some-integration-tests.rs
    └── multi-file-test/
        ├── main.rs
        └── test_module.rs

```

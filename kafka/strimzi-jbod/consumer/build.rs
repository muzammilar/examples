fn main() {
    let proto = "../producer/proto/order.proto";
    println!("cargo:rerun-if-changed={proto}");
    prost_build::Config::new()
        .compile_protos(&[proto], &["../producer/proto"])
        .expect("compile order.proto");
}

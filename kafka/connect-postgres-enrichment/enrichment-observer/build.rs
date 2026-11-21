fn main() {
    let proto = "../proto/order.proto";
    println!("cargo:rerun-if-changed={proto}");
    prost_build::Config::new()
        .compile_protos(&[proto], &["../proto"])
        .expect("compile enrichment contract");
}

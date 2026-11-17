fn main() {
    println!("cargo:rerun-if-env-changed=ENGINE");
    println!("cargo:rustc-link-search=native=/opt/bench");
    println!(
        "cargo:rustc-link-lib=bench_{}",
        std::env::var("ENGINE").expect("ENGINE")
    );
    println!("cargo:rustc-link-arg=-Wl,-rpath,/opt/bench");
}

fn main() {
    // On macOS, we need to allow undefined symbols (provided by the Python interpreter at runtime)
    if std::env::var("CARGO_CFG_TARGET_OS") == Ok("macos".to_string()) {
        println!("cargo:rustc-link-arg=-undefined");
        println!("cargo:rustc-link-arg=dynamic_lookup");
    }
}

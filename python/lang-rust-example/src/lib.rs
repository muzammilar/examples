use pyo3::prelude::*;

/// Formats the sum of two numbers as string.
#[pyfunction]
fn sum_as_string(a: usize, b: usize) -> PyResult<String> {
    Ok((a + b).to_string())
}


/// A Python module implemented in Rust. The name of this function must match
/// the `lib.name` setting in the `Cargo.toml`, else Python will not be able to
/// import the module. You can use
/// If you change the `my_rs_lib` below, you can use #[pyo3(name="my_rs_lib")]
#[pymodule]
fn my_rs_lib(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    m.add_function(wrap_pyfunction!(add, m)?)?;
//    m.add_class::<MyPythonRustClass>()?;
    #[pyfn(m)]
    fn greet(name: &str) -> PyResult<String> {
        Ok(format!("Hello, {} from Rust!", name))
    }
    Ok(())
}

// Code that was written before before
#[pyfunction]
pub fn add(left: u64, right: u64) -> u64 {
    left + right
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn it_works() {
        let result = add(2, 2);
        assert_eq!(result, 4);
    }
}

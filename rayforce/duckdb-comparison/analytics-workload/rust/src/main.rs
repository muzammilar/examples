use std::{
    error::Error,
    ffi::{CStr, CString, c_char, c_int},
    fs::File,
    io::{BufRead, BufReader},
    time::Instant,
};

unsafe extern "C" {
    fn bench_open(threads: c_int) -> c_int;
    fn bench_error() -> *const c_char;
    fn bench_eval(source: *const c_char, materialize: c_int) -> *mut c_char;
    fn bench_free(result: *mut c_char);
    fn bench_close();
}
struct Runtime;
impl Drop for Runtime {
    fn drop(&mut self) {
        unsafe { bench_close() };
    }
}
fn engine_error() -> String {
    // The bridge retains its NUL-terminated error until the next call.
    unsafe { CStr::from_ptr(bench_error()) }
        .to_string_lossy()
        .into_owned()
}
struct NativeResult(*mut c_char);
impl Drop for NativeResult {
    fn drop(&mut self) {
        unsafe { bench_free(self.0) };
    }
}
fn query(source: &CStr, materialize: bool) -> Result<NativeResult, Box<dyn Error>> {
    // Calls stay on the main thread. The result owns this C allocation.
    let raw = unsafe { bench_eval(source.as_ptr(), materialize.into()) };
    if raw.is_null() {
        return Err(engine_error().into());
    }
    Ok(NativeResult(raw))
}
fn usage() -> Result<libc::rusage, Box<dyn Error>> {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::uninit();
    // getrusage initializes the complete structure on success.
    if unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) } != 0 {
        return Err(std::io::Error::last_os_error().into());
    }
    Ok(unsafe { usage.assume_init() })
}
fn cpu_ns() -> Result<u64, Box<dyn Error>> {
    let u = usage()?;
    Ok(((u.ru_utime.tv_sec + u.ru_stime.tv_sec) * 1_000_000_000
        + (u.ru_utime.tv_usec + u.ru_stime.tv_usec) * 1_000) as u64)
}
fn run() -> Result<(), Box<dyn Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        return Err("usage: workload PLAN THREADS".into());
    }
    if unsafe { bench_open(args[2].parse()?) } != 0 {
        return Err(engine_error().into());
    }
    let _runtime = Runtime;
    for line in BufReader::new(File::open(&args[1])?).lines() {
        let line = line?;
        let fields: Vec<&str> = line.splitn(4, '\t').collect();
        if fields.len() != 4 {
            return Err("invalid plan".into());
        }
        let (phase, name, expected, source) = (fields[0], fields[1], fields[2], fields[3]);
        let source = CString::new(source)?;
        let cpu = cpu_ns()?;
        let start = Instant::now();
        let actual = query(&source, phase != "load")?;
        let elapsed = start.elapsed().as_nanos();
        let used_cpu = cpu_ns()?.saturating_sub(cpu);
        let text = unsafe { CStr::from_ptr(actual.0) }.to_str()?;
        if text != expected {
            return Err(format!("incorrect result: {name}: {text}").into());
        }
        println!(
            "{phase}\t{name}\t{elapsed}\t{used_cpu}\t{}",
            usage()?.ru_maxrss
        );
    }
    Ok(())
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

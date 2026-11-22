use crate::{actors::append, protocol::now};
use anyhow::Result;
use serde_json::{Value, json};
use std::{
    fs::{self, File},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::Duration,
};

pub fn disk(root: &Path) -> Result<Value> {
    let mut logical = 0;
    let mut allocated = 0;
    let mut files = 0;
    let mut pending = vec![root.to_path_buf()];
    while let Some(path) = pending.pop() {
        let meta = path.symlink_metadata()?;
        anyhow::ensure!(!meta.is_symlink(), "refuse symlink in owned storage");
        if meta.is_dir() {
            for child in fs::read_dir(path)? {
                pending.push(child?.path());
            }
        } else if meta.is_file() {
            logical += meta.len();
            allocated += meta.blocks() * 512;
            files += 1;
        }
    }
    Ok(json!({"logical_bytes":logical,"allocated_bytes":allocated,"files":files}))
}
fn observed_disk(root: &Path) -> Value {
    match disk(root) {
        Ok(mut value) => {
            value["available"] = json!(true);
            value
        }
        Err(error) => {
            json!({"available":false,"error":format!("{error:#}"),"logical_bytes":null,"allocated_bytes":null,"files":null,"scope":"Incomplete traversal; native files can be replaced during compaction. Other resource counters remain recorded."})
        }
    }
}
fn read(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path).ok()
}
fn proc_values(pid: u32) -> Value {
    json!({"pid":pid,"stat":read(format!("/proc/{pid}/stat")),"io":read(format!("/proc/{pid}/io")),"status":read(format!("/proc/{pid}/status"))})
}
#[derive(Clone)]
pub struct Context {
    pub phase: Arc<Mutex<String>>,
    pub processes: Arc<Mutex<Vec<(String, u32)>>>,
    output: Arc<Mutex<File>>,
}
impl Context {
    pub fn sample(&self) -> Result<()> {
        let processes = self
            .processes
            .lock()
            .map_err(|_| anyhow::anyhow!("process tracker poisoned"))?
            .clone();
        let roles: Vec<Value> = processes
            .into_iter()
            .map(|(role, pid)| json!({"role":role,"process":proc_values(pid)}))
            .collect();
        let mut cgroup = serde_json::Map::new();
        for field in [
            "cpu.stat",
            "cpu.max",
            "memory.current",
            "memory.peak",
            "memory.max",
            "memory.swap.max",
            "memory.events",
            "io.stat",
        ] {
            cgroup.insert(
                field.into(),
                json!(read(PathBuf::from("/sys/fs/cgroup").join(field))),
            );
        }
        let value = json!({"timestamp_ns":now(),"phase":*self.phase.lock().map_err(|_|anyhow::anyhow!("phase poisoned"))?,"roles":roles,"container_cgroup":cgroup,"database_storage":observed_disk(Path::new("/database")),"result_storage":observed_disk(Path::new("/results"))});
        let mut output = self
            .output
            .lock()
            .map_err(|_| anyhow::anyhow!("resource writer poisoned"))?;
        append(&mut output, &value)
    }
}
pub struct Collector {
    pub context: Context,
    stop: Arc<AtomicBool>,
    job: Option<thread::JoinHandle<Result<()>>>,
}
impl Collector {
    pub fn start(path: &Path) -> Result<Self> {
        let file = File::create_new(path)?;
        let context = Context {
            phase: Arc::new(Mutex::new("start".into())),
            processes: Arc::new(Mutex::new(vec![("controller".into(), std::process::id())])),
            output: Arc::new(Mutex::new(file)),
        };
        let ctx = context.clone();
        let stop = Arc::new(AtomicBool::new(false));
        let flag = Arc::clone(&stop);
        let job = thread::spawn(move || {
            while !flag.load(Ordering::SeqCst) {
                ctx.sample()?;
                for _ in 0..10 {
                    if flag.load(Ordering::SeqCst) {
                        break;
                    }
                    thread::sleep(Duration::from_millis(100));
                }
            }
            Ok(())
        });
        Ok(Self {
            context,
            stop,
            job: Some(job),
        })
    }
    pub fn finish(mut self) -> Result<()> {
        self.context.sample()?;
        self.stop.store(true, Ordering::SeqCst);
        self.job
            .take()
            .expect("collector job")
            .join()
            .map_err(|_| anyhow::anyhow!("resource collector panicked"))??;
        Ok(())
    }
}
impl Drop for Collector {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(job) = self.job.take() {
            let _ = job.join();
        }
    }
}

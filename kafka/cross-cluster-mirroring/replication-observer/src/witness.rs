use std::{
    error::Error,
    fs::{self, File, OpenOptions},
    io::Write,
    os::unix::fs::OpenOptionsExt,
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};

use flate2::{Compression, write::GzEncoder};
use serde::Serialize;

pub type Result<T> = std::result::Result<T, Box<dyn Error>>;

pub fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before Unix epoch")
        .as_secs_f64()
}

pub fn atomic_json(path: &Path, value: &impl Serialize) -> Result<()> {
    let temporary = path.with_extension("json.tmp");
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&temporary)?;
    serde_json::to_writer(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    drop(file);
    fs::rename(temporary, path)?;
    File::open(path.parent().ok_or("snapshot has no parent")?)?.sync_all()?;
    Ok(())
}

pub struct Journal {
    encoder: Option<GzEncoder<File>>,
}
impl Journal {
    pub fn new(path: &Path) -> Result<Self> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(path)?;
        File::open(path.parent().ok_or("journal has no parent")?)?.sync_all()?;
        Ok(Self {
            encoder: Some(GzEncoder::new(file, Compression::fast())),
        })
    }
    pub fn append(&mut self, value: &impl Serialize) -> Result<()> {
        let encoder = self.encoder.as_mut().ok_or("append after witness close")?;
        serde_json::to_writer(&mut *encoder, value)?;
        encoder.write_all(b"\n")?;
        Ok(())
    }
    pub fn sync(&mut self) -> Result<()> {
        let encoder = self.encoder.as_mut().ok_or("sync after witness close")?;
        encoder.flush()?;
        encoder.get_ref().sync_all()?;
        Ok(())
    }
    pub fn finish(&mut self) -> Result<()> {
        if let Some(encoder) = self.encoder.take() {
            let file = encoder.finish()?;
            file.sync_all()?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::read::GzDecoder;
    use serde_json::json;
    use std::io::Read;

    #[test]
    fn exclusive_gzip_and_atomic_progress() -> Result<()> {
        let root =
            std::env::temp_dir().join(format!("kafka-reader-witness-{}", std::process::id()));
        fs::create_dir(&root)?;
        let path = root.join("reader.jsonl.gz");
        let mut journal = Journal::new(&path)?;
        journal.append(&json!({"stage":"get","seq":0}))?;
        journal.sync()?;
        journal.append(&json!({"stage":"get","seq":1}))?;
        journal.finish()?;
        assert!(Journal::new(&path).is_err());
        let mut text = String::new();
        GzDecoder::new(File::open(&path)?).read_to_string(&mut text)?;
        let rows: Vec<serde_json::Value> = text
            .lines()
            .map(serde_json::from_str)
            .collect::<std::result::Result<_, _>>()?;
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1]["seq"], 1);
        let progress = root.join("progress.json");
        atomic_json(&progress, &json!({"rows":1}))?;
        atomic_json(&progress, &json!({"rows":2}))?;
        assert_eq!(
            serde_json::from_reader::<_, serde_json::Value>(File::open(progress)?)?["rows"],
            2
        );
        fs::remove_dir_all(root)?;
        Ok(())
    }
}

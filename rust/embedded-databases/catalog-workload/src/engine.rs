use crate::protocol::Pair;
use anyhow::{Context, Result, ensure};
use fjall::Readable;
use redb::{ReadableDatabase, ReadableTable, TableDefinition};
use std::{
    fs::File,
    io::{BufWriter, Write},
    path::Path,
};

const LEFT: TableDefinition<&[u8], &[u8]> = TableDefinition::new("left");
const RIGHT: TableDefinition<&[u8], &[u8]> = TableDefinition::new("right");

pub enum Engine {
    Fjall {
        db: fjall::Database,
        left: fjall::Keyspace,
        right: fjall::Keyspace,
    },
    Redb(redb::Database),
}

impl Engine {
    pub fn open(backend: &str, path: &Path) -> Result<Self> {
        match backend {
            "fjall" => {
                let db = fjall::Database::builder(path)
                    .cache_size(128 * 1024 * 1024)
                    .open()?;
                let left = db.keyspace("left", fjall::KeyspaceCreateOptions::default)?;
                let right = db.keyspace("right", fjall::KeyspaceCreateOptions::default)?;
                Ok(Self::Fjall { db, left, right })
            }
            "redb" => {
                std::fs::create_dir_all(path)?;
                let db = redb::Database::builder()
                    .set_cache_size(128 * 1024 * 1024)
                    .create(path.join("catalog.redb"))?;
                let mut tx = db.begin_write()?;
                tx.set_durability(redb::Durability::Immediate)?;
                tx.open_table(LEFT)?;
                tx.open_table(RIGHT)?;
                tx.commit()?;
                Ok(Self::Redb(db))
            }
            _ => anyhow::bail!("unknown backend"),
        }
    }

    pub fn put(&self, rows: &[Pair]) -> Result<()> {
        match self {
            Self::Fjall { db, left, right } => {
                let mut batch = db.batch().durability(Some(fjall::PersistMode::SyncAll));
                for row in rows {
                    batch.insert(left, row.id.to_be_bytes(), row.left.as_bytes());
                    batch.insert(right, row.id.to_be_bytes(), row.right.as_bytes());
                }
                batch.commit()?;
            }
            Self::Redb(db) => {
                let mut tx = db.begin_write()?;
                tx.set_durability(redb::Durability::Immediate)?;
                {
                    let mut left = tx.open_table(LEFT)?;
                    let mut right = tx.open_table(RIGHT)?;
                    for row in rows {
                        left.insert(row.id.to_be_bytes().as_slice(), row.left.as_bytes())?;
                        right.insert(row.id.to_be_bytes().as_slice(), row.right.as_bytes())?;
                    }
                }
                tx.commit()?;
            }
        }
        Ok(())
    }

    pub fn read(&self, id: u64) -> Result<(Option<String>, Option<String>)> {
        let key = id.to_be_bytes();
        let decode = |v: Option<Vec<u8>>| {
            v.map(String::from_utf8)
                .transpose()
                .context("non-UTF8 body")
        };
        match self {
            Self::Fjall { db, left, right } => {
                let snap = db.snapshot();
                Ok((
                    decode(snap.get(left, key)?.map(|v| v.to_vec()))?,
                    decode(snap.get(right, key)?.map(|v| v.to_vec()))?,
                ))
            }
            Self::Redb(db) => {
                let tx = db.begin_read()?;
                let l = tx
                    .open_table(LEFT)?
                    .get(key.as_slice())?
                    .map(|v| v.value().to_vec());
                let r = tx
                    .open_table(RIGHT)?
                    .get(key.as_slice())?
                    .map(|v| v.value().to_vec());
                Ok((decode(l)?, decode(r)?))
            }
        }
    }

    pub fn delete(&self, id: u64) -> Result<()> {
        let key = id.to_be_bytes();
        match self {
            Self::Fjall { db, left, right } => {
                let mut batch = db.batch().durability(Some(fjall::PersistMode::SyncAll));
                batch.remove(left, key);
                batch.remove(right, key);
                batch.commit()?;
            }
            Self::Redb(db) => {
                let mut tx = db.begin_write()?;
                tx.set_durability(redb::Durability::Immediate)?;
                tx.open_table(LEFT)?.remove(key.as_slice())?;
                tx.open_table(RIGHT)?.remove(key.as_slice())?;
                tx.commit()?;
            }
        }
        Ok(())
    }

    pub fn export(&self, path: &Path) -> Result<u64> {
        ensure!(path.starts_with("/results"), "export outside witness mount");
        let mut output = BufWriter::with_capacity(1024 * 1024, File::create_new(path)?);
        let mut count = 0;
        let mut emit = |side: u8, key: &[u8], body: &[u8]| -> Result<()> {
            ensure!(
                key.len() == 8 && body.len() == 256,
                "invalid stored record shape"
            );
            output.write_all(&[side])?;
            output.write_all(key)?;
            output.write_all(body)?;
            count += 1;
            Ok(())
        };
        match self {
            Self::Fjall { db, left, right } => {
                let snap = db.snapshot();
                for (side, keyspace) in [(0, left), (1, right)] {
                    for item in snap.iter(keyspace) {
                        let (key, body) = item.into_inner()?;
                        emit(side, &key, &body)?;
                    }
                }
            }
            Self::Redb(db) => {
                let tx = db.begin_read()?;
                for (side, definition) in [(0, LEFT), (1, RIGHT)] {
                    for item in tx.open_table(definition)?.iter()? {
                        let (key, body) = item?;
                        emit(side, key.value(), body.value())?;
                    }
                }
            }
        }
        output.flush()?;
        output.get_ref().sync_all()?;
        Ok(count)
    }

    pub fn snapshot_control(&self, row: &Pair) -> Result<()> {
        let key = row.id.to_be_bytes();
        match self {
            Self::Fjall { db, left, right } => {
                let snapshot = db.snapshot();
                ensure!(
                    snapshot.get(left, key)?.is_none() && snapshot.get(right, key)?.is_none(),
                    "control ID occupied"
                );
                self.put(std::slice::from_ref(row))?;
                ensure!(
                    snapshot.get(left, key)?.is_none() && snapshot.get(right, key)?.is_none(),
                    "snapshot changed after commit"
                );
            }
            Self::Redb(db) => {
                let snapshot = db.begin_read()?;
                ensure!(
                    snapshot.open_table(LEFT)?.get(key.as_slice())?.is_none()
                        && snapshot.open_table(RIGHT)?.get(key.as_slice())?.is_none(),
                    "control ID occupied"
                );
                self.put(std::slice::from_ref(row))?;
                ensure!(
                    snapshot.open_table(LEFT)?.get(key.as_slice())?.is_none()
                        && snapshot.open_table(RIGHT)?.get(key.as_slice())?.is_none(),
                    "snapshot changed after commit"
                );
            }
        }
        ensure!(
            self.read(row.id)? == (Some(row.left.clone()), Some(row.right.clone())),
            "fresh snapshot missed commit"
        );
        self.delete(row.id)?;
        Ok(())
    }

    pub fn abort_control(&self, pair: &Pair) -> Result<()> {
        match self {
            Self::Fjall { db, left, right } => {
                let mut uncommitted = db.batch();
                uncommitted.insert(left, pair.id.to_be_bytes(), pair.left.as_bytes());
                uncommitted.insert(right, pair.id.to_be_bytes(), pair.right.as_bytes());
                drop(uncommitted);
            }
            Self::Redb(db) => {
                let tx = db.begin_write()?;
                tx.open_table(LEFT)?
                    .insert(pair.id.to_be_bytes().as_slice(), pair.left.as_bytes())?;
                tx.open_table(RIGHT)?
                    .insert(pair.id.to_be_bytes().as_slice(), pair.right.as_bytes())?;
                tx.abort()?;
            }
        }
        ensure!(self.read(pair.id)? == (None, None), "aborted write visible");
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_abort_snapshot_and_delete_controls() -> Result<()> {
        for backend in ["fjall", "redb"] {
            let root =
                std::env::temp_dir().join(format!("native-controls-{}", uuid::Uuid::new_v4()));
            let db = Engine::open(backend, &root)?;
            let row = crate::protocol::pair(9);
            db.abort_control(&row)?;
            db.snapshot_control(&row)?;
            ensure!(db.read(9)? == (None, None), "delete visible");
            drop(db);
            std::fs::remove_dir_all(root)?;
        }
        Ok(())
    }
}

use std::{fmt, io};

/// Errors from opening a node's data directory.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The underlying filesystem operation failed.
    #[error(transparent)]
    Io(#[from] io::Error),
    /// Another process already has this data directory open.
    #[error("data directory is already in use")]
    Locked,
    /// A file holds damaged data that isn't a torn final write. Reading past
    /// it could silently drop data. The cause is logged.
    #[error("{file} is corrupt at byte {offset}")]
    Corrupt { file: &'static str, offset: u64 },
}

/// Shorthand for `Result<T, kv_store::Error>`.
pub type Result<T, E = Error> = std::result::Result<T, E>;

/// Displays an error followed by each error that caused it, e.g.
/// `opening storage: data directory is already in use`.
#[derive(Debug)]
pub struct Chain<'a>(pub &'a (dyn std::error::Error + 'static));

impl fmt::Display for Chain<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)?;
        let mut source = self.0.source();
        while let Some(cause) = source {
            write!(f, ": {cause}")?;
            source = cause.source();
        }
        Ok(())
    }
}

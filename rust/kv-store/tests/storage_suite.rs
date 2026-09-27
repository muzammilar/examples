//! Runs openraft's storage compliance suite against the write-ahead log and
//! state machine.

use kv_store::storage::{self, LogStore, StateMachine};
use kv_store::{NodeId, TypeConfig};
use openraft::StorageError;
use openraft::testing::{StoreBuilder, Suite};
use tempfile::TempDir;

struct Builder;

#[expect(
    clippy::unused_async_trait_impl,
    reason = "openraft's builder trait is async; opening storage isn't"
)]
impl StoreBuilder<TypeConfig, LogStore, StateMachine, TempDir> for Builder {
    async fn build(&self) -> Result<(TempDir, LogStore, StateMachine), StorageError<NodeId>> {
        let dir = TempDir::new().expect("temp dir");
        let (log, state_machine) = storage::open(dir.path()).expect("fresh storage opens");
        Ok((dir, log, state_machine))
    }
}

#[test]
fn passes_the_openraft_storage_suite() {
    Suite::test_all(Builder).unwrap();
}

# Cold storage recovery

Archive, retained cold reads, object-store recovery and resource boundaries.

This is a project within the [parent lab](../../README.md). The parent owns
the Docker infrastructure, workload image builds, lifecycle guards and ignored
raw evidence. This directory groups this project's controllers, offline tests,
guides and compact reports; it does not create a second independent cluster.

## Quick start

Run these commands from this directory. They delegate to the parent lab's
lifecycle and preserve its ownership and failed-evidence guards. Experiments
are currently paused; these commands document the existing workflows.

1. `make docker-up` — prepare the parent lab infrastructure.
2. `make up` — build/configure the lab workload.
3. `make test` — run this project's selected scenario.
4. `make down` — stop/remove the workload as documented by the parent.
5. `make docker-down` — remove the parent lab's owned infrastructure.

## Files

- `scripts/`: project controllers and report generation.
- `tests/`: offline tests, when present; run the parent lab's controller checks.
- `docs/`: detailed procedures and limitations, when present.
- `reports/`: compact tracked measurements, when present.
- Raw run histories remain in the parent's ignored `results/`, at their original paths.

## Guides

- [Cold Storage](docs/COLD_STORAGE.md)
- [Failed Preservation](docs/FAILED_PRESERVATION.md)
- [Memory Diagnostics](docs/MEMORY_DIAGNOSTICS.md)

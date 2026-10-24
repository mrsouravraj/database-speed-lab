# Where the time goes

Status: complete. Run: 20261023T125843.892027Z.

Same SQLite 3.45.3 binary in both drivers, on Apple M3 Pro. 3 trials × 5s; 28,000 events per transaction. All variants use WAL.

Rates and phase shares are medians across trials. Phase timers include any waits within that phase. CPU/wall is process CPU utilization relative to one core, not system utilization.

| Variant | Events/sec | Min–max | Commits/sec | CPU/wall | Generate % | Insert % | Commit % |
|---|---:|---:|---:|---:|---:|---:|---:|
| python-original | 278,004 | 223,910–414,599 | 9.9 | 0.68 | 5.9 | 60.1 | 34.0 |
| cpp-original | 310,104 | 300,620–430,199 | 11.1 | 0.69 | 2.3 | 72.3 | 24.1 |
| python-without-rowid | 320,655 | 317,209–425,921 | 11.5 | 0.78 | 7.2 | 71.2 | 21.6 |
| cpp-without-rowid | 368,298 | 330,339–455,489 | 13.2 | 0.74 | 2.6 | 77.9 | 19.3 |
| cpp-original-values100 | 453,196 | 126,185–777,784 | 16.2 | 0.46 | 3.1 | 30.5 | 66.3 |
| cpp-without-rowid-values100 | 469,148 | 430,265–941,753 | 16.8 | 0.44 | 5.7 | 46.3 | 48.0 |
| python-no-fullfsync | 395,268 | 391,915–440,047 | 14.1 | 0.95 | 7.3 | 77.9 | 14.9 |
| python-sync-off | 335,340 | 264,208–450,486 | 12.0 | 0.85 | 7.8 | 80.9 | 11.4 |

## Comparisons

`python-original` vs `cpp-original` isolates the driver implementation on the same schema, SQLite binary, batch size, and requested synchronization policy. C++ generates IDs in its timed loop and reuses a prepared statement. Python already uses a cached statement via executemany; its loop is not one SQL parse per event. Native buffers are reused; Python allocates tuples and strings.

`without-rowid` changes the physical table layout while retaining the same text primary key, values, and CHECK constraints. It removes the separate rowid table and primary-key index duplication. `values100` additionally executes 100 inserted rows per prepared SQL statement, with the transaction size unchanged. These are disclosed changes, not the original workload.

`no-fullfsync` is a diagnostic storage-policy comparison. `sync-off` removes synchronization requests and weakens the failure contract; neither is a durable-performance headline. All other variants request FULL, fullfsync=ON, and checkpoint_fullfsync=ON. Automatic checkpoints remain at 1,000 pages; final explicit checkpoints are separately timed.

A slow insert phase can include SQLite B-tree work, binding, constraints, and page-cache spills; it is not a pure CPU timer. Background work is uncontrolled. All completed trials passed clean-reopen row-count, sample-value, and integrity checks. No crash durability was tested.

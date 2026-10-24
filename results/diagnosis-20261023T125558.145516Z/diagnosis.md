# Where the time goes

Status: complete. Run: 20261023T125558.145516Z.

Same SQLite 3.45.3 binary in both drivers, on unavailable. 1 trials × 0.01s; 28,000 events per transaction. All variants use WAL.

Rates and phase shares are medians across trials. Phase timers include any waits within that phase. CPU/wall is process CPU utilization relative to one core, not system utilization.

| Variant | Events/sec | Min–max | Commits/sec | CPU/wall | Generate % | Insert % | Commit % |
|---|---:|---:|---:|---:|---:|---:|---:|
| python-original | 408,175 | 408,175–408,175 | 14.6 | 0.88 | 6.6 | 72.4 | 21.0 |
| cpp-original | 439,690 | 439,690–439,690 | 15.7 | 0.89 | 2.2 | 74.9 | 22.8 |
| cpp-without-rowid-values100 | 821,131 | 821,131–821,131 | 29.3 | 0.70 | 4.2 | 44.1 | 51.6 |

## Comparisons

`python-original` vs `cpp-original` isolates the driver implementation on the same schema, SQLite binary, batch size, and requested synchronization policy. C++ generates IDs in its timed loop and reuses a prepared statement. Python already uses a cached statement via executemany; its loop is not one SQL parse per event. Native buffers are reused; Python allocates tuples and strings.

`without-rowid` changes the physical table layout while retaining the same text primary key, values, and CHECK constraints. It removes the separate rowid table and primary-key index duplication. `values100` additionally executes 100 inserted rows per prepared SQL statement, with the transaction size unchanged. These are disclosed changes, not the original workload.

`no-fullfsync` is a diagnostic storage-policy comparison. `sync-off` removes synchronization requests and weakens the failure contract; neither is a durable-performance headline. All other variants request FULL, fullfsync=ON, and checkpoint_fullfsync=ON. Automatic checkpoints remain at 1,000 pages; final explicit checkpoints are separately timed.

A slow insert phase can include SQLite B-tree work, binding, constraints, and page-cache spills; it is not a pure CPU timer. Background work is uncontrolled. All completed trials passed clean-reopen row-count, sample-value, and integrity checks. No crash durability was tested.

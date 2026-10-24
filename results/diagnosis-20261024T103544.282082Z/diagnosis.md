# Where the time goes

Status: complete. Run: 20261024T103544.282082Z.

Same SQLite 3.45.3 binary in both drivers, on Apple M3 Pro. 3 trials × 30s; 28,000 events per transaction. All variants use WAL.

Rates and phase shares are medians across trials. Phase timers include any waits within that phase. CPU/wall is process CPU utilization relative to one core, not system utilization.

| Variant | Events/sec | Min–max | Commits/sec | CPU/wall | Generate % | Insert % | Commit % |
|---|---:|---:|---:|---:|---:|---:|---:|
| cpp-without-rowid-or-check | 904,812 | 786,534–1,011,167 | 32.3 | 0.64 | 5.1 | 45.6 | 49.3 |

## Comparisons

`python-original` vs `cpp-original` isolates the driver implementation on the same schema, SQLite binary, batch size, and requested synchronization policy. C++ generates IDs in its timed loop and reuses a prepared statement. Python already uses a cached statement via executemany; its loop is not one SQL parse per event. Native buffers are reused; Python allocates tuples and strings.

`without-rowid` changes the physical table layout while retaining the same text primary key, values, and CHECK constraints. It removes the separate rowid table and primary-key index duplication. `values100` / `values1000` additionally execute 100 / 1,000 inserted rows per prepared SQL statement, with the transaction size unchanged. These are disclosed changes, not the original workload.

`or-check` rewrites the status CHECK as three equality comparisons joined with OR. It accepts the same three status values, while avoiding the ephemeral membership-index construction observed in this SQLite build's EXPLAIN output for the original IN expression. The raw results preserve each variant's exact schema and settings.

`no-fullfsync` is a diagnostic storage-policy comparison. `sync-off` removes synchronization requests and weakens the failure contract; neither is a durable-performance headline. All other variants request FULL, fullfsync=ON, and checkpoint_fullfsync=ON. Automatic checkpoints remain at 1,000 pages; final explicit checkpoints are separately timed.

A slow insert phase can include SQLite B-tree work, binding, constraints, and page-cache spills; it is not a pure CPU timer. Background work is uncontrolled. All completed trials passed clean-reopen row-count, sample-value, and integrity checks. No crash durability was tested.

# Why a Tiny Database Write Can Take So Long — local measurements

Run ID: `20261023T124734.578770Z`. Status: **complete**.

Machine: Apple M3 Pro; 11 logical CPUs; 18 GiB RAM; Darwin 27.0.1; arm64.
Runtime: CPython 3.13.0; SQLite 3.45.3.
Storage: `{"BusProtocol": "Apple Fabric", "FilesystemName": "APFS", "FilesystemType": "apfs", "SolidState": true, "TotalSize": 494384795648, "free_bytes": 13551689728, "total_bytes": 494384795648}`.

Each configuration runs for at least 30 seconds per trial, with 3 repetitions and 1 seconds of warmup per trial. Case order is shuffled each repetition (seed 42). fullfsync=1; automatic WAL checkpoint threshold=1000 pages.

## Measured results

Throughput columns show medians of trial rates. Range is min–max across trials. Latency columns show medians of each trial's nearest-rank percentiles; they are not pooled percentiles.

| Configuration | Events/commit | Events/sec | Min–max events/sec | Commits/sec | Batch p95 ms | Batch p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| wal-full-batch-1000 | 1,000 | 174,491 | 164,683–179,177 | 174.5 | 8.335 | 17.933 |

## Scope and interpretation

One local Python process, one SQLite connection, one sequential writer, no concurrent readers. Each event has a unique text ID, integer minor-unit amount, and captured status. The schema has a primary-key index and two CHECK constraints. Each trial uses a fresh database; warmup rows remain, with a WAL checkpoint before measurement.

Elapsed time includes event generation, Python/SQLite calls, BEGIN, inserts, COMMIT, automatic checkpoints, and instrumentation. Batch latency spans generation through commit return; commit latency measures the COMMIT call only, not an isolated disk flush. No arrival queue or batch-formation delay is modeled, so these are not end-to-end service latencies. The last full batch may run past the requested duration; its actual elapsed time is counted.

Setup, warmup, final explicit checkpoint, close/reopen, COUNT, and integrity checks are outside the throughput timer. Final checkpoint time and per-second WAL size samples are in results.json. WAL file size is allocated length, not outstanding uncheckpointed work; sampled maximum can miss peaks. The harness does not count fsync calls or individual automatic checkpoints.

All completed trials passed reopened row-count and integrity checks. This is a clean-shutdown check, not a power-loss test. Results characterize this short local workload, not production capacity. Faster cases grow larger databases during the fixed time budget. Python overhead, growing indexes, background work, power settings, cache state, and storage free space affect the rates.

## Durability settings

WAL/FULL requests a WAL sync at commit. WAL/NORMAL can lose acknowledged transactions after machine failure; WAL/OFF can also permit corruption. DELETE/FULL has filesystem-dependent durability; DELETE/EXTRA adds directory synchronization. These contracts assume a working storage stack. On macOS the default harness profile enables fullfsync to request F_FULLFSYNC; the recorded PRAGMA is not proof that the hardware honored it. See [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) and [fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync).

## Credit

Inspired by Tanay Karnik / Foo Community, *The Physics of Database Speed: from 300 to 1M transactions per second*. This payment-event workload is an independent experiment. These are local measurements, not a reproduction of the video's account-transfer benchmark.

# Why a Tiny Database Write Can Take So Long — local measurements

Run ID: `20261023T124336.326987Z`. Status: **complete**.

Machine: Apple M3 Pro; 11 logical CPUs; 18 GiB RAM; Darwin 27.0.1; arm64.
Runtime: CPython 3.13.0; SQLite 3.45.3.
Storage: `{"BusProtocol": "Apple Fabric", "FilesystemName": "APFS", "FilesystemType": "apfs", "SolidState": true, "TotalSize": 494384795648, "free_bytes": 12532707328, "total_bytes": 494384795648}`.

Each configuration runs for at least 5 seconds per trial, with 3 repetitions and 0.25 seconds of warmup per trial. Case order is shuffled each repetition (seed 42). fullfsync=1; automatic WAL checkpoint threshold=1000 pages.

## Measured results

Throughput columns show medians of trial rates. Range is min–max across trials. Latency columns show medians of each trial's nearest-rank percentiles; they are not pooled percentiles.

| Configuration | Events/commit | Events/sec | Min–max events/sec | Commits/sec | Batch p95 ms | Batch p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| delete-full | 1 | 77 | 71–96 | 77.2 | 23.391 | 63.368 |
| delete-extra | 1 | 89 | 85–90 | 89.3 | 16.487 | 25.603 |
| wal-full | 1 | 413 | 214–428 | 412.8 | 3.864 | 5.259 |
| wal-normal | 1 | 18,972 | 18,024–20,180 | 18,971.5 | 0.053 | 0.112 |
| wal-off | 1 | 34,396 | 33,154–41,122 | 34,396.4 | 0.052 | 0.129 |
| wal-full-batch-10 | 10 | 2,521 | 952–3,694 | 252.1 | 6.184 | 9.361 |
| wal-full-batch-100 | 100 | 23,033 | 11,216–33,029 | 230.3 | 6.279 | 12.692 |
| wal-full-batch-1000 | 1,000 | 173,589 | 62,686–176,360 | 173.6 | 9.032 | 16.900 |
| wal-full-batch-28000 | 28,000 | 265,854 | 256,712–298,836 | 9.5 | 153.796 | 192.575 |

## Scope and interpretation

One local Python process, one SQLite connection, one sequential writer, no concurrent readers. Each event has a unique text ID, integer minor-unit amount, and captured status. The schema has a primary-key index and two CHECK constraints. Each trial uses a fresh database; warmup rows remain, with a WAL checkpoint before measurement.

Elapsed time includes event generation, Python/SQLite calls, BEGIN, inserts, COMMIT, automatic checkpoints, and instrumentation. Batch latency spans generation through commit return; commit latency measures the COMMIT call only, not an isolated disk flush. No arrival queue or batch-formation delay is modeled, so these are not end-to-end service latencies. The last full batch may run past the requested duration; its actual elapsed time is counted.

Setup, warmup, final explicit checkpoint, close/reopen, COUNT, and integrity checks are outside the throughput timer. Final checkpoint time and per-second WAL size samples are in results.json. WAL file size is allocated length, not outstanding uncheckpointed work; sampled maximum can miss peaks. The harness does not count fsync calls or individual automatic checkpoints.

All completed trials passed reopened row-count and integrity checks. This is a clean-shutdown check, not a power-loss test. Results characterize this short local workload, not production capacity. Faster cases grow larger databases during the fixed time budget. Python overhead, growing indexes, background work, power settings, cache state, and storage free space affect the rates.

## Durability settings

WAL/FULL requests a WAL sync at commit. WAL/NORMAL can lose acknowledged transactions after machine failure; WAL/OFF can also permit corruption. DELETE/FULL has filesystem-dependent durability; DELETE/EXTRA adds directory synchronization. These contracts assume a working storage stack. On macOS the default harness profile enables fullfsync to request F_FULLFSYNC; the recorded PRAGMA is not proof that the hardware honored it. See [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) and [fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync).

## Credit

Inspired by Tanay Karnik / Foo Community, *The Physics of Database Speed: from 300 to 1M transactions per second*. This payment-event workload is an independent experiment. These are local measurements, not a reproduction of the video's account-transfer benchmark.

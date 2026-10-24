# Measurement protocol

## Workload

The schema is deliberately limited to the article's opening example:

```sql
CREATE TABLE payment_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    status TEXT NOT NULL CHECK (status IN ('captured', 'refunded', 'failed'))
);
```

IDs are unique, sequential, zero-padded strings such as `evt_000000001042`. Amounts vary deterministically from 100 to 100,099 minor units; every inserted status is `captured`. SQLite maintains the table and its text primary-key index. There is no account balance update or transfer invariant. Each batch uses explicit `BEGIN`, parameterized `executemany`, and `COMMIT`; all batch sizes use the same insert path.

This is a backlogged event ingestion workload. Each batch can begin immediately. There is no simulated arrival process, queue, network, retry, concurrent reader, or competing writer. No per-event payload or random value generation is moved outside the measured loop.

## Trial lifecycle

1. Create a fresh database on the selected filesystem and apply settings.
2. Read back and record journal, synchronous, fullfsync, checkpoint, cache, page, mmap, and locking settings. Fail if the requested journal, sync, or fullfsync flag did not take effect.
3. Run the same insert path for the warmup duration; keep those rows but exclude their counts and time. For WAL, perform a truncating checkpoint before measurement.
4. Run whole batches until at least the requested duration elapses. Count completed batches and their events. Include the final batch's full elapsed time.
5. Sample WAL file length and interval throughput approximately once per second. Collect every measured batch's batch and COMMIT-call duration in memory for exact nearest-rank percentiles.
6. After the measurement window, time a final truncating WAL checkpoint separately, close the connection, reopen, check expected row count and `integrity_check`.
7. Delete the disposable database. Save the completed trial before starting another one.

Repeat the full configuration set with a fresh shuffled order per repetition using a seeded PRNG. This reduces a fixed ordering bias, but does not control thermals, background activity, SSD caches, or OS cache state. Warmup duration is fixed, so faster configurations retain more warmup rows.

## Time and units

Wall time uses `perf_counter_ns`; process CPU time uses `process_time_ns`. CPU fraction is process CPU seconds divided by wall seconds; it is not whole-machine CPU utilization or proof that all remaining time was disk waiting.

```text
events/sec  = successfully inserted measured rows / measured wall seconds
commits/sec = successful measured COMMIT calls / measured wall seconds
events      = commits × batch size
```

Batch latency begins before generating that batch's parameters and ends when COMMIT returns. COMMIT-call latency begins just before the COMMIT SQL call. Neither metric isolates the kernel's flush duration. Time outside the per-batch span, such as recording metrics, still contributes to overall throughput elapsed time.

p95 and p99 use nearest rank: sort N observations, then select element `ceil(percentile × N)`. Reports take the median of each trial's percentile; they do not pool latency observations across trials. With very few commits, p99 may be the maximum observed batch. CSV reports retain more precision than the rounded Markdown table.

Throughput is the median of individual trial rates, not total events divided by total time across trials. Reports show min–max trial rates to expose variation. No confidence interval is claimed.

## Checkpoints and storage

Page size is 4,096 bytes; cache target is 8 MiB; mmap is disabled. Automatic checkpointing is set to 1,000 pages by default and stays active in the timed loop. There is no attempt to defer all checkpoint work until the end.

The final checkpoint timer is outside the throughput denominator and recorded in `final_checkpoint`. TRUNCATE's returned frame counts may be zero after successful truncation; they are not a count of all frames written during the trial. `wal_bytes_max_sampled` is a sampled allocated file length, not a true peak or the amount of uncheckpointed data. The harness does not instrument individual automatic checkpoint events.

SQLite's fullfsync request is enabled by default on macOS, disabled elsewhere. It is an additional platform setting, separate from `synchronous`. Flag readback establishes requested configuration; it does not demonstrate physical persistence or detect fallback in the filesystem/storage implementation.

## Reproduction and limits

`results.json` contains the schema, CLI configuration, runtime and SQLite versions, SQLite build options, CPU and memory when available, filesystem details, disk free space, power status on macOS, initial load average, a SHA-256 hash of the benchmark source, and Git revision/dirty state. No hostnames, usernames, serial numbers, or database rows are intentionally included. Review free-form notes before publishing them.

Every invocation creates a timestamped run directory. Interrupted runs are explicitly marked; never publish an incomplete report as the full matrix. Reports live in version control by design; disposable databases do not.

The runner uses a fixed-duration protocol, so faster cases grow larger tables and indexes. For long runs, storage and memory consumption increase: all latency observations are collected and the database is not size-capped. A typical default run uses one temporary database at a time and deletes it after verification. A forced machine shutdown may leave a disposable trial directory behind.

The result is the performance of this Python/SQLite workload on this filesystem under the observed conditions. It does not establish the fastest possible SQLite rate, a general CPU ceiling, production throughput, read performance, group commit behavior, or crash durability. A process-kill experiment would still not reproduce power loss. These are separate experiments.

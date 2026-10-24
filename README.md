# Why a Tiny Database Write Can Take So Long

A reproducible SQLite lab for the Medium article of the same name. Insert small payment events, change the commit policy, and measure where the time goes on **your own machine**.

Inspired by Tanay Karnik / Foo Community's [*The Physics of Database Speed: from 300 to 1M transactions per second*](https://foo.community/blog/the-physics-of-database-speed/). This repo uses an independent payment-event workload. Its measurements do not reproduce the video's account-transfer workload.

**Latest longer-run result: 1,057,639 payment events/sec median**, across three 30-second trials on an Apple M3 Pro. All three exceeded one million. This uses optimized C++, WAL/FULL, fullfsync=ON, and 112,000 events per transaction: approximately **9.44 database commits/sec**. [What was limiting throughput](docs/findings.md) · [Raw run report](results/diagnosis-20261024T103845.312724Z/diagnosis.md).

## Run it

Python 3.10+ with SQLite support. No pip packages, containers, or database server required.

```sh
python3 benchmark.py
```

The default run takes approximately three minutes: nine configurations, three repetitions, at least five measured seconds per trial. Every invocation gets its own UTC run ID in `results/`; previous runs are preserved.

```text
results/<run-id>/
  results.json   # machine details, settings, per-trial measurements, timelines, source hash
  summary.csv    # median rates, trial ranges, batch latency summaries
  report.md      # table and methodology ready to adapt for your article
```

Disposable database files live in `.bench-data/` on the repo's filesystem. They are deleted after each trial and ignored by Git. JSON, CSV, and Markdown reports are intentionally **not** ignored, so you can publish the evidence with the code. A failed/interrupted run retains completed trials and its status.

## What changes between cases?

| Case | Journal | Synchronization | Events per database transaction |
|---|---|---|---:|
| `delete-full` | DELETE | FULL | 1 |
| `delete-extra` | DELETE | EXTRA | 1 |
| `wal-full` | WAL | FULL | 1 |
| `wal-normal` | WAL | NORMAL | 1 |
| `wal-off` | WAL | OFF | 1 |
| `wal-full-batch-10` | WAL | FULL | 10 |
| `wal-full-batch-100` | WAL | FULL | 100 |
| `wal-full-batch-1000` | WAL | FULL | 1,000 |
| `wal-full-batch-28000` | WAL | FULL | 28,000 |

Batching cases all keep `synchronous=FULL`. The batch size of 28,000 connects to the article's discussion of what “TPS” counts; it is not assumed to be optimal.

**Events/sec and commits/sec are separate columns.** If a transaction inserts 1,000 events, 100,000 events/sec means 100 commits/sec.

On macOS, the harness enables `fullfsync` and `checkpoint_fullfsync` by default to request Apple's `F_FULLFSYNC` mechanism. SQLite's default for `fullfsync` is off. This setting can materially affect the comparison; it appears in every report. See [SQLite fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync).

## Experiments

```sh
# Short smoke run, not suitable for article claims
python3 benchmark.py --seconds 0.2 --repeats 1

# Longer comparison with more repetitions
python3 benchmark.py --seconds 30 --repeats 5 --warmup 1 \
  --notes "On AC power; other heavy workloads stopped"

# Sustained batching workload
python3 benchmark.py --cases wal-full-batch-1000 --seconds 60 --repeats 3

# Explore the effect of macOS fullfsync explicitly
python3 benchmark.py --cases wal-full --fullfsync off

# Put the temporary databases on a specific local volume
python3 benchmark.py --data-dir /path/to/local/volume/benchmark-data

# Correctness checks, including accounting and transaction rollback
python3 -m unittest discover -s tests -v
```

Use `--help` for all options. Keep the machine's power mode and background load stable, and record context with `--notes`. Read the filesystem and free-space fields before comparing two runs. Hardware fields that cannot be read are marked unavailable; the runner does not require administrator access.

## What the numbers mean

- The workload uses one process, one connection, and one sequential writer; there are no concurrent readers or network calls.
- Timed work includes Python event generation, a text primary-key index, CHECK constraints, transaction operations, automatic checkpoints, and instrumentation.
- Batch p95/p99 measure generation through commit return. They exclude waiting for incoming events to fill a batch and are **not** end-to-end service percentiles.
- Automatic WAL checkpointing remains enabled at 1,000 pages. The final explicit checkpoint is separately timed outside the throughput window.
- Every trial checks row counts and database integrity after a clean close and reopen. This does not simulate power failure.
- Median throughput and min–max ranges describe repeated short local experiments. The harness does not measure raw fsync latency, count system calls, or establish a machine's maximum database throughput.

For timing boundaries and caveats, see [the methodology](docs/methodology.md). For article text, see [the draft](docs/article.md). Actual run reports are in [results](results/).

## First recorded run

Apple M3 Pro, 11 logical CPUs, 18 GiB RAM, macOS 27.0.1, APFS SSD; CPython 3.13.0 and SQLite 3.45.3. `fullfsync=ON`. Three five-second trials per case, October 23, 2026. Background applications were not controlled; the volume had about 11.7 GiB free.

| Configuration | Median events/sec | Median commits/sec | Min–max events/sec |
|---|---:|---:|---:|
| DELETE / FULL, 1 event | 77 | 77.2 | 71–96 |
| WAL / FULL, 1 event | 413 | 412.8 | 214–428 |
| WAL / NORMAL, 1 event | 18,972 | 18,971.5 | 18,024–20,180 |
| WAL / OFF, 1 event | 34,396 | 34,396.4 | 33,154–41,122 |
| WAL / FULL, 1,000 events | 173,589 | 173.6 | 62,686–176,360 |
| WAL / FULL, 28,000 events | 265,854 | 9.5 | 256,712–298,836 |

The large ranges matter: these are observations from this machine under the recorded conditions. [Full report and all nine configurations](results/20261023T124336.326987Z/report.md) · [Raw measurements](results/20261023T124336.326987Z/results.json) · [CSV](results/20261023T124336.326987Z/summary.csv).

![Events and commits per second on the M3 Pro](results/20261023T124336.326987Z/throughput.png)

The optional chart renderer needs Matplotlib; the benchmark itself remains dependency-free:

```sh
python3 -m pip install matplotlib
python3 plot_results.py results/20261023T124336.326987Z/results.json
```

This creates PNG and SVG figures beside the report. Both chart axes use logarithmic scales.

## Diagnose the bottleneck with C++

```sh
python3 diagnose.py --seconds 5 --repeats 3
```

This optional experiment requires a C++17 compiler and SQLite development headers (available with Xcode Command Line Tools on macOS). The C++ timed loop loads the **exact SQLite binary already used by Python**, rather than linking a different system SQLite version. Your Python SQLite extension must export the SQLite C symbols. It runs on macOS/Linux; the runner builds the native library under `.bench-data/`.

The first comparison keeps the original schema and WAL/FULL/fullfsync settings and changes the driver. Further variants explicitly change table storage to `WITHOUT ROWID`, use 100 or 1,000 rows per SQL statement within the same transaction, rewrite the status CHECK to equivalent OR comparisons, or disable flush settings as diagnostic controls. The original text IDs, amounts, statuses, and accepted values remain. Event generation stays inside the measured loop. Native buffers and prepared statements are reused.

Every run saves `diagnosis.json` and `diagnosis.md`, including generation, insert, and commit timing, process CPU usage, compiler details, and repeated throughput ranges. Removing synchronization is labeled separately and is not used as a durable-performance result.

The optimized comparison crossed one million events/sec with **WAL/FULL, fullfsync=ON, and 28,000 events per transaction**:

| Driver / schema | Median events/sec | Min–max events/sec |
|---|---:|---:|
| Python / original | 304,449 | 184,323–383,365 |
| C++ / original | 317,312 | 273,131–378,414 |
| C++ / equivalent OR check | 618,601 | 322,880–855,244 |
| Python / OR check + WITHOUT ROWID | 689,334 | 631,715–861,850 |
| C++ / OR check + WITHOUT ROWID | **1,086,579** | 694,825–1,149,319 |

These are three five-second trials per variant on the same SQLite 3.45.3 binary. Language alone did not account for most of the original gap. [Complete diagnostic comparison](results/diagnosis-20261023T130504.220743Z/diagnosis.md).

A longer follow-up on October 24 measured **904,812 events/sec** median across three 30-second trials with 28,000 events per commit (786,534–1,011,167). The commit/checkpoint phase occupied a median 49.3% of wall time. The short-run million-event result therefore does not establish a consistently sustained million at that batch size. [Longer native run](results/diagnosis-20261024T103544.282082Z/diagnosis.md).

```sh
python3 diagnose.py --seconds 30 --warmup 1 --repeats 3 \
  --variants cpp-without-rowid-or-check

# Validated configuration with all three 30-second trials above 1M events/sec
python3 diagnose.py --seconds 30 --warmup 1 --repeats 3 --batch-size 112000 \
  --variants cpp-without-rowid-or-check
```

The native code retains constraints and generates every event inside the timer. At 28,000 events per transaction, one million events/sec means about **35.7 database commits/sec**.

At 112,000 events/commit, the three longer trials reached **1,003,464**, **1,225,748**, and **1,057,639 events/sec**. This larger batch improves amortization but also increases the amount of work sharing one transaction and its latency. [Validated larger-batch result](results/diagnosis-20261024T103845.312724Z/diagnosis.md).

## Durability references

The configurations have different failure contracts. WAL/FULL requests durable commits, WAL/NORMAL can lose recent acknowledged work after machine failure, and WAL/OFF also risks corruption. DELETE/EXTRA includes directory synchronization beyond DELETE/FULL. These guarantees depend on the storage stack honoring synchronization requests. Read [SQLite's synchronization documentation](https://www.sqlite.org/pragma.html#pragma_synchronous) before applying settings to a service.

Further reading: [SQLite WAL](https://www.sqlite.org/wal.html), [atomic commit](https://www.sqlite.org/atomiccommit.html), [Linux fsync](https://man7.org/linux/man-pages/man2/fsync.2.html), [PostgreSQL asynchronous commit](https://www.postgresql.org/docs/current/wal-async-commit.html), and [PostgreSQL WAL configuration](https://www.postgresql.org/docs/current/wal-configuration.html).

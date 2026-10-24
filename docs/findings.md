# Why the initial benchmark fell short of one million

The machine can cross one million payment events/sec. The final measured configuration used C++, an equivalent status constraint, `WITHOUT ROWID`, and 112,000 events per transaction. It kept WAL/FULL, `fullfsync=ON`, `checkpoint_fullfsync=ON`, and automatic checkpoints at 1,000 pages.

Three 30-second trials measured **1,003,464**, **1,225,748**, and **1,057,639 events/sec**. The median is **1,057,639 events/sec**, or about **9.44 committed transactions/sec**. This is a repeated local result, not a guarantee of production throughput or a power-failure test.

[Final report](../results/diagnosis-20261024T103845.312724Z/diagnosis.md) · [Per-trial evidence and PRAGMA readback](../results/diagnosis-20261024T103845.312724Z/diagnosis.json)

## 1. Python was only part of the original cost

In the same diagnostic matrix, the original Python workload measured a median **304,449 events/sec**, while C++ with the original schema measured **317,312**. That small difference, with broad overlapping ranges, does not support blaming Python for the whole gap.

Python's `sqlite3` module already executes SQLite's native engine. Its `executemany` path reuses a prepared statement. The original workload spent substantial time doing database work, even with the driver rewritten.

The C++ driver loads SQLite 3.45.3 from the Python extension already loaded into the process. Both drivers therefore use the same SQLite binary and compile options. C++ generates every ID and amount inside the timed loop, but reuses native buffers. Python allocates its tuples and strings each batch. This compares concrete driver implementations, not an abstract language speed ratio.

## 2. The initial status CHECK introduced repeated SQLite work

The original harness used:

```sql
CHECK (status IN ('captured', 'refunded', 'failed'))
```

On this SQLite build, `EXPLAIN INSERT ...` showed `OpenEphemeral` followed by three `IdxInsert` operations constructing the membership lookup before the actual primary-key index insertion. Rewriting the expression to:

```sql
CHECK (status = 'captured' OR status = 'refunded' OR status = 'failed')
```

removed the temporary structure from the observed bytecode. The values accepted by the constraint remain the same. Tests cover valid statuses, invalid statuses, NULL, and nonpositive amounts.

C++ with this rewrite, but the original rowid table, measured **618,601 events/sec** median in the short diagnostic matrix. This is a SQLite-version-specific observation about this expression, not a general rule that `IN` is slow.

## 3. The text primary key required two storage structures

The original schema used a normal SQLite rowid table plus a separate unique index for the text event ID. `WITHOUT ROWID` stores the table around that primary key instead. This removes duplicate key storage and changes the B-tree work. It retains the text IDs, amount/status columns, uniqueness, NOT NULL constraints, and equivalent CHECK constraints.

It is a physical schema change with API implications, not a universal option to enable for every table. See [SQLite's WITHOUT ROWID documentation](https://www.sqlite.org/withoutrowid.html).

With the equivalent CHECK and `WITHOUT ROWID`, Python measured **689,334 events/sec** median and C++ measured **1,086,579** in three five-second trials. Once the database work became cheaper, driver overhead mattered more. Ranges remain important: these were not controlled runs on an otherwise idle machine.

[Full driver/schema comparison](../results/diagnosis-20261023T130504.220743Z/diagnosis.md)

## 4. Commit and checkpoint costs remained

The optimized C++ implementation with 28,000 events per transaction measured **904,812 events/sec** median over three 30-second trials. The median commit-phase share was **49.3%** of elapsed time. This phase includes SQLite commit processing and automatic checkpoints; it is not a direct fsync measurement.

Increasing the batch to 112,000 events produced the final **1,057,639 events/sec** median. The median commit-phase share was **40.5%**. The larger batch changes latency and the amount of work sharing an atomic boundary. It does not create one million independently committed transactions per second.

The final explicit checkpoint is separately timed outside the throughput denominator. Automatic checkpoints are included. Clean-reopen row-count, sample-value, and integrity checks passed for every completed diagnostic trial.

## 5. Background activity affects the interpretation

The machine has 11 logical CPUs and 18 GiB RAM. Recorded load-average snapshots were high: the 28,000-event longer run began with a one-minute load average of approximately 121. Load average is not a CPU-utilization percentage and is not attributable to this single writer alone.

A read-only `top -l 1 -n 0` snapshot on October 24 at 14:43 Dubai time, after the runs, showed approximately 17 GiB memory in use, around 7 GiB in the compressor, and roughly two thirds of CPU time busy. It was not taken during the timed loop, so it is context rather than a measured cause of any individual slow trial. The recorded runs were on AC power.

These observations reinforce why the results are not a clean maximum-hardware-capacity measurement. They do not prove that storage, memory, or background work alone caused the observed variation. No unrelated processes or machine settings were changed.

## Reproduce the validated configuration

```sh
python3 diagnose.py --seconds 30 --warmup 1 --repeats 3 --batch-size 112000 \
  --variants cpp-without-rowid-or-check
```

Each invocation retains a separate run directory. The source, settings, compiler, SQLite version, hardware metadata, actual PRAGMA values, timing phases, and per-trial results make the claim inspectable. A quiet-machine rerun can characterize different operating conditions; it should be saved as a new experiment.

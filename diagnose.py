#!/usr/bin/env python3
"""Compare Python and C++ against the exact SQLite library loaded by Python."""

import _sqlite3
import argparse
import ctypes
import hashlib
import json
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import benchmark


VARIANTS = {
    "python-original": ("python", False, 1, True, "FULL"),
    "cpp-original": ("cpp", False, 1, True, "FULL"),
    "python-without-rowid": ("python", True, 1, True, "FULL"),
    "cpp-without-rowid": ("cpp", True, 1, True, "FULL"),
    "cpp-original-values100": ("cpp", False, 100, True, "FULL"),
    "cpp-without-rowid-values100": ("cpp", True, 100, True, "FULL"),
    "python-no-fullfsync": ("python", False, 1, False, "FULL"),
    "python-sync-off": ("python", False, 1, False, "OFF"),
    "cpp-original-or-check": ("cpp", False, 1, True, "FULL"),
    "python-without-rowid-or-check": ("python", True, 1, True, "FULL"),
    "cpp-without-rowid-or-check": ("cpp", True, 1, True, "FULL"),
    "cpp-without-rowid-or-check-values1000": ("cpp", True, 1000, True, "FULL"),
}


def native_library():
    folder = benchmark.ROOT / ".bench-data"
    folder.mkdir(exist_ok=True)
    library = folder / "native_benchmark.so"
    command = ["c++", "-O3", "-std=c++17", "-Wall", "-Wextra", "-Werror",
               "-dynamiclib" if sys.platform == "darwin" else "-shared", "-fPIC",
               str(benchmark.ROOT / "native_benchmark.cpp"), "-o", str(library)]
    if sys.platform.startswith("linux"):
        command.append("-ldl")
    subprocess.run(command, check=True)
    native = ctypes.CDLL(str(library))
    function = native.run_native
    function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                         ctypes.c_double, ctypes.c_double, ctypes.c_int, ctypes.c_int,
                         ctypes.c_void_p, ctypes.c_size_t]
    function.restype = ctypes.c_int
    return function, command


def measure_python(connection, seconds, batch_size, statement_rows, first_event=0):
    events = commits = 0
    generation_seconds = insert_seconds = commit_seconds = 0.0
    latencies = []
    started = time.perf_counter()
    cpu_started = time.process_time()
    while time.perf_counter() - started < seconds:
        batch_started = time.perf_counter()
        rows = [(f"evt_{number:012d}", 100 + number % 100000, "captured")
                for number in range(first_event + events, first_event + events + batch_size)]
        generation_seconds += time.perf_counter() - batch_started
        insert_started = time.perf_counter()
        connection.execute("BEGIN")
        connection.executemany(benchmark.INSERT, rows)
        insert_seconds += time.perf_counter() - insert_started
        commit_started = time.perf_counter()
        connection.execute("COMMIT")
        commit_seconds += time.perf_counter() - commit_started
        latencies.append(int((time.perf_counter() - batch_started) * 1e9))
        events += batch_size
        commits += 1
    elapsed = time.perf_counter() - started
    cpu = time.process_time() - cpu_started
    return {"sqlite": sqlite3.sqlite_version, "events": events, "commits": commits,
            "elapsed_seconds": elapsed, "process_cpu_seconds": cpu,
            "generation_seconds": generation_seconds, "insert_seconds": insert_seconds,
            "commit_seconds": commit_seconds,
            "batch_p99_ms": benchmark.latency_summary(latencies)["p99_ms"]}


def trial(name, args, native):
    driver, without_rowid, statement_rows, fullfsync, synchronous = VARIANTS[name]
    if args.batch_size % statement_rows:
        raise ValueError("Batch size must be divisible by statement row count")
    schema = benchmark.SCHEMA.strip()
    if "or-check" in name:
        schema = schema.replace("status IN ('captured', 'refunded', 'failed')",
                                "status = 'captured' OR status = 'refunded' OR status = 'failed'")
    if without_rowid:
        schema = schema.removesuffix(";") + " WITHOUT ROWID;"
    setup = (f"PRAGMA page_size=4096; PRAGMA journal_mode=WAL; PRAGMA synchronous={synchronous}; "
             f"PRAGMA fullfsync={int(fullfsync)}; PRAGMA checkpoint_fullfsync={int(fullfsync)}; "
             "PRAGMA wal_autocheckpoint=1000; PRAGMA cache_size=-8192; PRAGMA mmap_size=0; " + schema)
    with tempfile.TemporaryDirectory(prefix="diagnose-", dir=benchmark.ROOT / ".bench-data") as folder:
        path = Path(folder) / "events.sqlite3"
        if driver == "cpp":
            output = ctypes.create_string_buffer(4096)
            status = native(_sqlite3.__file__.encode(), str(path).encode(), setup.encode(),
                            args.seconds, args.warmup, args.batch_size, statement_rows, output, len(output))
            if status:
                raise RuntimeError(output.value.decode())
            result = json.loads(output.value)
        else:
            with closing(sqlite3.connect(path, isolation_level=None)) as connection:
                connection.executescript(setup)
                pragmas = {key: connection.execute(f"PRAGMA {key}").fetchone()[0]
                           for key in ("synchronous", "fullfsync", "checkpoint_fullfsync", "wal_autocheckpoint")}
                pragmas["journal_is_wal"] = int(connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal")
                warmup = measure_python(connection, args.warmup, args.batch_size, statement_rows)
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                result = measure_python(connection, args.seconds, args.batch_size, statement_rows, warmup["events"])
                result["warmup_events"] = warmup["events"]
                result["pragmas"] = pragmas
                checkpoint_started = time.perf_counter()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                result["final_checkpoint_seconds"] = time.perf_counter() - checkpoint_started
        with closing(sqlite3.connect(path)) as connection:
            verified = connection.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            sample = connection.execute("SELECT event_id, amount_minor, status FROM payment_events ORDER BY event_id LIMIT 3").fetchall()
            expected_sample = [(f"evt_{number:012d}", 100 + number, "captured") for number in range(3)]
            if verified != result["events"] + result["warmup_events"] or integrity != [("ok",)] or sample != expected_sample:
                raise RuntimeError("Data verification failed")
        if result["sqlite"] != sqlite3.sqlite_version:
            raise RuntimeError("SQLite library mismatch")
        expected_pragmas = {"synchronous": 2 if synchronous == "FULL" else 0,
                            "fullfsync": int(fullfsync), "checkpoint_fullfsync": int(fullfsync),
                            "wal_autocheckpoint": 1000, "journal_is_wal": 1}
        if result["pragmas"] != expected_pragmas:
            raise RuntimeError(f"Unexpected SQLite settings: {result['pragmas']}")
        result.update(name=name, driver=driver, without_rowid=without_rowid, statement_rows=statement_rows,
                      fullfsync=fullfsync, synchronous=synchronous, batch_size=args.batch_size,
                      events_per_second=result["events"] / result["elapsed_seconds"],
                      commits_per_second=result["commits"] / result["elapsed_seconds"],
                      cpu_fraction=result["process_cpu_seconds"] / result["elapsed_seconds"],
                      database_bytes=path.stat().st_size, integrity="ok", setup_sql=setup)
        return result


def save(folder, payload):
    (folder / "diagnosis.json").write_text(json.dumps(payload, indent=2) + "\n")
    lines = ["# Where the time goes", "", f"Status: {payload['status']}. Run: {payload['run_id']}.", "",
             f"Same SQLite {payload['machine']['sqlite']} binary in both drivers, on {payload['machine']['cpu']}. "
             f"{payload['config']['repeats']} trials × {payload['config']['seconds']:g}s; "
             f"{payload['config']['batch_size']:,} events per transaction. All variants use WAL.", "",
             "Rates and phase shares are medians across trials. Phase timers include any waits within that phase. "
             "CPU/wall is process CPU utilization relative to one core, not system utilization.", "",
             "| Variant | Events/sec | Min–max | Commits/sec | CPU/wall | Generate % | Insert % | Commit % |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name in VARIANTS:
        trials = [result for result in payload["trials"] if result["name"] == name]
        if not trials:
            continue
        median = lambda key: statistics.median(result[key] for result in trials)
        fraction = lambda key: statistics.median(result[key] / result["elapsed_seconds"] for result in trials) * 100
        rates = [result["events_per_second"] for result in trials]
        lines.append(f"| {name} | {median('events_per_second'):,.0f} | {min(rates):,.0f}–{max(rates):,.0f} | "
                     f"{median('commits_per_second'):,.1f} | {median('cpu_fraction'):.2f} | "
                     f"{fraction('generation_seconds'):.1f} | {fraction('insert_seconds'):.1f} | {fraction('commit_seconds'):.1f} |")
    lines += ["", "## Comparisons", "",
              "`python-original` vs `cpp-original` isolates the driver implementation on the same schema, "
              "SQLite binary, batch size, and requested synchronization policy. C++ generates IDs in its timed loop "
              "and reuses a prepared statement. Python already uses a cached statement via executemany; "
              "its loop is not one SQL parse per event. Native buffers are reused; Python allocates tuples and strings.", "",
              "`without-rowid` changes the physical table layout while retaining the same text primary key, values, "
              "and CHECK constraints. It removes the separate rowid table and primary-key index duplication. "
              "`values100` / `values1000` additionally execute 100 / 1,000 inserted rows per prepared SQL statement, "
              "with the transaction size unchanged. These are disclosed changes, not the original workload.", "",
              "`or-check` rewrites the status CHECK as three equality comparisons joined with OR. "
              "It accepts the same three status values, while avoiding the ephemeral membership-index "
              "construction observed in this SQLite build's EXPLAIN output for the original IN expression. "
              "The raw results preserve each variant's exact schema and settings.", "",
              "`no-fullfsync` is a diagnostic storage-policy comparison. `sync-off` removes synchronization requests "
              "and weakens the failure contract; neither is a durable-performance headline. "
              "All other variants request FULL, fullfsync=ON, and checkpoint_fullfsync=ON. "
              "Automatic checkpoints remain at 1,000 pages; final explicit checkpoints are separately timed.", "",
              "A slow insert phase can include SQLite B-tree work, binding, constraints, and page-cache spills; "
              "it is not a pure CPU timer. Background work is uncontrolled. All completed trials passed clean-reopen "
              "row-count, sample-value, and integrity checks. No crash durability was tested.", ""]
    (folder / "diagnosis.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=benchmark.positive_seconds, default=5)
    parser.add_argument("--warmup", type=benchmark.positive_seconds, default=0.25)
    parser.add_argument("--repeats", type=benchmark.positive_int, default=3)
    parser.add_argument("--batch-size", type=benchmark.positive_int, default=28000)
    parser.add_argument("--variants", nargs="+", choices=list(VARIANTS), default=list(VARIANTS))
    args = parser.parse_args()
    native, build = native_library()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = benchmark.ROOT / "results" / f"diagnosis-{run_id}"
    folder.mkdir(parents=True)
    payload = {"run_id": run_id, "status": "running", "config": vars(args),
               "machine": benchmark.machine_info(benchmark.ROOT / ".bench-data"),
               "compiler": benchmark.command_output(["c++", "--version"]),
               "build": [Path(part).name if str(benchmark.ROOT) in part else part for part in build],
               "source_sha256": {name: hashlib.sha256((benchmark.ROOT / name).read_bytes()).hexdigest()
                                 for name in ("diagnose.py", "native_benchmark.cpp", "benchmark.py")},
               "trials": []}
    save(folder, payload)
    rng = random.Random(42)
    try:
        for repeat in range(1, args.repeats + 1):
            variants = args.variants.copy()
            rng.shuffle(variants)
            for name in variants:
                print(f"[{repeat}/{args.repeats}] {name}: measuring…", flush=True)
                result = trial(name, args, native)
                result["repeat"] = repeat
                payload["trials"].append(result)
                save(folder, payload)
                print(f"  {result['events_per_second']:,.0f} events/s, CPU/wall={result['cpu_fraction']:.2f}", flush=True)
    except BaseException as error:
        payload["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        payload["error"] = str(error)
        save(folder, payload)
        raise
    payload["status"] = "complete"
    save(folder, payload)
    print(folder / "diagnosis.md", flush=True)


if __name__ == "__main__":
    main()

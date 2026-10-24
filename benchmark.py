#!/usr/bin/env python3
"""Reproducible, standard-library-only SQLite payment-event benchmark."""

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import plistlib
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from array import array
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCHEMA = """
CREATE TABLE payment_events (
    event_id TEXT PRIMARY KEY NOT NULL,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    status TEXT NOT NULL CHECK (status IN ('captured', 'refunded', 'failed'))
);
"""
INSERT = "INSERT INTO payment_events VALUES (?, ?, ?)"


@dataclass(frozen=True)
class Case:
    name: str
    journal: str
    synchronous: str
    batch_size: int


CASES = [
    Case("delete-full", "DELETE", "FULL", 1),
    Case("delete-extra", "DELETE", "EXTRA", 1),
    Case("wal-full", "WAL", "FULL", 1),
    Case("wal-normal", "WAL", "NORMAL", 1),
    Case("wal-off", "WAL", "OFF", 1),
    *[Case(f"wal-full-batch-{size}", "WAL", "FULL", size)
      for size in (10, 100, 1000, 28000)],
]


def command_output(arguments):
    try:
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def machine_info(data_dir):
    info = {
        "os": platform.system(), "os_release": platform.release(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version, "logical_cpus": os.cpu_count(),
        "cpu": platform.processor() or "unavailable", "memory_bytes": None,
        "storage": {}, "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    }
    if sys.platform == "darwin":
        info["os_version"] = command_output(["sw_vers", "-productVersion"])
        info["cpu"] = command_output(["sysctl", "-n", "machdep.cpu.brand_string"]) or "unavailable"
        memory = command_output(["sysctl", "-n", "hw.memsize"])
        info["memory_bytes"] = int(memory) if memory else None
        filesystem = command_output(["df", "-P", str(data_dir.resolve())])
        device = filesystem.splitlines()[-1].split()[0] if filesystem else str(data_dir.resolve())
        disk = command_output(["diskutil", "info", "-plist", device])
        if disk:
            details = plistlib.loads(disk.encode())
            for key in ("FilesystemType", "FilesystemName", "SolidState", "BusProtocol", "TotalSize"):
                if key in details:
                    info["storage"][key] = details[key]
        info["power_status"] = command_output(["pmset", "-g", "batt"])
    elif sys.platform.startswith("linux"):
        cpuinfo = Path("/proc/cpuinfo")
        if cpuinfo.exists():
            for line in cpuinfo.read_text().splitlines():
                if line.startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    info["memory_bytes"] = int(line.split()[1]) * 1024
        info["storage"]["filesystem"] = command_output(["stat", "-f", "-c", "%T", str(data_dir)])
    usage = shutil.disk_usage(data_dir)
    info["storage"].update(total_bytes=usage.total, free_bytes=usage.free)
    with closing(sqlite3.connect(":memory:")) as connection:
        info["sqlite_compile_options"] = [row[0] for row in connection.execute("PRAGMA compile_options")]
    return info


def configure(connection, case, fullfsync, checkpoint_pages):
    connection.execute("PRAGMA page_size=4096")
    journal = connection.execute(f"PRAGMA journal_mode={case.journal}").fetchone()[0]
    if journal.upper() != case.journal:
        raise RuntimeError(f"Requested {case.journal}; SQLite returned {journal}")
    connection.execute(f"PRAGMA synchronous={case.synchronous}")
    connection.execute(f"PRAGMA fullfsync={int(fullfsync)}")
    connection.execute(f"PRAGMA checkpoint_fullfsync={int(fullfsync)}")
    connection.execute(f"PRAGMA wal_autocheckpoint={checkpoint_pages}")
    connection.execute("PRAGMA cache_size=-8192")
    connection.execute("PRAGMA mmap_size=0")
    connection.executescript(SCHEMA)
    names = ("journal_mode", "synchronous", "fullfsync", "checkpoint_fullfsync",
             "wal_autocheckpoint", "page_size", "cache_size", "mmap_size", "locking_mode")
    actual = {name: connection.execute(f"PRAGMA {name}").fetchone()[0] for name in names}
    expected_sync = {"OFF": 0, "NORMAL": 1, "FULL": 2, "EXTRA": 3}[case.synchronous]
    if actual["synchronous"] != expected_sync or actual["fullfsync"] != int(fullfsync):
        raise RuntimeError(f"SQLite did not apply requested settings: {actual}")
    return actual


def file_size(path):
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def latency_summary(samples):
    ordered = sorted(samples)
    return {
        "count": len(ordered),
        "p50_ms": ordered[math.ceil(len(ordered) * 0.50) - 1] / 1e6,
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1] / 1e6,
        "p99_ms": ordered[math.ceil(len(ordered) * 0.99) - 1] / 1e6,
        "max_ms": ordered[-1] / 1e6,
    }


def write_for(connection, db_path, case, seconds, first_event=0, sample=True):
    batch_times = array("Q")
    commit_times = array("Q")
    timeline = []
    events = commits = previous_events = 0
    wal_path = Path(str(db_path) + "-wal")
    started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    deadline = started + int(seconds * 1e9)
    previous_time = started
    finished = started
    while finished < deadline:
        batch_started = time.perf_counter_ns()
        rows = [(f"evt_{number:012d}", 100 + number % 100000, "captured")
                for number in range(first_event + events, first_event + events + case.batch_size)]
        connection.execute("BEGIN")
        try:
            connection.executemany(INSERT, rows)
            commit_started = time.perf_counter_ns()
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        committed = time.perf_counter_ns()
        events += case.batch_size
        commits += 1
        if sample:
            batch_times.append(committed - batch_started)
            commit_times.append(committed - commit_started)
            if committed - previous_time >= 1_000_000_000:
                timeline.append({"elapsed_seconds": (committed - started) / 1e9,
                                 "events_per_second": (events - previous_events) * 1e9 / (committed - previous_time),
                                 "wal_bytes": file_size(wal_path)})
                previous_time, previous_events = committed, events
        finished = time.perf_counter_ns()
    elapsed = (finished - started) / 1e9
    cpu_seconds = (time.process_time_ns() - cpu_started) / 1e9
    if not sample:
        return {"events": events, "commits": commits, "elapsed_seconds": elapsed}
    timeline.append({"elapsed_seconds": elapsed,
                     "events_per_second": (events - previous_events) * 1e9 / (finished - previous_time),
                     "wal_bytes": file_size(wal_path)})
    return {
        "events": events, "commits": commits, "elapsed_seconds": elapsed,
        "process_cpu_seconds": cpu_seconds, "process_cpu_fraction": cpu_seconds / elapsed,
        "events_per_second": events / elapsed, "commits_per_second": commits / elapsed,
        "batch_latency": latency_summary(batch_times), "commit_latency": latency_summary(commit_times),
        "timeline": timeline, "wal_bytes_end": file_size(wal_path),
        "wal_bytes_max_sampled": max(point["wal_bytes"] for point in timeline),
        "database_bytes_before_final_checkpoint": file_size(db_path),
    }


def run_trial(case, args, repeat, order):
    with tempfile.TemporaryDirectory(prefix="trial-", dir=args.data_dir) as folder:
        db_path = Path(folder) / "events.sqlite3"
        connection = sqlite3.connect(db_path, isolation_level=None)
        try:
            pragmas = configure(connection, case, args.fullfsync, args.checkpoint_pages)
            warmup = write_for(connection, db_path, case, args.warmup, sample=False)
            if case.journal == "WAL":
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            result = write_for(connection, db_path, case, args.seconds, warmup["events"])
            checkpoint = None
            if case.journal == "WAL":
                checkpoint_start = time.perf_counter_ns()
                status = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                checkpoint = {"elapsed_ms": (time.perf_counter_ns() - checkpoint_start) / 1e6,
                              "busy": status[0], "log_frames": status[1], "checkpointed_frames": status[2]}
                if status[0] != 0:
                    raise RuntimeError(f"Final checkpoint was busy: {status}")
            result["database_bytes_final"] = file_size(db_path)
        finally:
            connection.close()
        with closing(sqlite3.connect(db_path)) as reopened:
            row_count = reopened.execute("SELECT COUNT(*) FROM payment_events").fetchone()[0]
            integrity = [row[0] for row in reopened.execute("PRAGMA integrity_check")]
        expected = warmup["events"] + result["events"]
        if row_count != expected or integrity != ["ok"]:
            raise RuntimeError(f"Verification failed: rows={row_count}, expected={expected}, integrity={integrity}")
        return {"case": asdict(case), "repeat": repeat, "order": order,
                "pragmas": pragmas, "warmup": warmup, **result,
                "final_checkpoint": checkpoint, "verified_rows": row_count, "integrity": "ok"}


def summaries(trials):
    result = []
    for case in CASES:
        matching = [trial for trial in trials if trial["case"]["name"] == case.name]
        if not matching:
            continue
        rates = [trial["events_per_second"] for trial in matching]
        result.append({
            **asdict(case), "repeats": len(matching),
            "events_per_second_median": statistics.median(rates),
            "events_per_second_min": min(rates), "events_per_second_max": max(rates),
            "commits_per_second_median": statistics.median(trial["commits_per_second"] for trial in matching),
            "batch_p95_ms_median": statistics.median(trial["batch_latency"]["p95_ms"] for trial in matching),
            "batch_p99_ms_median": statistics.median(trial["batch_latency"]["p99_ms"] for trial in matching),
            "commit_p50_ms_median": statistics.median(trial["commit_latency"]["p50_ms"] for trial in matching),
        })
    return result


def make_report(payload):
    machine = payload["machine"]
    config = payload["config"]
    memory = f"{machine['memory_bytes'] / 2**30:g} GiB" if machine["memory_bytes"] else "unavailable"
    lines = [
        "# Why a Tiny Database Write Can Take So Long — local measurements", "",
        f"Run ID: `{payload['run_id']}`. Status: **{payload['status']}**.", "",
        f"Machine: {machine['cpu']}; {machine['logical_cpus']} logical CPUs; {memory} RAM; "
        f"{machine['os']} {machine.get('os_version', machine['os_release'])}; {machine['architecture']}.",
        f"Runtime: {machine['python_implementation']} {machine['python']}; SQLite {machine['sqlite']}.",
        f"Storage: `{json.dumps(machine['storage'], sort_keys=True)}`.", "",
        f"Each configuration runs for at least {config['seconds']:g} seconds per trial, "
        f"with {config['repeats']} repetitions and {config['warmup']:g} seconds of warmup per trial. "
        f"Case order is shuffled each repetition (seed {config['seed']}). "
        f"fullfsync={int(config['fullfsync'])}; automatic WAL checkpoint threshold={config['checkpoint_pages']} pages.", "",
        "## Measured results", "",
        "Throughput columns show medians of trial rates. Range is min–max across trials. "
        "Latency columns show medians of each trial's nearest-rank percentiles; they are not pooled percentiles.", "",
        "| Configuration | Events/commit | Events/sec | Min–max events/sec | Commits/sec | Batch p95 ms | Batch p99 ms |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries(payload["trials"]):
        lines.append(f"| {row['name']} | {row['batch_size']:,} | {row['events_per_second_median']:,.0f} | "
                     f"{row['events_per_second_min']:,.0f}–{row['events_per_second_max']:,.0f} | "
                     f"{row['commits_per_second_median']:,.1f} | {row['batch_p95_ms_median']:.3f} | "
                     f"{row['batch_p99_ms_median']:.3f} |")
    lines += ["", "## Scope and interpretation", "",
              "One local Python process, one SQLite connection, one sequential writer, no concurrent readers. "
              "Each event has a unique text ID, integer minor-unit amount, and captured status. "
              "The schema has a primary-key index and two CHECK constraints. Each trial uses a fresh database; "
              "warmup rows remain, with a WAL checkpoint before measurement.", "",
              "Elapsed time includes event generation, Python/SQLite calls, BEGIN, inserts, COMMIT, "
              "automatic checkpoints, and instrumentation. Batch latency spans generation through commit return; "
              "commit latency measures the COMMIT call only, not an isolated disk flush. "
              "No arrival queue or batch-formation delay is modeled, so these are not end-to-end service latencies. "
              "The last full batch may run past the requested duration; its actual elapsed time is counted.", "",
              "Setup, warmup, final explicit checkpoint, close/reopen, COUNT, and integrity checks are outside "
              "the throughput timer. Final checkpoint time and per-second WAL size samples are in results.json. "
              "WAL file size is allocated length, not outstanding uncheckpointed work; sampled maximum can miss peaks. "
              "The harness does not count fsync calls or individual automatic checkpoints.", "",
              "All completed trials passed reopened row-count and integrity checks. This is a clean-shutdown "
              "check, not a power-loss test. Results characterize this short local workload, not production capacity. "
              "Faster cases grow larger databases during the fixed time budget. Python overhead, growing indexes, "
              "background work, power settings, cache state, and storage free space affect the rates.", "",
              "## Durability settings", "",
              "WAL/FULL requests a WAL sync at commit. WAL/NORMAL can lose acknowledged transactions after "
              "machine failure; WAL/OFF can also permit corruption. DELETE/FULL has filesystem-dependent durability; "
              "DELETE/EXTRA adds directory synchronization. These contracts assume a working storage stack. "
              "On macOS the default harness profile enables fullfsync to request F_FULLFSYNC; "
              "the recorded PRAGMA is not proof that the hardware honored it. "
              "See [SQLite synchronous](https://www.sqlite.org/pragma.html#pragma_synchronous) and "
              "[fullfsync](https://www.sqlite.org/pragma.html#pragma_fullfsync).", "",
              "## Credit", "",
              "Inspired by Tanay Karnik / Foo Community, *The Physics of Database Speed: from 300 to 1M "
              "transactions per second*. This payment-event workload is an independent experiment. "
              "These are local measurements, not a reproduction of the video's account-transfer benchmark.", ""]
    return "\n".join(lines)


def save_results(folder, payload):
    temporary = folder / "results.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(folder / "results.json")
    (folder / "report.md").write_text(make_report(payload))
    summary = summaries(payload["trials"])
    if summary:
        with (folder / "summary.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary[0]))
            writer.writeheader()
            writer.writerows(summary)


def positive_seconds(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than zero")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=positive_seconds, default=5, help="minimum measured seconds per trial (default: 5)")
    parser.add_argument("--repeats", type=positive_int, default=3)
    parser.add_argument("--warmup", type=positive_seconds, default=0.25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cases", nargs="+", choices=[case.name for case in CASES])
    parser.add_argument("--checkpoint-pages", type=positive_int, default=1000)
    parser.add_argument("--fullfsync", choices=("auto", "on", "off"), default="auto")
    parser.add_argument("--data-dir", type=Path, default=ROOT / ".bench-data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--notes", default="", help="power state, background workload, or other experiment context")
    args = parser.parse_args(argv)
    args.fullfsync = args.fullfsync == "on" or (args.fullfsync == "auto" and sys.platform == "darwin")
    return args


def main(argv=None):
    args = parse_args(argv)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = args.output_dir / run_id
    folder.mkdir()
    cases = [case for case in CASES if not args.cases or case.name in args.cases]
    config = {key: value for key, value in vars(args).items() if key not in ("data_dir", "output_dir")}
    config["cases"] = [case.name for case in cases]
    git_status = command_output(["git", "-C", str(ROOT), "status", "--porcelain"])
    payload = {
        "format_version": 1, "run_id": run_id, "status": "running", "config": config,
        "machine": machine_info(args.data_dir), "trials": [],
        "source": {"benchmark_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   "git_commit": command_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
                   "git_dirty": bool(git_status) if git_status is not None else None},
        "schema": SCHEMA.strip(),
    }
    save_results(folder, payload)
    print(f"Run {run_id}: {len(cases)} cases × {args.repeats} repetitions; fullfsync={int(args.fullfsync)}", flush=True)
    print(f"Artifacts: {folder}", flush=True)
    rng = random.Random(args.seed)
    try:
        for repeat in range(1, args.repeats + 1):
            order = cases.copy()
            rng.shuffle(order)
            for case in order:
                print(f"[{repeat}/{args.repeats}] {case.name}: measuring…", flush=True)
                trial = run_trial(case, args, repeat, len(payload["trials"]) + 1)
                payload["trials"].append(trial)
                save_results(folder, payload)
                print(f"  {trial['events_per_second']:,.0f} events/s; {trial['commits_per_second']:,.1f} commits/s; "
                      f"batch p99 {trial['batch_latency']['p99_ms']:.3f} ms; integrity ok", flush=True)
    except BaseException as error:
        payload["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        payload["error"] = f"{type(error).__name__}: {error}"
        save_results(folder, payload)
        raise
    payload["status"] = "complete"
    payload["finished_at"] = datetime.now(timezone.utc).isoformat()
    save_results(folder, payload)
    print(f"Complete: {folder / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()

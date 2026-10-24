import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path

import benchmark


class BenchmarkTests(unittest.TestCase):
    def test_every_mode_counts_commits_and_verifies_reopened_database(self):
        with tempfile.TemporaryDirectory() as folder:
            args = argparse.Namespace(data_dir=Path(folder), fullfsync=False,
                                      checkpoint_pages=10, warmup=0.001, seconds=0.002)
            for case in benchmark.CASES:
                with self.subTest(case=case.name):
                    result = benchmark.run_trial(case, args, repeat=1, order=1)
                    self.assertEqual(result["events"], result["commits"] * case.batch_size)
                    self.assertEqual(result["verified_rows"], result["events"] + result["warmup"]["events"])
                    self.assertEqual(result["batch_latency"]["count"], result["commits"])
                    self.assertGreaterEqual(result["elapsed_seconds"], args.seconds)
                    self.assertAlmostEqual(result["events_per_second"], result["commits_per_second"] * case.batch_size)
                    self.assertEqual(result["integrity"], "ok")
                    self.assertEqual(result["pragmas"]["journal_mode"], case.journal.lower())
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_failed_batch_rolls_back_all_its_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.sqlite3"
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                case = benchmark.Case("test", "WAL", "FULL", 2)
                benchmark.configure(connection, case, False, 1000)
                connection.execute(benchmark.INSERT, ("evt_000000000001", 2500, "captured"))
                with self.assertRaises(sqlite3.IntegrityError):
                    benchmark.write_for(connection, path, case, 0.001)
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute("SELECT event_id FROM payment_events").fetchall(), [("evt_000000000001",)])
            finally:
                connection.close()

    def test_nearest_rank_percentiles_and_summary_units(self):
        result = benchmark.latency_summary([number * 1_000_000 for number in range(1, 101)])
        self.assertEqual(result["p50_ms"], 50)
        self.assertEqual(result["p95_ms"], 95)
        self.assertEqual(result["p99_ms"], 99)
        case = benchmark.CASES[-1]
        trials = [{"case": benchmark.asdict(case), "events_per_second": rate,
                   "commits_per_second": rate / case.batch_size,
                   "batch_latency": {"p95_ms": 2, "p99_ms": 3},
                   "commit_latency": {"p50_ms": 1}} for rate in (28000, 84000, 56000)]
        summary = benchmark.summaries(trials)[0]
        self.assertEqual(summary["events_per_second_median"], 56000)
        self.assertEqual(summary["commits_per_second_median"], 2)

    def test_rejects_nonfinite_and_nonpositive_durations(self):
        for value in ("nan", "inf", "-1", "0"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                benchmark.positive_seconds(value)


if __name__ == "__main__":
    unittest.main()

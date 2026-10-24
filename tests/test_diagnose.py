import argparse
import shutil
import sqlite3
import unittest

import diagnose


@unittest.skipUnless(shutil.which("c++"), "A C++ compiler is required for native comparisons")
class NativeComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native, _ = diagnose.native_library()

    def test_native_and_python_accounting_use_same_sqlite(self):
        args = argparse.Namespace(seconds=0.001, warmup=0.001, batch_size=100)
        for name in ("python-original", "cpp-original", "cpp-without-rowid-values100"):
            with self.subTest(name=name):
                result = diagnose.trial(name, args, self.native)
                self.assertEqual(result["sqlite"], sqlite3.sqlite_version)
                self.assertEqual(result["events"], result["commits"] * args.batch_size)
                self.assertGreaterEqual(result["elapsed_seconds"], args.seconds)
                self.assertGreater(result["generation_seconds"], 0)
                self.assertGreater(result["insert_seconds"], 0)
                self.assertGreater(result["commit_seconds"], 0)
                self.assertEqual(result["integrity"], "ok")

    def test_rejects_partial_sql_statement_batches(self):
        args = argparse.Namespace(seconds=0.001, warmup=0.001, batch_size=101)
        with self.assertRaisesRegex(ValueError, "divisible"):
            diagnose.trial("cpp-without-rowid-values100", args, self.native)

    def test_status_check_rewrite_preserves_accepted_values(self):
        original = diagnose.benchmark.SCHEMA
        optimized = original.replace("status IN ('captured', 'refunded', 'failed')",
                                     "status = 'captured' OR status = 'refunded' OR status = 'failed'")
        for schema in (original, optimized):
            with self.subTest(schema=schema):
                connection = sqlite3.connect(":memory:", isolation_level=None)
                try:
                    connection.executescript(schema)
                    for status in ("captured", "refunded", "failed"):
                        connection.execute(diagnose.benchmark.INSERT, (status, 2500, status))
                    for status in (None, "", "CAPTURED", "pending", 0):
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(diagnose.benchmark.INSERT, ("invalid", 2500, status))
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(diagnose.benchmark.INSERT, ("bad-amount", 0, "captured"))
                finally:
                    connection.close()


if __name__ == "__main__":
    unittest.main()

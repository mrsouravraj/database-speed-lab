# Recorded experiments

All numbers here come from actual runs on the local Apple M3 Pro. Reports include their durations, repetition counts, SQLite versions, and hardware/configuration metadata. Background applications were not controlled. Rates from different run IDs should not be treated as simultaneous controlled measurements.

| Run | Purpose | Protocol |
|---|---|---|
| [Initial matrix](20261023T124336.326987Z/report.md) | DELETE/WAL, synchronization settings, batching | 9 cases × 3 trials × 5 seconds |
| [Longer Python run](20261023T124734.578770Z/report.md) | WAL/FULL, 1,000 events/commit | 3 trials × 30 seconds |
| [Native smoke check](diagnosis-20261023T125558.145516Z/diagnosis.md) | Verify C++ integration | Tiny correctness run; not performance evidence |
| [First diagnosis](diagnosis-20261023T125843.892027Z/diagnosis.md) | Driver, storage layout, statement batching, flush controls | 8 cases × 3 trials × 5 seconds |
| [Constraint diagnosis](diagnosis-20261023T130504.220743Z/diagnosis.md) | Equivalent status-check rewrite and driver comparison | 6 cases × 3 trials × 5 seconds |
| [Longer native run](diagnosis-20261024T103544.282082Z/diagnosis.md) | Optimized C++, 28,000 events/commit | 3 trials × 30 seconds |
| [Larger-batch validation](diagnosis-20261024T103845.312724Z/diagnosis.md) | Optimized C++, 112,000 events/commit; all trials above 1M events/sec | 3 trials × 30 seconds |

`results.json` / `diagnosis.json` retain per-trial data. Human-readable reports round figures; machine-readable files retain precision. Initial and diagnostic timers differ slightly in instrumentation, so use the Python control within each diagnostic run when comparing the drivers.

The benchmark's numerator is inserted payment events. Database commits are counted separately. No experiment here tests actual power-loss recovery.

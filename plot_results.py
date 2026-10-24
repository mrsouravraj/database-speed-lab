#!/usr/bin/env python3
"""Render article figures from an existing run; requires optional matplotlib."""

import argparse
import json
import statistics
from pathlib import Path

from benchmark import summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="path to results.json")
    args = parser.parse_args()
    payload = json.loads(args.results.read_text())
    if payload["status"] != "complete":
        parser.error("Only completed runs can be plotted")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import FuncFormatter, LogLocator
    except ImportError:
        parser.exit(1, "Plotting needs matplotlib: python3 -m pip install matplotlib\n")

    diagnosis = "case" not in payload["trials"][0]
    if diagnosis:
        rows = []
        for name in payload["config"]["variants"]:
            trials = [trial for trial in payload["trials"] if trial["name"] == name]
            rates = [trial["events_per_second"] for trial in trials]
            rows.append({"name": name, "batch_size": trials[0]["batch_size"],
                         "synchronous": trials[0]["synchronous"],
                         "events_per_second_median": statistics.median(rates),
                         "events_per_second_min": min(rates), "events_per_second_max": max(rates),
                         "commits_per_second_median": statistics.median(trial["commits_per_second"] for trial in trials)})
        display_names = {
            "python-original": "Python · original schema",
            "cpp-original": "C++ · original schema",
            "cpp-original-or-check": "C++ · equivalent CHECK",
            "python-without-rowid-or-check": "Python · CHECK + WITHOUT ROWID",
            "cpp-without-rowid-or-check": "C++ · CHECK + WITHOUT ROWID",
            "cpp-without-rowid-or-check-values1000": "C++ · above + 1,000-row statements",
        }
        labels = [display_names.get(row["name"], row["name"]) for row in rows]
    else:
        rows = summaries(payload["trials"])
        labels = [f"{row['journal']} / {row['synchronous']} · {row['batch_size']:,} per commit" for row in rows]
    colors = [{"FULL": "#247b75", "EXTRA": "#247b75", "NORMAL": "#bb861f", "OFF": "#bf544b"}[row["synchronous"]]
              for row in rows]
    figure, axes = plt.subplots(1, 2, figsize=(13, 7), sharey=True, gridspec_kw={"width_ratios": [1.3, 1]})
    figure.set_facecolor("#faf9f6")
    for axis, metric, title in zip(axes, ("events_per_second_median", "commits_per_second_median"),
                                   ("Payment events / second", "Database commits / second")):
        values = [row[metric] for row in rows]
        divisors = [1 if metric.startswith("events") else row["batch_size"] for row in rows]
        minimums = [row["events_per_second_min"] / divisor for row, divisor in zip(rows, divisors)]
        maximums = [row["events_per_second_max"] / divisor for row, divisor in zip(rows, divisors)]
        axis.set_facecolor("#faf9f6")
        axis.barh(range(len(rows)), values, color=colors, height=0.58)
        axis.errorbar(values, range(len(rows)),
                      xerr=[[value - minimum for value, minimum in zip(values, minimums)],
                            [maximum - value for value, maximum in zip(values, maximums)]],
                      fmt="none", ecolor="#263c3b", capsize=3, linewidth=1)
        axis.set_xscale("log")
        axis.set_xlim(1, max(values) * 8)
        axis.set_title(title, loc="left", fontsize=13, pad=16)
        axis.grid(axis="x", which="major", alpha=0.15)
        axis.set_axisbelow(True)
        axis.xaxis.set_major_formatter(FuncFormatter(lambda value, position: f"{value:,.0f}"))
        axis.xaxis.set_major_locator(LogLocator(base=10, numticks=4))
        axis.tick_params(axis="both", length=0, labelsize=10)
        for spine in axis.spines.values():
            spine.set_visible(False)
        for index, value in enumerate(values):
            label = f"{value:,.0f}" if metric.startswith("events") else f"{value:,.1f}"
            axis.text(maximums[index] * 1.12, index, label, va="center", fontsize=10)
    axes[0].set_yticks(range(len(rows)), labels)
    axes[0].invert_yaxis()
    title = "Following the bottleneck to one million events" if diagnosis else "A small write, a very different commit cost"
    figure.suptitle(title, x=0.03, y=0.98, ha="left", fontsize=22, weight="bold")
    config = payload["config"]
    subtitle = (f"{payload['machine']['cpu']}  |  SQLite {payload['machine']['sqlite']}  |  "
                f"Median of {config['repeats']} × {config['seconds']:g}s trials  |  "
                + (f"{config['batch_size']:,} events / commit" if diagnosis else f"fullfsync={int(config['fullfsync'])}"))
    figure.text(0.03, 0.91, subtitle, fontsize=11, color="#444444")
    figure.text(0.03, 0.055, "Logarithmic axes. Bars: medians; whiskers: trial min–max. Teal: FULL/EXTRA; gold: NORMAL; red: OFF.", fontsize=10)
    report_name = "diagnosis.md" if diagnosis else "report.md"
    figure.text(0.03, 0.025, f"Single writer; includes automatic checkpoints. Trial ranges and methodology: {report_name}. Run {payload['run_id']}", fontsize=9)
    figure.subplots_adjust(left=0.32 if diagnosis else 0.29, right=0.97, top=0.82, bottom=0.16, wspace=0.12)
    for extension in ("png", "svg"):
        output = args.results.parent / f"throughput.{extension}"
        figure.savefig(output, dpi=180, facecolor=figure.get_facecolor())
        print(output)
    plt.close(figure)


if __name__ == "__main__":
    main()

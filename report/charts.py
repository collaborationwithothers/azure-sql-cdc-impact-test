"""PNG charts: value against load, one line per configuration (and run when several).

Colours follow the configuration, never its rank: A blue, B orange, C aqua
(the dataviz reference palette, slots 1 to 3). Each line also has its own
marker shape, so identity never rests on colour alone. One y-axis per panel.
"""
from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .analysis import StepMetrics  # noqa: E402

COLOURS = {"A": "#2a78d6", "B": "#eb6834", "C": "#1baf7a"}
MARKERS = {"A": "o", "B": "s", "C": "^"}
INK, MUTED, GRID = "#1f1f1e", "#6b6a64", "#e4e3de"


def _series(metrics: list[StepMetrics], fn, multi_run: bool):
    """{label: (configuration, [(tps, mean, low, high)])}, spread across repeats as low..high."""
    groups: dict[tuple[str, str], dict[int, list[float]]] = {}
    for m in metrics:
        v = fn(m)
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        groups.setdefault((m.step.run_id, m.step.configuration), {}).setdefault(m.step.step_tps, []).append(v)
    out = {}
    for (run_id, c), pts in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        label = f"{c} (run {run_id})" if multi_run else c
        out[label] = (c, [(t, sum(v) / len(v), min(v), max(v)) for t, v in sorted(pts.items())])
    return out


def _panel(ax, series, title: str, ylabel: str, ref: float | None = None):
    for label, (c, pts) in series.items():
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, color=COLOURS.get(c, INK), marker=MARKERS.get(c, "o"), markersize=8, linewidth=2, label=label)
        if any(p[3] > p[2] for p in pts):
            ax.fill_between(xs, [p[2] for p in pts], [p[3] for p in pts], color=COLOURS.get(c, INK), alpha=0.15, linewidth=0)
        if pts and len(series) == 1:  # several series get a legend; direct labels collide where lines coincide
            ax.annotate(label, (xs[-1], ys[-1]), textcoords="offset points", xytext=(6, 0), fontsize=8, color=INK, va="center")
    if ref is not None:
        ax.axhline(ref, color=MUTED, linewidth=1, linestyle="--")
        ax.annotate(f"{ref:g}% (limit reached)", (0, ref), xycoords=("axes fraction", "data"), textcoords="offset points",
                    xytext=(4, 3), fontsize=8, color=MUTED)
    ax.set_title(title, fontsize=10, color=INK, loc="left")
    ax.set_xlabel("database 1 target load (transactions per second)", fontsize=9, color=MUTED)
    ax.set_ylabel(ylabel, fontsize=9, color=MUTED)
    ax.grid(True, color=GRID, linewidth=0.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.set_ylim(bottom=0)
    if ref is not None:
        ax.set_ylim(top=max(105.0, ax.get_ylim()[1]))
    if len(series) >= 2:
        ax.legend(fontsize=8, frameon=False)


def _save(fig, path: Path) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=110, facecolor="white")
    plt.close(fig)
    return path.name


def _pct(scope: str, col: str):
    def f(m: StepMetrics):
        st = (m.db1 if scope == "db" else m.pool).get(col)
        return st.mean if st else None
    return f


def render(metrics: list[StepMetrics], out: Path) -> list[tuple[str, str]]:
    """Writes the charts; returns [(caption, file name)]."""
    multi = len({m.step.run_id for m in metrics}) > 1
    made = []
    for col, name in (("avg_cpu_percent", "CPU"), ("avg_data_io_percent", "data IO"),
                      ("avg_log_write_percent", "log write"), ("max_worker_percent", "workers")):
        fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
        _panel(axes[0], _series(metrics, _pct("db", col), multi), f"Database 1 {name}, % of database limit", "percent", 90)
        _panel(axes[1], _series(metrics, _pct("pool", col), multi), f"Pool {name}, % of pool limit", "percent", 90)
        made.append((f"{name}, database 1 and pool (measured)", _save(fig, out / f"{col}.png")))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    _panel(axes[0], _series(metrics, lambda m: m.step.p50_ms, multi), "Latency p50, database 1", "milliseconds")
    _panel(axes[1], _series(metrics, lambda m: m.step.p99_ms, multi), "Latency p99, database 1", "milliseconds")
    made.append(("Latency, database 1 (measured)", _save(fig, out / "latency.png")))
    fig, ax = plt.subplots(figsize=(6, 4))
    _panel(ax, _series(metrics, lambda m: m.step.achieved, multi), "Achieved against target, database 1", "transactions per second")
    xs = sorted({m.step.step_tps for m in metrics})
    ax.plot(xs, xs, color=MUTED, linewidth=1, linestyle="--")
    ax.plot(xs, [0.95 * x for x in xs], color=MUTED, linewidth=1, linestyle=":")
    ax.annotate("target", (xs[-1], xs[-1]), textcoords="offset points", xytext=(-30, 6), fontsize=8, color=MUTED)
    made.append(("Achieved against target; dotted line is 95% of target (measured)", _save(fig, out / "achieved.png")))
    lag = _series(metrics, lambda m: None if math.isnan(m.step.debezium_lag_ms) else m.step.debezium_lag_ms, multi)
    if lag:
        fig, ax = plt.subplots(figsize=(6, 4))
        _panel(ax, lag, "Debezium MilliSecondsBehindSource at step end", "milliseconds")
        made.append(("Debezium lag, configuration C, one reading at the end of each step (measured)", _save(fig, out / "debezium_lag.png")))
    nb = _series(metrics, lambda m: m.neighbour.get("p99_ms") if m.neighbour.get("n") else None, multi)
    if nb:
        fig, axes = plt.subplots(1, 2, figsize=(11, 4))
        _panel(axes[0], nb, "Database 2 latency p99 (mean of 10 s windows)", "milliseconds")
        _panel(axes[1], _series(metrics, lambda m: m.neighbour.get("achieved") if m.neighbour.get("n") else None, multi),
               "Database 2 achieved rate", "transactions per second")
        made.append(("Neighbour impact: database 2 against database 1 load (measured)", _save(fig, out / "neighbour.png")))
    fig, ax = plt.subplots(figsize=(6, 4))
    _panel(ax, _series(metrics, lambda m: m.step.client_cpu_percent, multi), "Load generator CPU, % of all client cores", "percent")
    made.append(("Client-side proof: load generator CPU (measured)", _save(fig, out / "client_cpu.png")))
    return made

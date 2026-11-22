"""SUMMARY.md: the results in plain words, for a reader with no context.

README.md is the technical appendix. This file restates its CDC comparison
without the appendix's vocabulary: "the application database" (where CDC is
switched on), "the neighbouring database" (sharing the pool), "without CDC"
(configuration B) and "with CDC" (configuration C), "round" for a repeat and
"run-to-run variation" for jitter. Configuration A is not shown.

Every figure comes from the functions README.md uses: means over the rounds
both sides ran (analysis.paired, config_value), the uplift rule
(analysis.uplift), paired ranges (analysis.paired_diffs), change-table
retention (storage), and the monthly total the storage section computed
(render.Report.monthly_total). Build the Report first; this reads its state.
"""
from __future__ import annotations

import math
from collections import defaultdict

from . import analysis as A
from . import prices as P
from . import storage as ST
from .load import num
from .render import Report, f, table

WITHOUT, WITH = "B", "C"
OVERLOADED = "overloaded: compares two overloaded systems, not the steady cost of CDC"
REGIONS = {"swedencentral": "Sweden Central"}
LIMIT_NAMES = {"CPU": "CPU limit", "data IO": "data read and write (IO) limit", "log write": "log write limit",
               "workers": "worker thread limit", "sessions": "session limit"}
SCOPE_NAMES = {"database 1": "the database's", "pool": "the pool's"}
GLOSSARY = ("Glossary: **vCore**, a virtual CPU core, the unit Azure SQL sizes and prices compute in. "
            "**Elastic pool**, a group of databases that share one set of compute, memory and log limits. "
            "**CDC** (change data capture), SQL Server's built-in change log. "
            "**Debezium**, open-source software that reads CDC's change table and publishes each change as an event. "
            "**Outbox**, a table the application writes its outgoing events to in the same transaction as the business "
            "change, so an event exists exactly when the change does. "
            "**95th percentile**, the response time that 95 out of 100 requests beat.")


def _log_kb(m: A.StepMetrics):
    v, samples = A.UPLIFT_METRICS[ST.LOG_METRIC][1](m)
    return v / 1000, [x / 1000 for x in samples]


POOL_LOG = "Pool log write rate (% of the pool's limit)"
POOL_WAIT = "Time held back by the pool's log write limit (ms per second)"


def _pool_pct(m: A.StepMetrics, col: str):
    st = m.pool.get(col)
    return (st.mean, st.samples) if st else (math.nan, [])


# (column name, function(StepMetrics) -> (value, within-window samples), decimals)
COMPARED = (
    ("Log written per update (KB)", _log_kb, 1),
    ("CPU used (% of the database's limit)", lambda m: A._pct(m, "avg_cpu_percent"), 1),
    ("Log write rate (% of the database's limit)", lambda m: A._pct(m, "avg_log_write_percent"), 1),
    ("Response time, 95th percentile (ms)", lambda m: (m.step.p95_ms, []), 2),
    ("Data read and write (IO) used (% of the database's limit)", lambda m: A._pct(m, "avg_data_io_percent"), 1),
    (POOL_LOG, lambda m: _pool_pct(m, "avg_log_write_percent"), 1),
    (POOL_WAIT, lambda m: (m.pool_waits.get("POOL_LOG_RATE_GOVERNOR", math.nan), []), 0),
)
NEIGHBOUR = ("Neighbouring database response time, 95th percentile (ms)",
             lambda m: (m.neighbour.get("p95_ms", math.nan), []), 2)
_BY_NAME = {c[0]: c for c in COMPARED + (NEIGHBOUR,)}
# Table order for the reader: what users feel first, then the causes, then the neighbour.
METRIC_ORDER = tuple(_BY_NAME[n] for n in (
    "Response time, 95th percentile (ms)", "Log written per update (KB)",
    "Log write rate (% of the database's limit)", POOL_LOG, POOL_WAIT,
    "Data read and write (IO) used (% of the database's limit)", "CPU used (% of the database's limit)",
    NEIGHBOUR[0]))
OVERLOADED_SHORT = "overloaded, see note"
EXPLAIN = {
    "handled": ("Whether the application database kept up with the requested updates per second: at least 95% of "
                "them done, with no errors and nothing dropped. A \"no\" means that load is too much for this setup."),
    "Response time, 95th percentile (ms)": (
        "How long the slowest 5 in 100 updates took, in milliseconds; this is the delay the API's users notice when "
        "the system is busy."),
    "Log written per update (KB)": (
        "How much transaction log, the database's record of every change used for recovery and backups, each update "
        "writes; CDC can add to it, because copying each change into the change table is itself a logged write."),
    "Log write rate (% of the database's limit)": (
        "How close the application database is to its cap on log writing speed; near 100% the database holds "
        "updates back until the log catches up, so they slow down."),
    POOL_LOG: (
        "How close the whole pool is to its shared cap on log writing speed. CDC's own copying into the change table "
        "runs as an internal SQL process, so its log writes count here, at pool level, and not in the database's "
        "own log write rate above."),
    POOL_WAIT: (
        "For each second of the measured window, how many milliseconds of waiting, summed over all waiting tasks in "
        "the pool, were spent held back by the pool's log write limit (wait type POOL_LOG_RATE_GOVERNOR). 0 means "
        "the limit never held anything back. Several tasks can wait at once, so the figure can exceed 1,000."),
    "Data read and write (IO) used (% of the database's limit)": (
        "How close the application database is to its cap on reading and writing data pages; near 100% updates wait "
        "for the disk."),
    "CPU used (% of the database's limit)": (
        "How much of the application database's processing allowance (its vCore cap) the updates used; near 100% "
        "the database cannot work any faster."),
    NEIGHBOUR[0]: (
        "How long the other database in the same pool took for its own steady updates; it shows whether switching "
        "on CDC in one database slows down its neighbours."),
    "storage": (
        "How much space CDC's change table takes when changes are kept for the default 3 days, and an estimate of "
        "what CDC adds to the monthly bill."),
}


def handled(ms: list[A.StepMetrics]) -> str:
    if not ms:
        return "not measured"
    bad = [m for m in ms if not m.step.carries]
    if not bad:
        return "yes"
    worst = min(bad, key=lambda m: -1 if math.isnan(m.step.ratio) else m.step.ratio).step
    errs = sum(m.step.errors for m in bad if not math.isnan(m.step.errors))
    shed = sum(m.step.shed for m in bad if not math.isnan(m.step.shed))
    text = f"no: {f(worst.achieved)} of {f(worst.target, 0)} per second, {f(errs, 0)} errors"
    if shed:
        text += f", {f(shed, 0)} dropped"
    if len(bad) < len(ms):
        text += " (round " + ", ".join(str(m.step.repeat) for m in bad) + ")"
    return text


def added(vc: A.Value, vb: A.Value, per: dict[int, float], digits: int) -> tuple[str, A.Uplift | None]:
    """With minus without under the uplift rule, with the lowest and highest per-round difference in brackets."""
    if math.isnan(vc.value) or math.isnan(vb.value):
        return "not measured", None
    u = A.uplift(vc, vb)
    if u.suppressed == "difference within jitter":
        return "within run-to-run variation", u
    if u.suppressed:
        return "variation unknown, no figure", u
    vals = list(per.values())
    spread = f" [{f(min(vals), digits)} to {f(max(vals), digits)}]" if len(vals) > 1 else " [one round]"
    return f"{'+' if u.absolute >= 0 else ''}{f(u.absolute, digits)}{spread}", u


def limit_name(b: A.Binding) -> str:
    return f"{SCOPE_NAMES.get(b.scope, 'the')} {LIMIT_NAMES.get(b.limit, b.limit)}"


class Summary:
    def __init__(self, report: Report):
        self.r = report
        self.lines: list[str] = []

    def w(self, *lines: str) -> None:
        self.lines.extend(lines)

    def build(self) -> str:
        self.w("# What switching on CDC costs: plain-language summary", "")
        for run in self.r.runs:
            self.run_block(run)
        self.w(GLOSSARY, "")
        return "\n".join(self.lines).rstrip() + "\n"

    # -- per run -------------------------------------------------------------

    def opening(self, run, rounds_without: int, rounds_with: int) -> None:
        s = run.settings
        a = self.r.applied.get(run.run_id)
        env = next((row for rec in run.get("environment") for row in (rec.get("data") or {}).get("rows", [])
                    if row.get("database_name") == s.get("database1")), {})
        edition = {"GeneralPurpose": "General Purpose"}.get(env.get("edition"), env.get("edition") or "unknown tier")
        pool = f"{a.pool.vcores} vCores" if a and a.pool else "unknown size"
        caps = {row.get("database_name"): num(row.get("cpu_limit")) for rec in run.get("governance")
                for row in (rec.get("data") or {}).get("rows", [])}
        c1, c2 = caps.get(s.get("database1"), math.nan), caps.get(s.get("database2"), math.nan)
        if not math.isnan(c1) and c1 == c2:
            capped = f"Each database is capped at {f(c1, 0)} vCores"
        elif not math.isnan(c1):
            capped = f"The application database is capped at {f(c1, 0)} vCores"
        else:
            capped = "The per-database cap was not recorded"
        seeded = next((r.get("data") or {} for r in run.get("seed")
                       if (r.get("data") or {}).get("database") == s.get("database1")), {})
        gib = seeded.get("reserved_bytes")
        data = (f"{f(gib / 2**30, 1)} GiB of task data" if gib else
                f"a target of {s.get('seedGb', 'an unknown amount of')} GiB of task data")
        chars = s.get("payloadChars")
        event = (f"a JSON event of {chars:,} characters (about {f(chars * 2 / 1000, 0)} KB as stored, because the "
                 f"column type {s.get('payloadType')} uses 2 bytes per character)") if chars else "a JSON event"
        warm, meas = s.get("warmupSeconds"), s.get("measureSeconds")
        def span(x, adjective=False):
            if not x:
                return "an unknown time" if not adjective else "an unknown"
            n, unit = (x // 60, "minute") if x % 60 == 0 else (x, "second")
            return f"{n}-{unit}" if adjective else f"{n} {unit}{'s' if n != 1 else ''}"

        rounds = (f"{rounds_without} round{'s' if rounds_without != 1 else ''} without CDC and {rounds_with} with CDC"
                  if rounds_without else f"{rounds_with} round{'s' if rounds_with != 1 else ''} with CDC only (this "
                  "run did not test the application without CDC)")
        self.w(f"Run {run.run_id}.", "",
               f"We tested an API that updates a task record and, in the same transaction, saves {event} to an outbox "
               f"table. The databases sit in an Azure SQL elastic pool of {pool}, {edition}, zone redundant, in "
               f"{REGIONS.get(P.REGION, P.REGION)}. {capped}; the application database holds "
               f"{data}, and the neighbouring database in the same pool takes a steady "
               f"{s.get('database2Rate', 'unknown')} updates per second throughout. "
               "CDC is SQL Server's built-in change log: it copies every change to the outbox into a change table, "
               "so that a reader, here Debezium, can publish it. "
               f"Each load level ran for {span(meas)} after a {span(warm, True)} warm-up, in "
               f"{rounds}.", "")

    def run_block(self, run) -> None:
        rid = run.run_id
        ms = [m for m in self.r.run_metrics(rid) if m.step.configuration in (WITHOUT, WITH)]
        by: dict[tuple[str, int], list[A.StepMetrics]] = defaultdict(list)
        for m in ms:
            by[(m.step.configuration, m.step.step_tps)].append(m)
        rw = len({m.step.repeat for m in ms if m.step.configuration == WITHOUT})
        rc = len({m.step.repeat for m in ms if m.step.configuration == WITH})
        self.opening(run, rw, rc)
        if not ms:
            self.w("No load level was measured with or without CDC in this run.", "")
            return
        ct = ST.bytes_per_row(ST.change_rows(run, self.r.steps.get(rid, [])))
        retention_s = ST.CDC_RETENTION_MINUTES * 60
        # One entry per load: the cells of every table, computed once.
        loads, best = [], None
        for t in sorted({m.step.step_tps for m in ms}):
            mb, mc, shared = A.paired(by.get((WITHOUT, t), []), by.get((WITH, t), []))
            if not shared:  # only one side ran at this load: show what it has
                mb, mc = by.get((WITHOUT, t), []), by.get((WITH, t), [])
            both = bool(mb and mc)
            over = both and not all(m.step.carries for m in mb + mc)
            if both and not over:
                best = (t, mb, mc, shared)
            cells = {"handled": [handled(mb), handled(mc)]}
            for name, fn, d in COMPARED + (NEIGHBOUR,):
                vb = A.config_value(mb, fn) if mb else A.Value(math.nan, math.nan)
                vc = A.config_value(mc, fn) if mc else A.Value(math.nan, math.nan)
                cells[name] = [f(vb.value, d), f(vc.value, d), added(vc, vb, A.paired_diffs(mc, mb, fn, shared), d)[0]]
            rng = ct.get(t)
            cells["storage"] = [f(ST.retained_bytes(rng.mean, t, retention_s) / ST.GB, 2) if rng and rng.n
                                else "not measured", self.monthly_cell(t, both)]
            cells = {k: ["not measured" if c == "n/a" else c for c in v] for k, v in cells.items()}
            loads.append((t, over, cells))

        self.w("## What this means", "", *self.meaning(rid, by, best, ct, retention_s), "")
        self.w("## The measurements", "",
               "How to read the tables. Each figure is the average over the rounds that ran both without and with CDC "
               "(where only one side ran, over its own rounds). \"Added by CDC\" is with minus without; the brackets "
               "give its lowest and highest value in a single round. \"Within run-to-run variation\" means the "
               "difference is not larger than how much the same setup varied from round to round, so it cannot be "
               "told apart from chance. KB = 1,000 bytes, GB = 10^9 bytes.", "")
        load_col = "Load (updates per second)"
        mark = lambda t, over: f"{t:,}" + (f" ({OVERLOADED_SHORT})" if over else "")
        any_over = any(over for _, over, _ in loads)

        self.w("### Handled the load?", "", EXPLAIN["handled"], "")
        self.w(*table([load_col, "Without CDC", "With CDC", "Note"],
                      [[f"{t:,}", *c["handled"], OVERLOADED_SHORT if over else ""] for t, over, c in loads]))
        if any_over:
            self.w(f"Note: {OVERLOADED}. At such a load at least one side could not keep up, so its figures describe "
                   "a system under strain.", "")
        for name, _, _ in METRIC_ORDER:
            self.w(f"### {name}", "", EXPLAIN[name], "")
            self.w(*table([load_col, "Without CDC", "With CDC", "Added by CDC"],
                          [[mark(t, over), *c[name]] for t, over, c in loads]))
        self.w("### Storage and cost", "", EXPLAIN["storage"], "")
        self.w(*table(["Load", f"Change table kept for {ST.CDC_RETENTION_MINUTES // 1440} days (GB)",
                       f"Extra monthly cost ({self.r.currency or 'USD'})"],
                      [[mark(t, over), *c["storage"]] for t, over, c in loads]))
        self.w("## How sure are we", "", *self.certainty(run, best), "")

    def monthly_cell(self, t: int, both: bool) -> str:
        if not both:
            return "not measured"
        if not self.r.price_data:
            return "not priced (prices were not read)"
        total = self.r.monthly_total.get(t, math.nan)
        return f"{f(total, 2)} (estimate)" if not math.isnan(total) else "not available"

    # -- bullets ---------------------------------------------------------------

    def meaning(self, rid, by, best, ct, retention_s) -> list[str]:
        out = []
        if best is None:
            out.append(f"- Added cost of CDC: not measured, because no load level was both measured and handled "
                       f"without and with CDC (run {rid}).")
        else:
            t, mb, mc, shared = best
            shown, quiet = [], []
            for name, fn, d in COMPARED:
                cell, u = added(A.config_value(mc, fn), A.config_value(mb, fn), A.paired_diffs(mc, mb, fn, shared), d)
                label = name.split(" (")[0]
                label = label if label.startswith("CPU") else label[0].lower() + label[1:]
                if u is None or u.suppressed:
                    quiet.append(f"{label} {cell}")
                    continue
                unit = name.split(" (")[1].rstrip(")")
                unit = ("percentage points" + unit[1:]) if unit.startswith("%") else unit
                pct = f", {'+' if u.percent >= 0 else ''}{f(u.percent, 0)}%" if not math.isnan(u.percent) else ""
                shown.append(f"{label} {cell.split(' [')[0]} {unit}{pct}")
            text = (f"- At {t:,} updates per second, the highest load handled both without and with CDC, CDC added: "
                    + ("; ".join(shown) if shown else "nothing larger than run-to-run variation"))
            if quiet:
                text += ". Not distinguishable: " + "; ".join(quiet)
            out.append(text + f" (run {rid}).")
        for side, words in ((WITHOUT, "Without CDC"), (WITH, "With CDC")):
            loads = sorted(t for (c, t) in by if c == side)
            if not loads:
                out.append(f"- {words}: not measured (run {rid}).")
                continue
            bad = [t for t in loads if not all(m.step.carries for m in by[(side, t)])]
            if not bad:
                out.append(f"- {words}, the database handled every load tested, up to {loads[-1]:,} updates per second "
                           f"(run {rid}).")
                continue
            group = by[(side, bad[0])]
            reached = [m.binding for m in group if m.binding.reached]
            if reached:
                b = max(reached, key=lambda x: x.percent)
                why = f"{limit_name(b)} was hit ({f(b.percent, 0)}% of it on average)"
                below = [t for t in loads if t < bad[0]]
                if b.limit == "workers" and below:
                    why += ("; worker threads pile up when updates are already waiting on something else. "
                            + self.closest(by[(side, below[-1])], below[-1]))
            else:
                why = (f"no measured limit reached {A.THRESHOLD:g}% of its value, so the cause lies outside the "
                       "database's measured limits (for example the load generator, connections or CDC's copying)")
            out.append(f"- {words}, the database first stopped coping at {bad[0]:,} updates per second: {why} "
                       f"(run {rid}).")
        rng = ct.get(100)
        if rng and rng.n:
            gb = ST.retained_bytes(rng.mean, 100, retention_s) / ST.GB
            out.append(f"- At 100 updates per second, CDC's change table holds about {f(gb, 1)} GB when changes are "
                       f"kept for the default {ST.CDC_RETENTION_MINUTES // 1440} days ({f(rng.mean, 0)} bytes per "
                       f"change, measured, times 100 per second times 3 days) (run {rid}).")
        else:
            out.append(f"- Change-table storage at 100 updates per second: not measured (run {rid}).")
        out.append(self.cost_bullet(rid, best))
        return out

    @staticmethod
    def closest(group: list[A.StepMetrics], t: int) -> str:
        """The resource nearest its limit at a load that was handled, from the evidence text of each round."""
        ev = [m.binding.evidence for m in group if m.binding.evidence.startswith("highest ")]
        named = [(m.binding.percent, m.binding.label) for m in group if m.binding.reached]
        named += [(float(e.split("%")[0].rsplit(" ", 1)[1]), e[len("highest "):].split("%")[0].rsplit(" ", 1)[0])
                  for e in ev]
        if not named:
            return ""
        pct, label = max(named)
        scope, _, limit = label.partition(" ") if not label.startswith("database 1") else ("database 1", "", label[11:])
        return (f"At {t:,} updates per second the resource closest to its limit was "
                f"{SCOPE_NAMES.get(scope, 'the')} {LIMIT_NAMES.get(limit, limit)} ({f(pct, 0)}% in the busiest round)")

    def cost_bullet(self, rid, best) -> str:
        if best is None:
            return f"- Monthly cost difference: not measured (run {rid})."
        t = best[0]
        if not self.r.price_data:
            return f"- Monthly cost difference: not priced, because prices were not read for this report (run {rid})."
        total = self.r.monthly_total.get(t, math.nan)
        if math.isnan(total):
            return (f"- Monthly cost difference at {t:,} updates per second: not available, because a part of it "
                    f"could not be worked out (see the technical appendix, README.md) (run {rid}).")
        comp, label = self.r.compute_diff.get(t, (math.nan, ""))
        cur = self.r.currency or "USD"
        sizes = ("the pool sizes were measured" if label == "measured sizes" else
                 "at least one pool size is extrapolated from documented limits")
        compute = ("no extra compute, because the same pool size handles both" if comp == 0 else
                   f"{f(comp, 2)} {cur} of compute, the list-price difference between the smallest pool that handles each")
        return (f"- With CDC, running {t:,} updates per second all day, every day would add at most about {f(total, 2)} "
                f"{cur} a month (estimate, upper bound; {sizes}): {compute}, plus {f(total - comp, 2)} {cur} of backup "
                f"storage for the extra log, which is 0 if the pool's backups stay inside the free allowance. "
                f"Not included: data storage for the change table (run {rid}).")

    def certainty(self, run, best) -> list[str]:
        rounds = sorted({m.step.repeat for m in self.r.run_metrics(run.run_id) if m.step.configuration in (WITHOUT, WITH)})
        n = len(rounds)
        first = (f"- Each load level ran in {n} rounds. The brackets show the range across rounds. {n} rounds give a "
                 "rough idea of run-to-run variation, not a statistical guarantee." if n > 1 else
                 "- Each load level ran once, so chance cannot be told apart from a real effect.")
        sizes = sorted({x.pool.vcores for x in self.r.applied.values() if x.pool})
        size_text = (f"One pool size ({', '.join(map(str, sizes))} vCores) was measured; other sizes are extrapolated"
                     if len(sizes) == 1 else "Pool sizes other than those measured are extrapolated")
        return [first,
                "- Measured: every table except \"Storage and cost\", read from the databases and the load generator "
                "during the run. Calculated: the change-table size (measured bytes "
                "per change, times the load, times 3 days). Extrapolated: the monthly cost, which assumes the load "
                "runs all day, every day, takes unmeasured pool sizes from documented limits, and counts backup "
                "storage at its upper bound.",
                "- Limitations: the task data fits mostly in the pool's memory, so the database reads from disk less "
                f"than a production database would. {size_text}. Prices are pay-as-you-go list prices for compute and "
                "backup storage only; data storage, licence discounts and reservations are not included."]


def build(report: Report) -> str:
    return Summary(report).build()

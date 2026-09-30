"""Per-repository comparison figures for the two-model DevBench pilot
(qwen2.5-coder:7b, qwen3.8:27b): AgentM2M vs. free text vs. shared schema on
fidelity (canaries), build cost, change-propagation cost and change-impact
precision. Reads evaluation/harness/logs/devbench_rq{1,2,3}_<model>_*.jsonl
(all files per model, so a resumed run's RQ2 split across files is merged)
and writes:

  evaluation/results/figures/devbench_two_model_overview.{pdf,png}   all four aspects
  evaluation/results/figures/devbench_two_model_<aspect>.{pdf,png}   one per aspect
  evaluation/results/csv/devbench_two_model_per_repo.csv             the plotted data

Usage:
    python -m evaluation.analysis.make_two_model_figures
"""
from __future__ import annotations

import ast
import csv
import json
import re
import statistics as st
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = ROOT / "harness" / "logs"
FIG_DIR = ROOT / "results" / "figures"
CSV_DIR = ROOT / "results" / "csv"

MODELS = [("qwen2.5-coder-7b", "qwen2.5-coder:7b"), ("qwen3.8-27b", "qwen3.8:27b")]

# Fixed identity per configuration, same in every panel (categorical slots
# 1-3 of the dataviz reference palette, validated all-pairs, light mode).
# Hatching is the secondary channel for grayscale print and CVD.
CONFIGS = [
    ("agentm2m", "AgentM2M", "#2a78d6", ""),
    ("free_text", "Free text", "#eb6834", "////"),
    ("shared_schema", "Shared schema", "#1baf7a", "...."),
]

INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SURFACE = "#fcfcfb"


def _load(kind: str, tag: str) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(LOGS_DIR.glob(f"devbench_{kind}_{tag}_*.jsonl")):
        rows += [json.loads(line) for line in f.open() if line.strip()]
    return rows


def _module_truncated(rec: dict) -> bool:
    """A baseline's Developer output that does not parse (it ran into the
    output cap); its canaries are lost for that reason, not hand-off loss."""
    t = rec.get("transcript_text", "")
    if rec["config"] == "agentm2m" or "[Developer]" not in t:
        return False
    dev = t.split("[Developer]")[1].split("RESPONSE:", 1)[1].split("\n[Tester]")[0]
    m = re.search(r"```(?:python)?\s*\n(.*?)```", dev, re.S)
    try:
        ast.parse(m.group(1) if m else dev)
        return False
    except SyntaxError:
        return True


def collect() -> tuple[list[str], dict]:
    """data[model][config][repo] -> metrics; repos ordered by operation count."""
    data: dict = {}
    n_ops: dict[str, int] = {}
    for tag, name in MODELS:
        rq1 = [r for r in _load("rq1", tag) if not r.get("error")]
        rq2 = _load("rq2", tag)
        for r in _load("rq3", tag):
            if "error" not in r:
                n_ops[r["repo"]] = r["n_ops_before"]
        per: dict = {c: {} for c, *_ in CONFIGS}
        for r in rq1:
            per[r["config"]][r["repo"]] = {
                "canary": r["canary_pass"],
                "build_k": r["total_tokens"] / 1000.0,
                "calls": r["llm_calls"],
                "truncated": _module_truncated(r),
            }
        for c, *_ in CONFIGS:
            for repo in per[c]:
                changes = [x for x in rq2 if x["config"] == c and x["repo"] == repo]
                if changes:
                    per[c][repo]["change_k"] = st.mean(x["tokens"] for x in changes) / 1000.0
                    per[c][repo]["precision"] = st.mean(x["precision"] for x in changes)
                    per[c][repo]["recall"] = st.mean(x["recall"] for x in changes)
        data[name] = per
    repos = sorted({repo for per in data.values() for repo in per["agentm2m"]}, key=lambda r: (n_ops.get(r, 0), r))
    return repos, {"data": data, "n_ops": n_ops}


def write_csv(repos: list[str], bundle: dict) -> Path:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    path = CSV_DIR / "devbench_two_model_per_repo.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "repo", "operations", "config", "canaries_of_5", "module_truncated",
                    "build_tokens_k", "llm_calls", "tokens_per_change_k", "impact_precision", "impact_recall"])
        for _tag, model in MODELS:
            for repo in repos:
                for c, *_ in CONFIGS:
                    m = bundle["data"][model][c].get(repo, {})
                    w.writerow([model, repo, bundle["n_ops"].get(repo, ""), c, m.get("canary", ""),
                                m.get("truncated", ""), f"{m.get('build_k', float('nan')):.2f}", m.get("calls", ""),
                                f"{m.get('change_k', float('nan')):.3f}", f"{m.get('precision', float('nan')):.2f}",
                                f"{m.get('recall', float('nan')):.2f}"])
    return path


# ---------------------------------------------------------------------------
# plotting: shared style and a text-overlap check

FIG_WIDTH = 7.16  # IEEE two-column full width, inches: sizes below are print sizes
FS_TICK, FS_LABEL, FS_TITLE, FS_SUB, FS_LEGEND, FS_NOTE = 7.5, 8, 8.5, 7.5, 8.5, 7

LINE_STYLE = {"agentm2m": ("-", "o"), "free_text": ("--", "s"), "shared_schema": (":", "D")}


def _base_style() -> None:
    plt.rcParams.update({
        "font.family": "sans-serif", "font.size": FS_LABEL, "hatch.linewidth": 0.5, "hatch.color": SURFACE,
        "axes.titlesize": FS_TITLE, "pdf.fonttype": 42, "ps.fonttype": 42,
    })


def _clean_axes(ax, *, grid_axis: str) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(INK_2)
        ax.spines[side].set_linewidth(0.6)
    ax.tick_params(colors=INK_2, labelsize=FS_TICK, width=0.6, length=2.5)
    getattr(ax, f"{grid_axis}axis").grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _panel_title(ax, head: str, sub: str) -> None:
    ax.set_title(head, fontsize=FS_TITLE, fontweight="bold", color=INK, loc="left", pad=16)
    ax.text(0.0, 1.02, sub, transform=ax.transAxes, fontsize=FS_SUB, color=INK_2, ha="left", va="bottom")


def check_text_overlaps(fig) -> list[str]:
    """Every visible, non-empty text artist's rendered box must lie inside the
    figure and must not intersect any other text box. Returns problems."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    fb = fig.bbox
    # tick labels whose tick lies outside the visible range are never drawn
    hidden = set()
    for ax in fig.axes:
        for axis, (lo, hi) in ((ax.xaxis, sorted(ax.get_xlim())), (ax.yaxis, sorted(ax.get_ylim()))):
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                if not (lo - 1e-9 <= tick.get_loc() <= hi + 1e-9):
                    hidden.update({id(tick.label1), id(tick.label2)})
    boxes = []
    for t in fig.findobj(matplotlib.text.Text):
        if id(t) in hidden or not t.get_visible() or not t.get_text().strip():
            continue
        bb = t.get_window_extent(renderer)
        if bb.width < 1 or bb.height < 1:
            continue
        boxes.append((t.get_text().strip().replace("\n", " / ")[:40], bb))
    problems = []
    for name, bb in boxes:
        if bb.x0 < fb.x0 - 1 or bb.x1 > fb.x1 + 1 or bb.y0 < fb.y0 - 1 or bb.y1 > fb.y1 + 1:
            problems.append(f"outside figure: '{name}'")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i][1], boxes[j][1]
            if a.x0 < b.x1 - 0.5 and b.x0 < a.x1 - 0.5 and a.y0 < b.y1 - 0.5 and b.y0 < a.y1 - 0.5:
                problems.append(f"overlap: '{boxes[i][0]}' <> '{boxes[j][0]}'")
    return problems


def _save(fig, stem: str, outputs: list, report: dict) -> None:
    report[stem] = check_text_overlaps(fig)
    for ext in ("pdf", "png"):
        p = FIG_DIR / f"{stem}.{ext}"
        fig.savefig(p, dpi=220, facecolor=SURFACE)
        outputs.append(p)
    plt.close(fig)


# ---------------------------------------------------------------------------
# per-repository comparison (bars / dots)

ASPECTS = [
    # key, short title, x-label, form, x-limits
    ("canary", "Fidelity", "canaries reaching the code (of 5)", "bar", (0, 5)),
    ("build_k", "Build cost", "tokens to build once (thousands)", "bar", None),
    ("change_k", "Change cost", "tokens per change (thousands, log scale)", "dot", None),
    ("precision", "Impact precision", "precision of the impact set (mean of 3 changes)", "bar", (0, 1)),
]


def _repo_label(r: str, n_ops: dict) -> str:
    return f"{r.replace('particle-swarm-optimization', 'particle-swarm-opt')} ({n_ops.get(r, '?')})"


def _bars(ax, key: str, form: str, xlim, model: str, repos: list[str], bundle: dict, show_labels: bool) -> None:
    per = bundle["data"][model]
    _clean_axes(ax, grid_axis="x")
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0, labelsize=FS_TICK, colors=INK)
    ax.set_yticks(range(len(repos)))
    ax.set_yticklabels([_repo_label(r, bundle["n_ops"]) for r in repos] if show_labels else [])
    ax.set_ylim(len(repos) - 0.5, -0.5)
    slot = 0.84
    h = slot / len(CONFIGS)
    for i, (c, _label, color, hatch) in enumerate(CONFIGS):
        ys = [y - slot / 2 + h * (i + 0.5) for y in range(len(repos))]
        vals = [per[c].get(r, {}).get(key, float("nan")) for r in repos]
        if form == "bar":
            ax.barh(ys, vals, height=h * 0.86, color=color, hatch=hatch, edgecolor=SURFACE, linewidth=0, zorder=2)
            # a zero-length bar is invisible: mark zeros with a series-coloured
            # tick so they don't read as missing data; note truncated modules
            zeros = [(y, r) for y, r, v in zip(ys, repos, vals) if v == 0]
            if zeros:
                ax.scatter([0] * len(zeros), [y for y, _ in zeros], marker="|", s=40, linewidths=1.6,
                           color=color, zorder=3, clip_on=False)
            for y, r in zeros:
                if per[c].get(r, {}).get("truncated"):
                    ax.text(0.0, y, "   † truncated", fontsize=FS_NOTE - 0.5, color=INK_2, va="center",
                            ha="left", zorder=3)
        else:  # log scale: bar length would be meaningless, so dots
            ax.scatter(vals, ys, s=22, color=color, edgecolor=SURFACE, linewidth=0.8, zorder=3,
                       marker=LINE_STYLE[c][1])
    if form == "dot":
        ax.set_xscale("log")
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _p: f"{v:g}"))
        ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    elif xlim:
        ax.set_xlim(*xlim)
    ax.set_xlabel(ASPECT_XLABEL[key], fontsize=FS_LABEL, color=INK_2, labelpad=3)


ASPECT_XLABEL = {k: x for k, _t, x, _f, _l in ASPECTS}


def _summary(key: str, model: str, repos: list[str], bundle: dict) -> str:
    per = bundle["data"][model]

    def mean(c):
        vals = [per[c][r][key] for r in repos if key in per[c].get(r, {})]
        return st.mean(vals) if vals else float("nan")

    a, f, s = mean("agentm2m"), mean("free_text"), mean("shared_schema")
    if key == "canary":
        tot = lambda c: sum(per[c][r]["canary"] for r in repos)  # noqa: E731
        return f"total of 40:  A {tot('agentm2m')} · F {tot('free_text')} · S {tot('shared_schema')}"
    if key == "build_k":
        return f"mean:  A {a:.1f}k · F {f:.1f}k · S {s:.1f}k"
    if key == "change_k":
        return f"mean:  A {a:.2f}k · F {f:.2f}k · S {s:.2f}k"
    return f"mean:  A {a:.2f} · F {f:.2f} · S {s:.2f}"


def _bar_legend(fig) -> None:
    handles = [Patch(facecolor=color, hatch=hatch, edgecolor=SURFACE, label=label) for _c, label, color, hatch in CONFIGS]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0.995), ncol=3, frameon=False,
               fontsize=FS_LEGEND, handlelength=1.8, columnspacing=2.0, labelcolor=INK)


def bar_figure(aspects, repos, bundle, *, height_per_row: float, note: str | None = None) -> plt.Figure:
    _base_style()
    n = len(aspects)
    fig, axes = plt.subplots(n, len(MODELS), figsize=(FIG_WIDTH, height_per_row * n + 0.5), squeeze=False,
                             facecolor=SURFACE)
    for row, (key, title, _xl, form, xlim) in enumerate(aspects):
        for col, (_tag, model) in enumerate(MODELS):
            ax = axes[row][col]
            _bars(ax, key, form, xlim, model, repos, bundle, show_labels=(col == 0))
            _panel_title(ax, f"{title} · {model}", _summary(key, model, repos, bundle))
        # same scale in both model columns, for an honest comparison
        lims = [axes[row][c].get_xlim() for c in range(len(MODELS))]
        lo, hi = min(l[0] for l in lims), max(l[1] for l in lims)
        if form == "bar" and not xlim:
            lo = 0
        for c in range(len(MODELS)):
            axes[row][c].set_xlim(lo, hi)
    _bar_legend(fig)
    bottom = 0
    if note:
        fig.text(0.01, 0.005, note, fontsize=FS_NOTE, color=INK_2, ha="left", va="bottom")
        bottom = 0.22 / fig.get_figheight()
    fig.tight_layout(rect=(0, bottom, 1, 1 - 0.3 / fig.get_figheight()), h_pad=1.6, w_pad=1.4)
    return fig


# ---------------------------------------------------------------------------
# line charts

def _mean_over(repos, per, c, key):
    vals = [per[c][r][key] for r in repos if key in per[c].get(r, {})]
    return st.mean(vals)


def _direct_labels(ax, ends: list[tuple[str, float, str]], *, log: bool) -> None:
    """Label line ends on the right, nudged apart so they never collide."""
    import math

    ymin, ymax = ax.get_ylim()
    to = (lambda v: math.log10(v)) if log else (lambda v: v)
    back = (lambda v: 10 ** v) if log else (lambda v: v)
    span = to(ymax) - to(ymin)
    gap = span * 0.075
    ceiling = to(ymax) - span * 0.06  # stay inside the plot, clear of the subtitle above it
    placed = []
    for label, y, color in sorted(ends, key=lambda e: e[1]):
        ty = to(y)
        if placed and ty - placed[-1] < gap:
            ty = placed[-1] + gap
        placed.append(ty)
    # if pushed past the ceiling, shift the whole stack down (keeps the spacing),
    # but never below the floor: labels must stay beside the plot
    excess = placed[-1] - ceiling if placed else 0
    if excess > 0:
        placed = [p - excess for p in placed]
    floor = to(ymin) + span * 0.04
    if placed and placed[0] < floor:
        step = (ceiling - floor) / max(len(placed) - 1, 1)
        placed = [max(p, floor + i * step) if p < floor + i * step else p for i, p in enumerate(placed)]
    for (label, _y, _color), ty in zip(sorted(ends, key=lambda e: e[1]), placed):
        ax.annotate(label, xy=(1.0, back(ty)), xycoords=("axes fraction", "data"), xytext=(4, 0),
                    textcoords="offset points", fontsize=FS_NOTE, color=INK, va="center", ha="left")


def line_cumulative(repos, bundle) -> plt.Figure:
    """Cumulative tokens = measured mean build cost + n x measured mean cost
    per change, n = 0..10 changes. Where AgentM2M's line crosses a baseline's,
    its extra build cost has been repaid."""
    _base_style()
    fig, axes = plt.subplots(1, len(MODELS), figsize=(FIG_WIDTH, 3.2), facecolor=SURFACE)
    ns = list(range(0, 11))
    top = 0.0
    for ax, (_tag, model) in zip(axes, MODELS):
        per = bundle["data"][model]
        _clean_axes(ax, grid_axis="y")
        cost, ends = {}, []
        for c, label, color, _h in CONFIGS:
            b, d = _mean_over(repos, per, c, "build_k"), _mean_over(repos, per, c, "change_k")
            cost[c] = (b, d)
            ys = [b + n * d for n in ns]
            top = max(top, max(ys))
            ls, mk = LINE_STYLE[c]
            ax.plot(ns, ys, ls, color=color, lw=1.6, marker=mk, ms=4, markevery=2, zorder=3,
                    markeredgecolor=SURFACE, markeredgewidth=0.6)
            ends.append((label, ys[-1], color))
        # break-even vs each baseline: first n at which AgentM2M is no more expensive
        parts = []
        for c, short in (("free_text", "F"), ("shared_schema", "S")):
            (ba, da), (bb, db) = cost["agentm2m"], cost[c]
            n_star = 0.0 if ba <= bb else (ba - bb) / (db - da)
            parts.append(f"{short} " + ("from 0" if n_star == 0 else f"after {n_star:.1f}"))
        ax.set_xlim(0, 10)
        ax.set_xticks(ns[::2])
        ax.set_xlabel("changes propagated", fontsize=FS_LABEL, color=INK_2)
        _panel_title(ax, model, "A cheaper than " + " · ".join(parts))
        ax._ends = ends
    axes[0].set_ylabel("cumulative tokens (k)", fontsize=FS_LABEL, color=INK_2)
    for ax in axes:  # one common scale, set before the labels are placed
        ax.set_ylim(0, top * 1.08)
    for ax in axes:
        _direct_labels(ax, ax._ends, log=False)
    fig.text(0.01, 0.012, "Measured mean build cost + n × measured mean cost per change, "
             "averaged over the 8 repositories.", fontsize=FS_NOTE, color=INK_2, ha="left", va="bottom")
    fig.tight_layout(rect=(0.0, 0.11, 0.91, 1), w_pad=5.5)
    return fig


def line_scaling(repos, bundle, key: str, ylabel: str, log: bool, head: str) -> plt.Figure:
    """A metric against module size (operations incl. 5 canaries); repositories
    of equal size are averaged, so the x values are distinct and ordered."""
    _base_style()
    fig, axes = plt.subplots(1, len(MODELS), figsize=(FIG_WIDTH, 3.0), facecolor=SURFACE)
    sizes = sorted({bundle["n_ops"][r] for r in repos})
    by_size = {n: [r for r in repos if bundle["n_ops"][r] == n] for n in sizes}
    all_ys: list[float] = []
    for ax, (_tag, model) in zip(axes, MODELS):
        per = bundle["data"][model]
        _clean_axes(ax, grid_axis="y")
        ends = []
        for c, label, color, _h in CONFIGS:
            ys = []
            for n in sizes:
                vals = [per[c][r][key] for r in by_size[n] if key in per[c].get(r, {})]
                ys.append(st.mean(vals) if vals else float("nan"))
            if log:  # a removal costs AgentM2M 0; keep the mean (never 0 over 3 changes)
                ys = [max(y, 1e-3) for y in ys]
            all_ys += ys
            ls, mk = LINE_STYLE[c]
            ax.plot(sizes, ys, ls, color=color, lw=1.6, marker=mk, ms=4.5, zorder=3,
                    markeredgecolor=SURFACE, markeredgewidth=0.6)
            ends.append((label, ys[-1], color))
        if log:
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _p: f"{v:g}"))
            ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        ax.set_xticks(sizes)
        ax.set_xlabel("operations in the module (incl. 5 canaries)", fontsize=FS_LABEL, color=INK_2)
        _panel_title(ax, model, head)
        ax._ends = ends  # labelled after limits are final
    axes[0].set_ylabel(ylabel, fontsize=FS_LABEL, color=INK_2)
    # one common scale from both models' data, set before labels are placed
    lo, hi = (min(all_ys) / 1.5, max(all_ys) * 1.5) if log else (0.0, max(all_ys) * 1.08)
    for a in axes:
        a.set_ylim(lo, hi)
    for a in axes:
        _direct_labels(a, a._ends, log=log)
    fig.tight_layout(rect=(0, 0, 0.93, 1), w_pad=4.5)
    return fig


def main() -> int:
    repos, bundle = collect()
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    outputs = [write_csv(repos, bundle)]
    report: dict[str, list[str]] = {}
    note = "† baseline module truncated at the 4096-token output cap; its canaries count as lost."

    _save(bar_figure(ASPECTS, repos, bundle, height_per_row=2.45, note=note), "devbench_two_model_overview",
          outputs, report)
    for aspect in ASPECTS:
        stem = f"devbench_two_model_{aspect[0].replace('_k', '')}"
        _save(bar_figure([aspect], repos, bundle, height_per_row=2.7,
                         note=note if aspect[0] == "canary" else None), stem, outputs, report)

    _save(line_cumulative(repos, bundle), "devbench_two_model_line_cumulative", outputs, report)
    _save(line_scaling(repos, bundle, "build_k", "tokens to build once (thousands)", False,
                       "Build cost vs. module size"), "devbench_two_model_line_build_vs_size", outputs, report)
    _save(line_scaling(repos, bundle, "change_k", "tokens per change (thousands, log)", True,
                       "Change cost vs. module size"), "devbench_two_model_line_change_vs_size", outputs, report)

    for p in outputs:
        print(p.relative_to(ROOT.parent))
    bad = {k: v for k, v in report.items() if v}
    print("text-overlap check:", "all figures clean" if not bad else "")
    for k, v in bad.items():
        print(f"  {k}: {len(v)} problem(s)")
        for msg in v[:12]:
            print("    -", msg)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

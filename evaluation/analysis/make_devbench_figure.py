#!/usr/bin/env python3
"""Professional, paper-ready figure for the DevBench chakin pilot
(Sec. V / Table tab:prelim of docs/DS-A2A.tex), built directly from the
per-model CSVs in evaluation/results/csv/devbench_raw_metrics_*.csv and
the RQ2 precision/recall in devbench_summary_*.json. Three panels, grouped
by model, colored by coordination config (free text / shared schema /
AgentM2M) -- this is the exact grouping the paper's Results paragraph
narrates (the RQ1 P1-mode sign flip across models; free-text's
non-monotonic RQ2 precision; AgentM2M's token premium).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CSV_DIR = Path(__file__).resolve().parents[1] / "results" / "csv"
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "figures"

# model key -> (csv suffix, json suffix, short axis label)
MODELS = [
    ("qwen2.5-coder:7b", "qwen2.5-coder-7b", "qwen2.5-coder\n7b"),
    ("devstral:24b", "devstral-24b", "devstral\n24b"),
    ("qwen3-coder:30b", "qwen3-coder-30b", "qwen3-coder\n30b"),
]

CONFIGS = [
    ("free_text", "Free text"),
    ("shared_schema", "Shared schema"),
    ("agentm2m", "AgentM2M"),
]

# validated 3-slot categorical order (dataviz skill reference palette,
# slots 1/2/3: blue / orange / aqua -- passes CVD + normal-vision floors
# all-pairs in light mode)
COLORS = {
    "free_text": "#2a78d6",
    "shared_schema": "#eb6834",
    "agentm2m": "#1baf7a",
}

TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID_COLOR = "#dedcd3"


def load_p1_and_tokens() -> tuple[dict, dict]:
    p1 = {m: {} for m, _, _ in MODELS}
    tokens = {m: {} for m, _, _ in MODELS}
    for model_key, csv_suffix, _ in MODELS:
        path = CSV_DIR / f"devbench_raw_metrics_{csv_suffix}.csv"
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                cfg = row["config"]
                p1[model_key][cfg] = float(row["mast_p1_count"])
                tokens[model_key][cfg] = float(row["total_tokens"]) / 1000.0
    return p1, tokens


def load_precision() -> dict:
    precision = {m: {} for m, _, _ in MODELS}
    for model_key, json_suffix, _ in MODELS:
        path = CSV_DIR / f"devbench_summary_{json_suffix}.json"
        with open(path) as f:
            data = json.load(f)
        for cfg, _ in CONFIGS:
            precision[model_key][cfg] = data["rq2"][cfg]["precision"]
    return precision


def style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Nimbus Roman", "Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 8.5,
            "axes.edgecolor": TEXT_SECONDARY,
            "axes.labelcolor": TEXT_PRIMARY,
            "text.color": TEXT_PRIMARY,
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "axes.linewidth": 0.7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def grouped_bars(ax, values_by_model, ylabel, title, ylim, fmt, bar_label_fontsize=6.6) -> None:
    n_models = len(MODELS)
    n_cfg = len(CONFIGS)
    width = 0.24
    group_gap = 1.0
    x = [i * group_gap for i in range(n_models)]

    # per-model list of (x-offset, value) already placed, to detect near-equal
    # bar heights within a group and stagger their labels so text never overlaps
    placed_per_group: list[list[tuple[float, float]]] = [[] for _ in range(n_models)]

    for j, (cfg_key, cfg_label) in enumerate(CONFIGS):
        offsets = [xi + (j - (n_cfg - 1) / 2) * width for xi in x]
        vals = [values_by_model[m][cfg_key] for m, _, _ in MODELS]
        bars = ax.bar(
            offsets,
            vals,
            width=width * 0.92,
            color=COLORS[cfg_key],
            label=cfg_label,
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )
        for gi, (rect, v) in enumerate(zip(bars, vals)):
            base_pad = ylim[1] * 0.025
            extra = 0.0
            for _prev_off, prev_v in placed_per_group[gi]:
                if abs(prev_v - v) < ylim[1] * 0.035:
                    extra = max(extra, ylim[1] * 0.075)
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                v + base_pad + extra,
                fmt(v),
                ha="center",
                va="bottom",
                fontsize=bar_label_fontsize,
                color=TEXT_PRIMARY,
            )
            placed_per_group[gi].append((rect.get_x(), v))

    ax.set_xticks(x)
    ax.set_xticklabels([lbl for _, _, lbl in MODELS], fontsize=7.2)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.set_title(title, fontsize=8.5, fontweight="bold", pad=4)
    ax.set_ylim(*ylim)
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="both", length=2.5)


def make_figure(out_dir: Path = OUT_DIR) -> tuple[Path, Path]:
    style()
    p1, tokens = load_p1_and_tokens()
    precision = load_precision()

    fig, axes = plt.subplots(1, 3, figsize=(7.05, 2.35), constrained_layout=True)

    grouped_bars(
        axes[0],
        p1,
        ylabel="P1 modes / trace",
        title="(a) RQ1: MAST P1 failure modes",
        ylim=(0, 3.9),
        fmt=lambda v: f"{v:.0f}",
    )
    grouped_bars(
        axes[1],
        precision,
        ylabel="Impact precision",
        title="(b) RQ2: impact precision (recall = 1.00 ∀)",
        ylim=(0, 1.18),
        fmt=lambda v: f"{v:.2f}",
    )
    grouped_bars(
        axes[2],
        tokens,
        ylabel="Tokens / RQ1 task (k)",
        title="(c) RQ1: LLM token cost",
        ylim=(0, 32),
        fmt=lambda v: f"{v:.1f}",
    )

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="outside upper center",
        ncol=3,
        frameon=False,
        fontsize=8.5,
        handlelength=1.4,
        handleheight=1.1,
        columnspacing=1.6,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "devbench_rq1_rq2_per_model.pdf"
    png_path = out_dir / "devbench_rq1_rq2_per_model.png"
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path


def main() -> int:
    pdf_path, png_path = make_figure()
    print(f"Wrote {pdf_path}")
    print(f"Wrote {png_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

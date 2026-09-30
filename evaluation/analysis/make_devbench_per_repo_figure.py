#!/usr/bin/env python3
"""Per-repository DevBench figure: how AgentM2M's RQ1 (P1 modes) and RQ2
(impact precision) results vary across the 6 evaluated repos (chakin,
geotext, readtime, particle-swarm-optimization, Hybrid_Images,
stocktrends), grouped by model. Complements
make_devbench_figure.py's per-model panels (which pool across repos) with
the repo axis that script does not show. Reads
evaluation/results/csv/devbench_per_repo.json, written by
aggregate_devbench_per_repo.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

CSV_DIR = Path(__file__).resolve().parents[1] / "results" / "csv"
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "figures"

MODELS = [
    ("qwen2.5-coder:7b", "qwen2.5-coder:7b"),
    ("devstral:24b", "devstral:24b"),
    ("qwen3-coder:30b", "qwen3-coder:30b"),
]

# same validated 3-slot categorical palette as make_devbench_figure.py,
# here keyed by model instead of by config
COLORS = {
    "qwen2.5-coder:7b": "#2a78d6",
    "devstral:24b": "#eb6834",
    "qwen3-coder:30b": "#1baf7a",
}

TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID_COLOR = "#dedcd3"


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


def grouped_bars(ax, repos, values_by_repo_model, ylabel, title, ylim, fmt) -> None:
    n_repos = len(repos)
    n_models = len(MODELS)
    width = 0.24
    group_gap = 1.0
    x = [i * group_gap for i in range(n_repos)]

    for j, (model_key, _) in enumerate(MODELS):
        offsets = [xi + (j - (n_models - 1) / 2) * width for xi in x]
        vals = [values_by_repo_model[r].get(model_key) for r in repos]
        plot_offsets = [o for o, v in zip(offsets, vals) if v is not None]
        plot_vals = [v for v in vals if v is not None]
        bars = ax.bar(
            plot_offsets,
            plot_vals,
            width=width * 0.92,
            color=COLORS[model_key],
            label=model_key,
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )
        for rect, v in zip(bars, plot_vals):
            ax.text(
                rect.get_x() + rect.get_width() / 2,
                v + ylim[1] * 0.025,
                fmt(v),
                ha="center",
                va="bottom",
                fontsize=6.2,
                color=TEXT_PRIMARY,
            )

    ax.set_xticks(x)
    ax.set_xticklabels(repos, fontsize=6.6, rotation=20, ha="right")
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
    data = json.loads((CSV_DIR / "devbench_per_repo.json").read_text())
    repos = data["repos"]

    p1_by_repo_model = {
        r: {m: (data["by_repo"][r]["per_model"][m]["rq1"]["p1_count"]
                if data["by_repo"][r]["per_model"][m]["rq1"] else None)
            for m, _ in MODELS}
        for r in repos
    }
    precision_by_repo_model = {
        r: {m: (data["by_repo"][r]["per_model"][m]["rq2"]["precision"]
                if data["by_repo"][r]["per_model"][m]["rq2"] else None)
            for m, _ in MODELS}
        for r in repos
    }

    fig, axes = plt.subplots(1, 2, figsize=(7.05, 2.7), constrained_layout=True)

    grouped_bars(
        axes[0], repos, p1_by_repo_model,
        ylabel="P1 modes / trace",
        title="(a) AgentM2M: RQ1 MAST P1 modes by repo",
        ylim=(0, 4.4),
        fmt=lambda v: f"{v:.0f}",
    )
    grouped_bars(
        axes[1], repos, precision_by_repo_model,
        ylabel="Impact precision",
        title="(b) AgentM2M: RQ2 impact precision by repo",
        ylim=(0, 1.18),
        fmt=lambda v: f"{v:.2f}",
    )

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles, labels,
        loc="outside upper center", ncol=3, frameon=False, fontsize=8,
        handlelength=1.4, handleheight=1.1, columnspacing=1.6,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / "devbench_per_repo.pdf"
    png_path = out_dir / "devbench_per_repo.png"
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

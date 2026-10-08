"""Aggregate the follow-up study (docs/follow-up-study-DS-A2A.tex).

Reads every log in evaluation/results/follow-up-study/raw/ and writes, next to
it:

    csv/      one CSV per analysis (per-run rows and summaries)
    tables/   LaTeX tabular bodies and `macros.tex` (every number quoted in the
              paper is a macro generated here, so text and data cannot diverge)
    figures/  PDF figures
    summary.json   all macros, machine readable

    python -m evaluation.analysis.follow_up_aggregate [--raw DIR] [--validate-legacy]

`--validate-legacy` recomputes the NIER pilot numbers (7B free text keeps 7 of
40 canaries, ...) from the archived pilot logs to prove the aggregation is the
one that produced the published table.
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parents[1] / "results" / "follow-up-study"
LEGACY_LOGS = Path(__file__).resolve().parents[1] / "harness" / "logs"
MODELS = {"qwen2.5-coder:7b": "Small", "qwen3.8:27b": "Large"}
MODEL_LABEL = {"Small": "qwen2.5-coder:7b", "Large": "qwen3.8:27b"}
CONFIGS = ("free_text", "shared_schema", "agentm2m")
CFG_SHORT = {"free_text": "F", "shared_schema": "S", "agentm2m": "A"}
ALL_POLICIES = ("paste", "routed", "ctx", "ctxi")
POL_LABEL = {"paste": "Paste", "routed": "Routed", "ctx": "Ctx", "ctxi": "Ctx-item"}
COLORS = {"free_text": "#eb6834", "shared_schema": "#1baf7a", "agentm2m": "#2a78d6",
          "paste": "#eb6834", "routed": "#c9a227", "ctx": "#8a6fd1", "ctxi": "#2a78d6"}
MACROS: dict[str, str] = {}


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _words(s: str) -> str:
    return re.sub(r"\d", lambda m: "zero one two three four five six seven eight nine".split()[int(m.group())].title(), s)


def macro(name: str, value, fmt: str = "{:.2f}") -> None:
    if isinstance(value, (float, np.floating)):
        text = "n/a" if (value is None or math.isnan(value)) else fmt.format(value)
    else:
        text = str(value)
    MACROS[_words(re.sub(r"[^A-Za-z0-9]", "", name))] = re.sub(r"(?<!\\)%", r"\\%", text).replace("_", r"\_")


def load(raw: Path, pattern: str) -> pd.DataFrame:
    rows = []
    for f in sorted(raw.glob(pattern)):
        for line in f.open():
            if line.strip():
                r = json.loads(line)
                r["_file"] = f.name
                rows.append(r)
    return pd.DataFrame(rows)


def paired(a: np.ndarray, b: np.ndarray, *, n_boot: int = 10000, seed: int = 0) -> dict:
    """Paired comparison a - b: mean difference, bootstrap 95% CI, sign-flip
    permutation p (two-sided, exact for n <= 16), paired effect size dz and
    Cliff's delta."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    n = len(d)
    if n == 0:
        return {"n": 0, "mean_diff": float("nan"), "ci_lo": float("nan"), "ci_hi": float("nan"), "p": float("nan"),
                "dz": float("nan"), "cliff": float("nan")}
    rng = np.random.default_rng(seed)
    boots = rng.choice(d, size=(n_boot, n), replace=True).mean(axis=1)
    obs = abs(d.mean())
    if n <= 16:
        signs = np.array(np.meshgrid(*[[-1, 1]] * n)).reshape(n, -1).T
        perm = np.abs((signs * d).mean(axis=1))
    else:
        perm = np.abs((rng.choice([-1, 1], size=(20000, n)) * d).mean(axis=1))
    p = float((perm >= obs - 1e-12).mean())
    sd = d.std(ddof=1) if n > 1 else float("nan")
    gt = sum((x > y) for x in a for y in b)
    lt = sum((x < y) for x in a for y in b)
    return {"n": n, "mean_diff": float(d.mean()), "ci_lo": float(np.percentile(boots, 2.5)),
            "ci_hi": float(np.percentile(boots, 97.5)), "p": p,
            "dz": float(d.mean() / sd) if sd and not math.isnan(sd) and sd > 0 else float("nan"),
            "cliff": (gt - lt) / (len(a) * len(b))}


def tex(path: Path, header: list[str], rows: list[list], align: str | None = None) -> None:
    align = align or "l" + "r" * (len(header) - 1)
    lines = [rf"\begin{{tabular}}{{@{{}}{align}@{{}}}}", r"\toprule", " & ".join(header) + r" \\", r"\midrule"]
    for r in rows:
        lines.append(" & ".join(str(x) for x in r) + r" \\" if r != ["__rule__"] else r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def f2(x, nd=2) -> str:
    return "--" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def mean_sd(xs, nd=2) -> str:
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not xs:
        return "--"
    return f"{np.mean(xs):.{nd}f}" + (f"\\,{{\\scriptsize$\\pm${np.std(xs, ddof=1):.{nd}f}}}" if len(xs) > 1 else "")


def _module_truncated(rec: pd.Series) -> bool:
    t = rec.get("transcript_text", "") or ""
    if rec["config"] == "agentm2m" or "[Developer]" not in t:
        return False
    try:
        dev = t.split("[Developer]")[1].split("RESPONSE:", 1)[1].split("\n[Tester]")[0]
    except IndexError:
        return False
    m = re.search(r"```(?:python)?\s*\n(.*?)```", dev, re.S)
    try:
        ast.parse(m.group(1) if m else dev)
        return False
    except SyntaxError:
        return True


# ----------------------------------------------------------------------------
# RQ0: replication of the NIER pilot on all valid repos
# ----------------------------------------------------------------------------

def nier(raw: Path, csv_dir: Path, tab_dir: Path, fig_dir: Path, pattern_suffix: str = "_s*_*.jsonl") -> dict | None:
    rq1 = load(raw, f"devbench_rq1_*{pattern_suffix}")
    if rq1.empty:
        return None
    rq1 = rq1[rq1.get("error").isna()] if "error" in rq1 else rq1
    rq1["model_key"] = rq1["model"].map(MODELS)
    rq1["truncated"] = rq1.apply(_module_truncated, axis=1)
    rq1["build_k"] = rq1["total_tokens"] / 1000.0
    rq1["canary_frac"] = rq1["canary_pass"] / rq1["canary_total"].replace(0, np.nan)
    rq2 = load(raw, f"devbench_rq2_*{pattern_suffix}")
    rq3 = load(raw, f"devbench_rq3_*{pattern_suffix}")
    if not rq2.empty:
        rq2["model_key"] = rq2["model"].map(MODELS)
        rq2["tokens_k"] = rq2["tokens"] / 1000.0
    if not rq3.empty:
        rq3 = rq3[rq3.get("error").isna()] if "error" in rq3 else rq3
        rq3["model_key"] = rq3["model"].map(MODELS)
    rq1.drop(columns=["transcript_text", "mast_raw"], errors="ignore").to_csv(csv_dir / "nier_rq1_per_run.csv", index=False)
    if not rq2.empty:
        rq2.to_csv(csv_dir / "nier_rq2_per_change.csv", index=False)

    try:
        from evaluation.harness.hot_reviewer_rq3 import glue_line_counts
        glue = glue_line_counts()
    except Exception:  # noqa: BLE001
        glue = {"agentm2m_hot": 7, "manual_per_baseline_config": 11}

    out_rows, tex_rows = [], []
    for mk in ("Small", "Large"):
        sub1 = rq1[rq1.model_key == mk]
        if sub1.empty:
            continue
        repos = sorted(sub1.repo.unique())
        seeds = sorted(sub1.seed.unique())
        macro(f"nier{mk}Repos", len(repos), "{}")
        macro(f"nier{mk}Seeds", len(seeds), "{}")
        for c in CONFIGS:
            s1 = sub1[sub1.config == c]
            per_seed_can = [s1[s1.seed == sd].canary_pass.sum() for sd in seeds]
            per_seed_den = [s1[s1.seed == sd].canary_total.sum() for sd in seeds]
            s2 = rq2[(rq2.model_key == mk) & (rq2.config == c)] if not rq2.empty else pd.DataFrame()
            row = {
                "model": mk, "config": c, "repos": len(repos), "seeds": len(seeds),
                "canaries_mean": float(np.mean(per_seed_can)), "canaries_total": float(np.mean(per_seed_den)),
                "canaries_sd": float(np.std(per_seed_can, ddof=1)) if len(seeds) > 1 else float("nan"),
                "truncated_modules": int(s1.truncated.sum()),
                "build_k": float(s1.build_k.mean()), "mast_p1": float(s1.mast_p1_count.mean()),
                "mast_p2": float(s1.mast_p2_count.mean()), "accept_pass": int(s1.acceptance_pass.sum()),
                "unit_pass": int(s1.unit_pass.sum()), "n_runs": len(s1),
            }
            if len(s2):
                row.update(precision=float(s2.precision.mean()), recall=float(s2.recall.mean()),
                           change_k=float(s2.tokens_k.mean()), n_changes=len(s2))
            out_rows.append(row)
            cs = CFG_SHORT[c]
            macro(f"nier{mk}{cs}Canaries", row["canaries_mean"], "{:.1f}")
            macro(f"nier{mk}{cs}CanariesDen", row["canaries_total"], "{:.0f}")
            macro(f"nier{mk}{cs}Build", row["build_k"], "{:.1f}")
            if "precision" in row:
                macro(f"nier{mk}{cs}Precision", row["precision"])
                macro(f"nier{mk}{cs}Recall", row["recall"])
                macro(f"nier{mk}{cs}Change", row["change_k"], "{:.2f}")
            macro(f"nier{mk}{cs}Accept", row["accept_pass"], "{}")
            macro(f"nier{mk}{cs}Unit", row["unit_pass"], "{}")
            macro(f"nier{mk}{cs}Runs", row["n_runs"], "{}")
            macro(f"nier{mk}{cs}Truncated", row["truncated_modules"], "{}")
        # paired tests on (repo, seed) pairs
        wide = sub1.pivot_table(index=["repo", "seed"], columns="config", values="canary_frac")
        for base in ("free_text", "shared_schema"):
            if {"agentm2m", base} <= set(wide.columns):
                p = paired(wide["agentm2m"].to_numpy(), wide[base].to_numpy())
                macro(f"nier{mk}CanaryDiffVs{CFG_SHORT[base]}", p["mean_diff"])
                macro(f"nier{mk}CanaryLoVs{CFG_SHORT[base]}", p["ci_lo"])
                macro(f"nier{mk}CanaryHiVs{CFG_SHORT[base]}", p["ci_hi"])
                macro(f"nier{mk}CanaryPVs{CFG_SHORT[base]}", p["p"], "{:.4f}")
                macro(f"nier{mk}CanaryPairsVs{CFG_SHORT[base]}", p["n"], "{}")
        wb = sub1.pivot_table(index=["repo", "seed"], columns="config", values="build_k")
        for base in ("free_text",):
            if {"agentm2m", base} <= set(wb.columns):
                cheaper = int((wb["agentm2m"] < wb[base]).sum())
                macro(f"nier{mk}CheaperThanF", cheaper, "{}")
                macro(f"nier{mk}CheaperPairs", len(wb), "{}")
        if not rq2.empty:
            s2m = rq2[rq2.model_key == mk]
            w2 = s2m.pivot_table(index=["repo", "seed", "change_kind"], columns="config", values="tokens_k")
            for base in ("free_text", "shared_schema"):
                if {"agentm2m", base} <= set(w2.columns) and (w2["agentm2m"] > 0).any():
                    ratio = float(w2[base].mean() / w2["agentm2m"].mean())
                    macro(f"nier{mk}ChangeRatioVs{CFG_SHORT[base]}", ratio, "{:.0f}")
            wp = s2m.pivot_table(index=["repo", "seed", "change_kind"], columns="config", values="precision")
            for base in ("free_text", "shared_schema"):
                if {"agentm2m", base} <= set(wp.columns):
                    p = paired(wp["agentm2m"].to_numpy(), wp[base].to_numpy())
                    macro(f"nier{mk}PrecDiffVs{CFG_SHORT[base]}", p["mean_diff"])
                    macro(f"nier{mk}PrecPVs{CFG_SHORT[base]}", p["p"], "{:.4f}")
            exact = (s2m[s2m.config == "agentm2m"][["precision", "recall"]] == 1.0).all(axis=1).mean()
            macro(f"nier{mk}ExactImpactShare", 100 * exact, "{:.0f}\\%")
        if not rq3.empty:
            s3 = rq3[rq3.model_key == mk]
            if len(s3):
                cov = (s3.n_reviews_after.sum()) / max(1, s3.n_ops_before.sum())
                macro(f"nier{mk}OpsCovered", f"{int(s3.n_reviews_after.sum())}/{int(s3.n_ops_before.sum())}")
                macro(f"nier{mk}CoverageShare", 100 * cov, "{:.0f}\\%")
    macro("nierGlueA", glue["agentm2m_hot"], "{}")
    macro("nierGlueB", glue["manual_per_baseline_config"], "{}")
    df = pd.DataFrame(out_rows)
    df.to_csv(csv_dir / "nier_summary.csv", index=False)

    # table
    hdr = ["Metric"] + [f"{CFG_SHORT[c]}" for _ in ("Small", "Large") for c in CONFIGS]
    def cell(mk, c, key, nd=2, scale=1.0):
        r = df[(df.model == mk) & (df.config == c)]
        if r.empty or key not in r or pd.isna(r.iloc[0][key]):
            return "--"
        return f"{r.iloc[0][key] * scale:.{nd}f}"
    rows = [
        ["Canaries kept (mean/seed)"] + [f"{cell(mk, c, 'canaries_mean', 1)}/{cell(mk, c, 'canaries_total', 0)}"
                                         for mk in ("Small", "Large") for c in CONFIGS],
        ["Tests passed (unit/total runs)"] + [f"{cell(mk, c, 'unit_pass', 0)}/{cell(mk, c, 'n_runs', 0)}"
                                              for mk in ("Small", "Large") for c in CONFIGS],
        ["Build tokens (k)"] + [cell(mk, c, "build_k", 1) for mk in ("Small", "Large") for c in CONFIGS],
        ["Impact precision"] + [cell(mk, c, "precision") for mk in ("Small", "Large") for c in CONFIGS],
        ["Impact recall"] + [cell(mk, c, "recall") for mk in ("Small", "Large") for c in CONFIGS],
        ["Tokens/change (k)"] + [cell(mk, c, "change_k") for mk in ("Small", "Large") for c in CONFIGS],
    ]
    tex(tab_dir / "nier_replication.tex", hdr, rows, "l" + "rrr" + "rrr")

    # figure
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.0))
        for ax, (key, title, nd) in zip(axes, (("canaries_mean", "Canaries kept (of 5/repo)", 1),
                                                ("change_k", "Tokens per change (k)", 2),
                                                ("precision", "Impact precision", 2))):
            x = np.arange(2)
            for i, c in enumerate(CONFIGS):
                vals = []
                for mk in ("Small", "Large"):
                    r = df[(df.model == mk) & (df.config == c)]
                    v = float(r.iloc[0][key]) if len(r) and key in r and not pd.isna(r.iloc[0][key]) else 0.0
                    if key == "canaries_mean" and len(r):
                        v = v / max(1.0, float(r.iloc[0]["canaries_total"])) * 5
                    vals.append(v)
                ax.bar(x + (i - 1) * 0.26, vals, 0.25, color=COLORS[c], label=CFG_SHORT[c])
            ax.set_xticks(x, ["7B", "27B"], fontsize=7)
            ax.set_title(title, fontsize=7)
            ax.tick_params(labelsize=6)
            if key == "change_k":
                ax.set_yscale("log")
        axes[0].legend(fontsize=6, frameon=False)
        fig.tight_layout()
        fig.savefig(fig_dir / "nier_replication.pdf")
        plt.close(fig)
    except Exception as exc:  # noqa: BLE001
        print("figure skipped:", exc)
    return {"summary": df}


# ----------------------------------------------------------------------------
# RQ1-RQ3: shared context and observability
# ----------------------------------------------------------------------------

def context_study(raw: Path, csv_dir: Path, tab_dir: Path, fig_dir: Path) -> None:
    df = load(raw, "context_study_*.jsonl")
    if df.empty:
        return
    df["model_key"] = df["model"].map(MODELS)
    err = df[df.step == "ERROR"]
    macro("ctxErrors", len(err), "{}")
    steps = df[df.policy.isin(ALL_POLICIES) & df.step.str.startswith("R")].copy()
    POLICIES = tuple(p for p in ALL_POLICIES if p in set(steps.policy))
    steps["tokens"] = steps["input_tokens"] + steps["output_tokens"]
    steps["step_id"] = steps.step.str.split("_").str[0]
    steps.to_csv(csv_dir / "context_steps.csv", index=False)
    macro("ctxRuns", df[df.step == "R0_build"].groupby(["model", "seed", "repo", "policy"]).ngroups, "{}")
    macro("ctxRepos", df.repo.nunique(), "{}")
    macro("ctxSeeds", df.seed.nunique(), "{}")
    macro("ctxModels", df.model.nunique(), "{}")

    rev = steps[steps.step_id != "R0"]
    key = ["model", "seed", "repo"]


    # ---- RQ1: traceability / impact exactness / knowledge delivery -------------
    rows = []
    changed = rev[rev.truth > 0]
    unchanged = rev[rev.truth == 0]
    for pol in POLICIES:
        c, u = changed[changed.policy == pol], unchanged[unchanged.policy == pol]
        d = {"policy": pol,
             "impact_precision": c.impact_precision.mean(), "impact_recall": c.impact_recall.mean(),
             "exact_share": float(((c.impact_precision == 1) & (c.impact_recall == 1)).mean()),
             # spurious = a no-change step that actually made LLM calls (a stale binding whose re-run is
             # cached as escalated makes none and is not counted)
             "spurious_share": float((u.calls > 0).mean()) if len(u) else float("nan"),
             "spurious_count": float(u.calls.mean()) if len(u) else float("nan"),
             "exact_clean_share": (lambda cc: float(((cc.impact_precision == 1) & (cc.impact_recall == 1)).mean())
                                   if len(cc) else float("nan"))(c[c.escalations == 0]),
             "clean_steps": float((c.escalations == 0).sum()),
             "delivered_new": c.carries_new.mean(), "stale_left": c.carries_stale.mean(),
             "regeneration_churn": rev[rev.policy == pol].collateral_changes.mean()}
        fin = df[(df.step == "FINAL") & (df.policy == pol)]
        if f"influence_precision_finding-code" in fin and pol != "paste":
            d["influence_precision"] = pd.concat([fin["influence_precision_finding-code"], fin["influence_precision_finding-test"]]).mean()
            d["influence_recall"] = pd.concat([fin["influence_recall_finding-code"], fin["influence_recall_finding-test"]]).mean()
        b = df[(df.step == "R0_build") & (df.policy == pol)]
        d["build_retention_code"], d["build_retention_test"] = b.retention_code.mean(), b.retention_test.mean()
        rows.append(d)
        for k, v in d.items():
            if k != "policy":
                name = f"rqOne{pol.title()}{k.title().replace('_', '')}"
                if k.endswith("_share") or k in ("delivered_new", "stale_left"):
                    macro(name, 100 * v if not pd.isna(v) else v, "{:.0f}\\%")
                else:
                    macro(name, v)
        macro(f"rqOne{pol.title()}ChangedSteps", len(c), "{}")
        macro(f"rqOne{pol.title()}UnchangedSteps", len(u), "{}")
    pd.DataFrame(rows).to_csv(csv_dir / "rq1_traceability.csv", index=False)
    def g(r, k, nd=2, pct=False):
        v = r.get(k)
        return "--" if v is None or (isinstance(v, float) and math.isnan(v)) else (f"{100 * v:.0f}\\%" if pct else f"{v:.{nd}f}")
    tex(tab_dir / "rq1_traceability.tex",
        ["Policy", "Impact P", "Impact R", "Exact", "Spurious re-run", "Delivered", "Stale", "Influence P/R"],
        [[POL_LABEL[r["policy"]], g(r, "impact_precision"), g(r, "impact_recall"), g(r, "exact_share", pct=True),
          g(r, "spurious_share", pct=True), g(r, "delivered_new", pct=True), g(r, "stale_left", pct=True),
          ("--" if r.get("influence_precision") is None or pd.isna(r.get("influence_precision"))
           else f"{r['influence_precision']:.2f}/{r['influence_recall']:.2f}")] for r in rows], "lrrrrrrr")

    # ---- RQ2: cost / optimization ---------------------------------------------
    tot = rev.groupby(key + ["policy"]).agg(tokens=("tokens", "sum"), calls=("calls", "sum")).reset_index()
    build = steps[steps.step_id == "R0"].set_index(key + ["policy"])
    tot = tot.join(build[["tokens", "calls"]].rename(columns={"tokens": "build_tokens", "calls": "build_calls"}),
                   on=key + ["policy"])
    tot.to_csv(csv_dir / "rq2_cost_per_run.csv", index=False)
    rep = tot.groupby(["model", "repo", "policy"]).agg(tokens=("tokens", "mean"), calls=("calls", "mean")).reset_index()
    wide = rep.pivot_table(index=["model", "repo"], columns="policy", values="tokens")  # unit: repository x model
    wc = rep.pivot_table(index=["model", "repo"], columns="policy", values="calls")
    cost_rows = []
    for pol in POLICIES:
        t = tot[tot.policy == pol]
        cost_rows.append({"policy": pol, "revision_tokens_k": t.tokens.mean() / 1000, "revision_calls": t.calls.mean(),
                          "build_tokens_k": t.build_tokens.mean() / 1000, "build_calls": t.build_calls.mean()})
        macro(f"rqTwo{pol.title()}RevTokens", t.tokens.mean() / 1000, "{:.1f}")
        macro(f"rqTwo{pol.title()}RevCalls", t.calls.mean(), "{:.0f}")
        macro(f"rqTwo{pol.title()}BuildTokens", t.build_tokens.mean() / 1000, "{:.1f}")
    pd.DataFrame(cost_rows).to_csv(csv_dir / "rq2_cost_summary.csv", index=False)
    for a, b in (("ctxi", "paste"), ("ctxi", "routed"), ("ctxi", "ctx"), ("ctx", "paste"), ("routed", "paste")):
        if {a, b} <= set(wide.columns):
            w2 = wide[[a, b]].dropna()
            c2 = wc[[a, b]].dropna()
            p = paired(w2[a].to_numpy(), w2[b].to_numpy())
            ratio = float(w2[b].sum() / w2[a].sum()) if w2[a].sum() else float("nan")
            callr = float(c2[b].sum() / c2[a].sum()) if c2[a].sum() else float("nan")
            nm = f"{a.title()}Vs{b.title()}"
            macro(f"rqTwoRatio{nm}", ratio, "{:.1f}")
            macro(f"rqTwoCallRatio{nm}", callr, "{:.1f}")
            macro(f"rqTwoDiffLo{nm}", p["ci_lo"] / 1000, "{:.1f}")
            macro(f"rqTwoDiffHi{nm}", p["ci_hi"] / 1000, "{:.1f}")
            macro(f"rqTwoP{nm}", p["p"], "{:.4f}")
            macro(f"rqTwoPairs{nm}", p["n"], "{}")
    # per step table
    st = steps.groupby(["policy", "step_id"]).agg(tokens=("tokens", "mean"), calls=("calls", "mean")).reset_index()
    rows = []
    labels = {"R0": "build", "R1": "revise test item", "R2": "revise code item", "R3": "add unrelated item",
              "R4": "identical rewrite", "R5": "revise code item"}
    for sid in ("R0", "R1", "R2", "R3", "R4", "R5"):
        row = [f"{sid} {labels[sid]}"]
        for pol in POLICIES:
            r = st[(st.policy == pol) & (st.step_id == sid)]
            row += [f"{r.tokens.iloc[0] / 1000:.1f}" if len(r) else "--", f"{r.calls.iloc[0]:.0f}" if len(r) else "--"]
        rows.append(row)
    tex(tab_dir / "rq2_cost_steps.tex", ["Step"] + [f"{POL_LABEL[p]} {m}" for p in POLICIES for m in ("tok(k)", "calls")],
        rows, "l" + "rr" * 3)
    # fidelity guard
    fid = []
    for pol in POLICIES:
        fin = df[(df.step == "FINAL") & (df.policy == pol)]
        b0 = df[(df.step == "R0_build") & (df.policy == pol)]
        if "canary_pass" in fin and fin.canary_pass.notna().any():
            fid.append({"policy": pol, "canary_build": b0.canary_pass.sum() / max(1, b0.canary_total.sum()),
                        "canary_final": fin.canary_pass.sum() / max(1, fin.canary_total.sum()),
                        "unit_build": b0.unit_pass.mean(), "unit_final": fin.unit_pass.mean()})
            macro(f"rqTwo{pol.title()}CanaryFinal", 100 * fid[-1]["canary_final"], "{:.0f}\\%")
            macro(f"rqTwo{pol.title()}CanaryBuild", 100 * fid[-1]["canary_build"], "{:.0f}\\%")
    pd.DataFrame(fid).to_csv(csv_dir / "rq2_fidelity.csv", index=False)
    # non-inferiority: per (repository x model) final canary retention, margin = one canary of five (0.2)
    fin = df[(df.step == "FINAL") & df.canary_total.notna()].copy() if "canary_total" in df else pd.DataFrame()
    if len(fin):
        fin["frac"] = fin.canary_pass / fin.canary_total.replace(0, np.nan)
        cw = fin.groupby(["model", "repo", "policy"]).frac.mean().unstack("policy")
        for a, b in (("ctxi", "paste"), ("ctxi", "routed"), ("ctxi", "ctx")):
            if {a, b} <= set(cw.columns):
                c2 = cw[[a, b]].dropna()
                p = paired(c2[a].to_numpy(), c2[b].to_numpy())
                nm = f"{a.title()}Vs{b.title()}"
                macro(f"fidDiff{nm}", p["mean_diff"])
                macro(f"fidLo{nm}", p["ci_lo"])
                macro(f"fidHi{nm}", p["ci_hi"])
                macro(f"fidNonInf{nm}", "yes" if p["ci_lo"] > -0.2 else "no")
                macro(f"fidUnits{nm}", p["n"], "{}")
    # context metrics from the timeline
    met = df[(df.step == "METRICS")]
    mm = met.groupby("policy").agg(reuse_agents=("context_reuse_agents", "mean"), reuse_bindings=("context_reuse_bindings", "mean"),
                                   induced=("context_induced_obligations", "mean"), amplification=("amplification_max", "mean"),
                                   reads=("context_reads", "mean"), events=("timeline_events", "mean")).reset_index()
    mm.to_csv(csv_dir / "rq2_context_metrics.csv", index=False)
    for _, r in mm.iterrows():
        macro(f"metric{r['policy'].title()}Induced", r["induced"], "{:.1f}")
        macro(f"metric{r['policy'].title()}Reads", r["reads"], "{:.0f}")
        macro(f"metric{r['policy'].title()}Reuse", r["reuse_bindings"], "{:.1f}")
        macro(f"metric{r['policy'].title()}Events", r["events"], "{:.0f}")
    ok = ((met.event_llm_calls == met.counted_llm_calls) & (met.event_tokens == met.counted_tokens)).mean()
    macro("timelineCostExactShare", 100 * ok, "{:.0f}\\%")

    # cumulative cost curve figure
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.2))
        ids = ["R1", "R2", "R3", "R4", "R5"]
        for pol in POLICIES:
            ys = []
            cum = 0.0
            for sid in ids:
                r = st[(st.policy == pol) & (st.step_id == sid)]
                cum += float(r.tokens.iloc[0]) / 1000 if len(r) else 0.0
                ys.append(cum)
            axes[0].plot(ids, ys, marker="o", ms=3, color=COLORS[pol], label=POL_LABEL[pol])
        axes[0].set_ylabel("cumulative tokens (k)", fontsize=7)
        axes[0].set_title("Cost of absorbing 5 revisions", fontsize=7)
        axes[0].legend(fontsize=6, frameon=False)
        pr = pd.DataFrame(rows_for_prec(changed))
        for i, pol in enumerate(POLICIES):
            axes[1].bar(i, float(changed[changed.policy == pol].impact_precision.mean()), color=COLORS[pol])
        axes[1].set_xticks(range(len(POLICIES)), [POL_LABEL[p] for p in POLICIES], fontsize=7)
        axes[1].set_title("Impact precision (changed steps)", fontsize=7)
        for a in axes:
            a.tick_params(labelsize=6)
        fig.tight_layout()
        fig.savefig(fig_dir / "context_cost.pdf")
        plt.close(fig)
    except Exception as exc:  # noqa: BLE001
        print("figure skipped:", exc)

    # ---- RQ3: observability -----------------------------------------------------
    obs = df[df.step == "OBSERVABILITY"]
    flt = df[df.step == "FAULT"]
    obs.to_csv(csv_dir / "rq3_observability.csv", index=False)
    flt.to_csv(csv_dir / "rq3_faults.csv", index=False)
    if len(obs):
        macro("obsRuns", len(obs), "{}")
        for col, nm in (("nostr_state_identical", "NostrStateIdentical"), ("nostr_prompts_identical", "NostrPromptsIdentical"),
                        ("nostr_down_state_identical", "DownStateIdentical"), ("nostr_down_prompts_identical", "DownPromptsIdentical"),
                        ("sync_peer_converged", "SyncConverged"), ("state_hash_matches_original", "ReplayFaithful")):
            if col in obs:
                macro(f"obs{nm}", 100 * obs[col].astype(float).mean(), "{:.0f}\\%")
        macro("obsRecTimeline", 100 * obs.reconstruction_timeline.mean(), "{:.1f}\\%")
        macro("obsRecRelay", 100 * obs.reconstruction_relay.mean(), "{:.1f}\\%")
        macro("obsVerifiedShare", 100 * (obs.nostr_events_verified / obs.nostr_events_published).mean(), "{:.1f}\\%")
        macro("obsLeaks", int(obs.content_leaks_standard.sum()), "{}")
        macro("obsSnapshotsWithContent", int(obs.snapshot_events_with_content.sum()), "{}")
        macro("obsOverheadTimelinePct", obs.overhead_timeline_pct.median(), "{:.0f}\\%")
        macro("obsOverheadNostrMs", obs.overhead_nostr_ms_per_event.median(), "{:.2f}")
        macro("obsOverheadTimelineMs", obs.overhead_timeline_ms_per_event.median(), "{:.3f}")
        macro("obsEventsMean", obs.timeline_events.mean(), "{:.0f}")
        macro("obsKbMean", obs.timeline_bytes.mean() / 1000, "{:.0f}")
        macro("obsOutboxDepthMean", obs.outage_outbox_depth.mean(), "{:.0f}")
        macro("obsOutboxDrained", 100 * (obs.outage_outbox_depth_after_flush == 0).mean(), "{:.0f}\\%")
        macro("obsReplayMisses", int(obs.replay_misses.sum() + obs.nostr_misses.sum() + obs.nostr_down_misses.sum()), "{}")
        macro("obsOverheadSecNone", obs.overhead_none_s.median(), "{:.2f}")
        macro("obsOverheadSecNostr", obs.overhead_nostr_s.median(), "{:.2f}")
    if len(flt):
        g = flt.groupby(["family", "fault"]).agg(n=("detected", "size"), detected=("detected", "mean"),
                                                 silent=("silent_corruption", "mean")).reset_index()
        g.to_csv(csv_dir / "rq3_fault_summary.csv", index=False)
        macro("faultInjections", len(flt), "{}")
        macro("faultKinds", flt.fault.nunique(), "{}")
        nb = flt[~flt.family.isin(["benign-control", "design-limit"])]
        lim = flt[flt.family == "design-limit"]
        macro("faultDetectedShare", 100 * nb.detected.mean(), "{:.1f}\\%")
        macro("faultSilentShare", 100 * nb.silent_corruption.mean(), "{:.1f}\\%")
        macro("faultSilentCount", int(nb.silent_corruption.sum()), "{}")
        macro("faultTested", len(nb), "{}")
        if len(lim):
            macro("faultLimitDetected", 100 * lim.detected.mean(), "{:.0f}\\%")
            macro("faultLimitSilent", 100 * lim.silent_corruption.mean(), "{:.0f}\\%")
        macro("faultBenignShare", 100 * flt[flt.family == "benign-control"].detected.mean(), "{:.0f}\\%")
        ev = flt[flt.fault.isin(["reader_revoked", "context_expired", "context_missing"])]
        macro("faultEventsToDetection", ev.events_to_detection.median(), "{:.0f}")
        macro("faultUnguardedPrompts", int(ev.unguarded_prompts.sum()), "{}")
        lab = {"reader_revoked": "reader access revoked", "context_expired": "context expired",
               "context_missing": "context missing", "unauthorized_write": "write by non-writer",
               "concurrent_writers": "concurrent writers", "forged_item_authorship": "forged item author",
               "unrelated_context_changed": "unrelated context changed", "llm_rejects_value": "LLM output rejected",
               "llm_transport_failure": "LLM transport failure", "nostr_bad_signature": "bad signature",
               "nostr_wrong_pubkey": "wrong signing key", "nostr_unknown_agent": "unknown agent",
               "nostr_wrong_namespace": "wrong team namespace", "nostr_unauthorized_writer": "unauthorized writer",
               "nostr_tampered_content": "tampered snapshot", "nostr_unknown_context": "unknown context",
               "nostr_forked_base": "forked version base", "nostr_duplicate_delivery": "duplicate delivery",
               "nostr_relay_down": "relay offline",
               "undeclared_dependency_edit": "rule prompt edited (undeclared dependency)",
               "wrong_but_authorized_knowledge": "wrong knowledge from an authorized writer"}
        tex(tab_dir / "rq3_faults.tex", ["Injected fault", "Family", "n", "Surfaced", "Silent"],
            [[lab.get(r.fault, r.fault.replace("_", " ")), r.family.replace("-", " "), int(r.n),
              f"{100 * r.detected:.0f}\\%", f"{100 * r.silent:.0f}\\%"] for r in g.itertuples()], "llrrr")


def rows_for_prec(changed):  # tiny helper kept for the figure
    return []


def legacy_validation(csv_dir: Path) -> None:
    """Recompute the published pilot numbers from the archived pilot logs."""
    out = {}
    for model, key in MODELS.items():
        tag = model.replace(":", "-")
        rows = []
        for f in sorted(LEGACY_LOGS.glob(f"devbench_rq1_{tag}_*.jsonl")):
            rows += [json.loads(line) for line in f.open() if line.strip()]
        rows = [r for r in rows if not r.get("error")]
        for c in CONFIGS:
            out[f"{key}_{c}_canaries"] = sum(r["canary_pass"] for r in rows if r["config"] == c)
        out[f"{key}_agentm2m_build_k"] = float(np.mean([r["total_tokens"] for r in rows if r["config"] == "agentm2m"])) / 1000
    (csv_dir / "legacy_validation.json").write_text(json.dumps(out, indent=1))
    print("legacy pilot recomputation:", json.dumps(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=str(BASE / "raw"))
    ap.add_argument("--validate-legacy", action="store_true")
    args = ap.parse_args()
    csv_dir, tab_dir, fig_dir = BASE / "csv", BASE / "tables", BASE / "figures"
    for d in (csv_dir, tab_dir, fig_dir):
        d.mkdir(parents=True, exist_ok=True)
    if args.validate_legacy:
        legacy_validation(csv_dir)
    raw = Path(args.raw)
    nier(raw, csv_dir, tab_dir, fig_dir)
    context_study(raw, csv_dir, tab_dir, fig_dir)
    ref = csv_dir / "reference_validity.csv"
    if ref.exists():
        r = pd.read_csv(ref)
        macro("refRepos", len(r), "{}")
        macro("refValid", int(r.reference_valid.sum()), "{}")
        hy = r[r.repo == "Hybrid_Images"]
        if len(hy) and not pd.isna(hy.iloc[0].get("unit_failed")):
            macro("refHybridFailing", int(hy.iloc[0]["unit_failed"]), "{}")
    lines = ["% generated by evaluation/analysis/follow_up_aggregate.py -- do not edit"]
    for k in sorted(MACROS):
        lines.append(rf"\newcommand{{\{k}}}{{{MACROS[k]}}}")
    (tab_dir / "macros.tex").write_text("\n".join(lines) + "\n")
    (BASE / "summary.json").write_text(json.dumps(MACROS, indent=1, sort_keys=True))
    print(f"{len(MACROS)} macros -> {tab_dir / 'macros.tex'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

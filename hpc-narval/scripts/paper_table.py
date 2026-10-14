#!/usr/bin/env python3
"""Rebuild the paper's Table `tab:results` (per model x {free text F, shared schema S, AgentM2M A}) from the raw
DevBench logs, plus the RQ1 decision rule (paired bootstrap, AgentM2M vs shared schema).
Writes tab_results.{csv,md,tex} and rq1_decision.json under --out."""
import argparse, json, os
from pathlib import Path
from statistics import mean

ap = argparse.ArgumentParser()
ap.add_argument("--logs", required=True); ap.add_argument("--out", required=True)
ap.add_argument("--models", nargs="+", default=["qwen2.5-coder:7b", "qwen3.8:27b"])
a = ap.parse_args()
os.environ["DEVBENCH_LOGS_DIR"] = a.logs
from evaluation.analysis.aggregate_devbench import _load_jsonl, canary_bootstrap, compute_rq1, compute_rq2, compute_rq3, discover
from evaluation.harness.hot_reviewer_rq3 import glue_line_counts

logs = Path(a.logs); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
MODELS = a.models; CFG = [("free_text", "F"), ("shared_schema", "S"), ("agentm2m", "A")]
rows, decision = [], {}
def add(metric, model, vals): rows.append({"metric": metric, "model": model, **{k: vals.get(c) for c, k in CFG}})
for m in MODELS:
    r1 = _load_jsonl(discover("rq1", logs_dir=logs, model=m)); r2 = _load_jsonl(discover("rq2", logs_dir=logs, model=m))
    r3 = _load_jsonl(discover("rq3", logs_dir=logs, model=m))
    if not r1:
        continue
    ok = [r for r in r1 if "error" not in r]
    s1 = compute_rq1(ok); s2 = compute_rq2(r2) if r2 else {}
    add("RQ1 canaries in code", m, {c: sum(r["canary_pass"] for r in ok if r["config"] == c) for c, _ in CFG})
    add("RQ1 canaries total", m, {c: sum(r["canary_total"] for r in ok if r["config"] == c) for c, _ in CFG})
    add("RQ1 acceptance passed", m, {c: sum(bool(r.get("acceptance_pass")) for r in ok if r["config"] == c) for c, _ in CFG})
    add("RQ1 repos run", m, {c: sum(1 for r in ok if r["config"] == c) for c, _ in CFG})
    add("RQ1 MAST P1 per trace", m, {c: round(s1[c]["p1_per_trace"], 2) if s1.get(c) else None for c, _ in CFG})
    add("RQ1 MAST P2 per trace", m, {c: round(mean(r.get("mast_p2_count", 0) for r in ok if r["config"] == c), 2) if s1.get(c) else None for c, _ in CFG})
    add("RQ1 build tokens (k)", m, {c: round(s1[c]["tokens_k"], 2) if s1.get(c) else None for c, _ in CFG})
    for key, lab in (("precision", "RQ2 impact precision"), ("recall", "RQ2 impact recall"), ("tokens_k", "RQ2 tokens per change (k)")):
        add(lab, m, {c: round(s2[c][key], 2) if s2.get(c) else None for c, _ in CFG})
    add("RQ2 changes", m, {c: s2[c]["n"] if s2.get(c) else None for c, _ in CFG})
    if r3:
        s3 = compute_rq3(r3, glue_counts=glue_line_counts())
        rows.append({"metric": "RQ3 retroactive coverage (%)", "model": m, "F": None, "S": None, "A": round(s3["retroactive_coverage_pct"], 1)})
        rows.append({"metric": "RQ3 glue lines", "model": m, "F": s3["glue_lines_manual"], "S": s3["glue_lines_manual"], "A": s3["glue_lines_agentm2m"]})
    decision[m] = canary_bootstrap(s1)
import csv
with (out / "tab_results.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["metric", "model", "F", "S", "A"]); w.writeheader(); w.writerows(rows)
fmt = lambda v: "-" if v is None else str(v)
md = ["| Metric | Model | F | S | A |", "|---|---|---|---|---|"] + [f"| {r['metric']} | {r['model']} | {fmt(r['F'])} | {fmt(r['S'])} | {fmt(r['A'])} |" for r in rows]
(out / "tab_results.md").write_text("\n".join(md) + "\n")
tex = ["\\begin{tabular}{llrrr}", "\\toprule", "Metric & Model & F & S & A \\\\", "\\midrule"] + \
      [f"{r['metric']} & \\texttt{{{r['model']}}} & {fmt(r['F'])} & {fmt(r['S'])} & {fmt(r['A'])} \\\\" for r in rows] + ["\\bottomrule", "\\end{tabular}"]
(out / "tab_results.tex").write_text("\n".join(tex) + "\n")
(out / "rq1_decision.json").write_text(json.dumps(decision, indent=1))
print("\n".join(md))

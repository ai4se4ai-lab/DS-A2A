"""Per-repository breakdown of the DevBench pilot, complementing
aggregate_devbench.py's per-model pooling with the axis that script does
not report: how each metric varies *across the 6 evaluated repositories*
(chakin, geotext, readtime, particle-swarm-optimization, Hybrid_Images,
stocktrends -- the disclosed scope in evaluation/README.md), for each of
the 3 models, restricted to the AgentM2M config (the pipeline under
evaluation; the free-text/shared-schema contrast is already reported,
pooled across repos, in docs/eval_summary.tex's tab:pooled).

Reads the same evaluation/harness/logs/devbench_rq{1,2,3}_<model>_*.jsonl
this repo already writes; adds nothing new to the harness. Writes
evaluation/results/csv/devbench_per_repo.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from .aggregate_devbench import LOGS_DIR, RESULTS_CSV_DIR, _load_jsonl, discover

MODELS = ["qwen2.5-coder:7b", "devstral:24b", "qwen3-coder:30b"]
REPOS = ["chakin", "geotext", "readtime", "particle-swarm-optimization", "Hybrid_Images", "stocktrends"]


def _rq1_cell(records: list[dict], repo: str, model: str) -> dict | None:
    rows = [r for r in records if r.get("repo") == repo and r.get("model") == model
            and r.get("config") == "agentm2m" and "error" not in r]
    if not rows:
        return None
    r = rows[0]
    canary = (r["canary_pass"] / r["canary_total"]) * 100.0 if r.get("canary_total") else 0.0
    return {
        "canary_retention_pct": canary,
        "p1_count": r.get("mast_p1_count", 0),
        "acceptance_pct": 100.0 if r.get("acceptance_pass") else 0.0,
        "tokens_k": r.get("total_tokens", 0) / 1000.0,
    }


def _rq2_cell(records: list[dict], repo: str, model: str) -> dict | None:
    rows = [r for r in records if r.get("repo") == repo and r.get("model") == model
            and r.get("config") == "agentm2m"]
    if not rows:
        return None
    return {
        "precision": mean(r["precision"] for r in rows),
        "recall": mean(r["recall"] for r in rows),
        "tokens_k": mean(r.get("tokens", 0) for r in rows) / 1000.0,
        "n": len(rows),
    }


def _rq3_cell(records: list[dict], repo: str, model: str) -> dict | None:
    rows = [r for r in records if r.get("repo") == repo and r.get("model") == model and "error" not in r]
    if not rows:
        return None
    return {"retroactive_coverage_pct": rows[0]["retroactive_coverage"] * 100.0}


def build(logs_dir: Path | None = None) -> dict:
    d = logs_dir or LOGS_DIR
    rq1_records = _load_jsonl(discover("rq1", logs_dir=d))
    rq2_records = _load_jsonl(discover("rq2", logs_dir=d))
    rq3_records = _load_jsonl(discover("rq3", logs_dir=d))

    out: dict = {"repos": REPOS, "models": MODELS, "by_repo": {}}
    for repo in REPOS:
        per_model = {}
        for model in MODELS:
            per_model[model] = {
                "rq1": _rq1_cell(rq1_records, repo, model),
                "rq2": _rq2_cell(rq2_records, repo, model),
                "rq3": _rq3_cell(rq3_records, repo, model),
            }
        present = [m for m in MODELS if per_model[m]["rq1"] is not None]
        pooled = None
        if present:
            pooled = {
                "canary_retention_pct": mean(per_model[m]["rq1"]["canary_retention_pct"] for m in present),
                "p1_count": mean(per_model[m]["rq1"]["p1_count"] for m in present),
                "acceptance_pct": mean(per_model[m]["rq1"]["acceptance_pct"] for m in present),
                "tokens_k": mean(per_model[m]["rq1"]["tokens_k"] for m in present),
            }
            rq2_present = [m for m in present if per_model[m]["rq2"] is not None]
            if rq2_present:
                pooled["rq2_precision"] = mean(per_model[m]["rq2"]["precision"] for m in rq2_present)
                pooled["rq2_recall"] = mean(per_model[m]["rq2"]["recall"] for m in rq2_present)
            rq3_present = [m for m in present if per_model[m]["rq3"] is not None]
            if rq3_present:
                pooled["retroactive_coverage_pct"] = mean(per_model[m]["rq3"]["retroactive_coverage_pct"] for m in rq3_present)
        out["by_repo"][repo] = {"per_model": per_model, "pooled": pooled, "n_models_present": len(present)}
    return out


def main() -> int:
    data = build()
    RESULTS_CSV_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_CSV_DIR / "devbench_per_repo.json"
    out_path.write_text(json.dumps(data, indent=2, default=str))
    print(json.dumps(data, indent=2, default=str))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

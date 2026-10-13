#!/usr/bin/env python3
"""Setup gate: each model answers one short prompt through the running Ollama server (loads weights on the GPU)."""
import argparse, json, os, time
import requests
ap = argparse.ArgumentParser(); ap.add_argument("--models", nargs="+", required=True); ap.add_argument("--out", required=True)
a = ap.parse_args(); base = os.environ["OLLAMA_BASE_URL"]; out = {}
for m in a.models:
    t = time.time()
    r = requests.post(f"{base}/api/generate", json={"model": m, "prompt": "Reply with the single word: ready", "stream": False,
                      "think": False, "options": {"temperature": 0.2, "num_predict": 16}}, timeout=1800)
    r.raise_for_status(); j = r.json()
    out[m] = {"seconds": round(time.time() - t, 1), "response": j.get("response", "")[:80],
              "eval_count": j.get("eval_count"), "prompt_eval_count": j.get("prompt_eval_count")}
    print(m, out[m], flush=True)
    assert j.get("response", "").strip(), f"{m}: empty response"
os.makedirs(os.path.dirname(a.out), exist_ok=True); open(a.out, "w").write(json.dumps(out, indent=1))

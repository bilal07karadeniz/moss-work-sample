"""Summarises the brisvox A/B (run_ab.sh): client-side voice-to-voice from brisvox-bench, and per-turn
stage timings from the server's `turn finished` timeline lines. Writes results/ab.json.

Arms: `moss` (Moss, reply cache on: the default), `moss-nocache` (Moss through nocache_proxy.py, which adds
`X-Moss-Cache: off`) and `openrouter`. The fixture repeats the same two questions on every call, so in the
`moss` arm most turns are reply-cache hits. A hit shows in the timeline as a first sentence that arrives
with the first token (the whole cached reply comes in one chunk); those turns are reported separately.
Turns are paired with timeline lines by call order (one call at a time, session s1 = first call).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES = HERE / "results"
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STAGES = ("end_of_turn_delay", "llm_ttft", "llm_first_sentence", "tts_ttfb", "post_endpoint_latency")


def pct(vals: list[float], p: float) -> float | None:
    vals = sorted(vals)
    if not vals:
        return None
    return vals[max(0, min(len(vals) - 1, math.ceil(p / 100 * len(vals)) - 1))]


def summary(vals: list[float]) -> dict:
    return {"n": len(vals), "p50": pct(vals, 50), "p90": pct(vals, 90), "max": max(vals) if vals else None}


def block_files(which: str) -> list[tuple[Path, Path]]:
    pat = re.compile(rf"v2v-{re.escape(which)}-(\d+)\.json$")
    out = []
    for js in sorted(RES.glob("v2v-*.json")):
        m = pat.search(js.name)
        if m:
            out.append((js, RES / f"server-{which}-{m.group(1)}.log"))
    return out


def turns_of(which: str) -> tuple[list[dict], int]:
    rows, errors = [], 0
    for js, log in block_files(which):
        d = json.loads(js.read_text(encoding="utf-8"))
        bench = []
        for run in d["runs"]:
            errors += len(run.get("server_errors") or [])
            bench += [t for t in run["turns"]]
        timeline = []
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = ANSI.sub("", line)
            if "turn finished" in line:
                kv = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}
                sid = int(re.search(r"\bid=s(\d+)", line).group(1))
                timeline.append((sid, kv.get("epoch", 0), kv))
        timeline.sort(key=lambda x: (x[0], x[1]))
        if len(timeline) != len(bench):
            raise SystemExit(f"{js.name}: {len(bench)} bench turns vs {len(timeline)} timeline lines")
        for t, (_, _, kv) in zip(bench, timeline):
            st = {s: kv[s + "_us"] / 1000 for s in STAGES if s + "_us" in kv}
            hit = "llm_ttft" in st and st.get("llm_first_sentence", 0) - st["llm_ttft"] < 1.0
            rows.append({"outcome": t["outcome"], "v2v_ms": (t.get("v2v_us") or 0) / 1000, "stages": st, "cache_hit": hit})
    return rows, errors


def arm(rows: list[dict], errors: int) -> dict:
    ok = [r for r in rows if r["outcome"] == "answered"]
    return {"turns": len(rows), "answered": len(ok), "server_errors": errors,
            "v2v_ms": summary([r["v2v_ms"] for r in ok]),
            "server_stages_ms": {s: summary([r["stages"][s] for r in ok if s in r["stages"]]) for s in STAGES}}


def main() -> None:
    out: dict = {}
    for which in ("moss", "moss-nocache", "openrouter"):
        rows, errors = turns_of(which)
        if not rows:
            continue
        out[which] = arm(rows, errors)
        if which == "moss":
            out["moss: reply-cache hits"] = arm([r for r in rows if r["cache_hit"]], 0)
            out["moss: cache misses"] = arm([r for r in rows if not r["cache_hit"]], 0)
        if which == "moss-nocache":
            out[which]["cache_hit_like_turns"] = sum(r["cache_hit"] for r in rows)
    proxy = []
    for p in RES.glob("proxy-moss-nocache-*.jsonl"):
        proxy += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if proxy:
        out["proxy"] = {"requests": len(proxy), "reconnects": sum(bool(x.get("reconnected")) for x in proxy),
                        "statuses": sorted({x["status"] for x in proxy}),
                        "upstream_first_bytes_ms": summary([x["upstream_first_bytes_ms"] for x in proxy])}
    (RES / "ab.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for which, r in out.items():
        if which == "proxy":
            print("proxy:", r)
            continue
        st = r["server_stages_ms"]
        print(f"{which}: {r['answered']}/{r['turns']} answered, v2v p50 {r['v2v_ms']['p50']:.0f} p90 {r['v2v_ms']['p90']:.0f} | "
              + " ".join(f"{k} {v['p50']:.0f}" for k, v in st.items() if v["p50"] is not None))


if __name__ == "__main__":
    main()

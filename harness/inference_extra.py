"""Follow-up checks on the first inference run.

1. Network floor: TCP + TLS handshake time and a GET /models round trip, so model time can be told
   apart from the path between this machine and inference.moss.dev.
2. Responses vs Chat Completions streaming, interleaved (A B A B ...), so ordering and drift cannot
   explain a difference. Records `x-moss-box` (an undocumented reply header) per call.
3. Prefix cache on a long system prompt (~3k tokens): first call vs repeats with a new user turn.
4. Five calls at once (inside the 10-request burst).
5. Delta cadence: events per reply and the gap between deltas, which is what a TTS stage consumes.
Writes logs/inference_extra.jsonl and results/inference_extra.json.
"""

from __future__ import annotations

import json
import socket
import ssl
import statistics
import threading
import time

from client import ROOT, Moss, pct
from inference_tests import MODEL, NO_CACHE, QUESTIONS, SYSTEM


def network_floor(n: int = 10) -> dict:
    tcp, tls = [], []
    ctx = ssl.create_default_context()
    for _ in range(n):
        t0 = time.perf_counter()
        s = socket.create_connection(("inference.moss.dev", 443), timeout=10)
        t1 = time.perf_counter()
        ss = ctx.wrap_socket(s, server_hostname="inference.moss.dev")
        t2 = time.perf_counter()
        ss.close()
        tcp.append((t1 - t0) * 1000)
        tls.append((t2 - t1) * 1000)
        time.sleep(0.2)
    return {"tcp_connect_ms_p50": round(pct(tcp, 50), 1), "tls_handshake_ms_p50": round(pct(tls, 50), 1),
            "peer": socket.getaddrinfo("inference.moss.dev", 443)[0][4][0]}


def main() -> None:
    m = Moss("inference_extra")
    out: dict = {"started": time.time(), "network": network_floor()}
    models_rtt = []
    for _ in range(10):
        r = m.request("GET", "/models", test="floor.models")
        models_rtt.append(r.total_ms)
        time.sleep(0.5)
    out["network"]["get_models_warm_ms_p50"] = round(pct(models_rtt, 50), 1)

    # 2. interleaved
    rows = []
    for i in range(30):
        q = QUESTIONS[i % 20]
        if i % 2 == 0:
            r = m.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": q, "stream": True},
                         test="interleaved.responses", headers=NO_CACHE)
            api = "responses"
        else:
            r = m.stream("/chat/completions", {"model": MODEL, "stream": True, "messages": [
                {"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]},
                test="interleaved.chat", headers=NO_CACHE, kind="chat")
            api = "chat"
        deltas = [e["t_ms"] for e in r.events if (e["data"].get("type") == "response.output_text.delta") or
                  (e["data"].get("choices") and (e["data"]["choices"][0].get("delta") or {}).get("content"))]
        gaps = [b - a for a, b in zip(deltas, deltas[1:])]
        rows.append({"api": api, "status": r.status, "first_token_ms": r.first_token_ms, "total_ms": r.total_ms, "ttfb_ms": r.ttfb_ms,
                     "box": r.headers.get("x-moss-box"), "deltas": len(deltas),
                     "median_gap_ms": statistics.median(gaps) if gaps else None, "chars": len(r.text)})
        time.sleep(0.6)
    summ = {}
    out["failures"] = [{"api": x["api"], "status": x["status"]} for x in rows if x["status"] != 200]
    for api in ("responses", "chat"):
        sel = [x for x in rows if x["api"] == api and x["status"] == 200]
        summ[api] = {k: {"p50": round(pct([x[k] for x in sel], 50), 1), "p95": round(pct([x[k] for x in sel], 95), 1)}
                     for k in ("ttfb_ms", "first_token_ms", "total_ms")}
        summ[api]["deltas_per_reply_p50"] = pct([x["deltas"] for x in sel], 50)
        summ[api]["median_gap_ms_p50"] = round(pct([x["median_gap_ms"] for x in sel if x["median_gap_ms"]], 50), 1)
    boxes: dict = {}
    for x in rows:
        if x["status"] != 200:
            continue
        boxes.setdefault(x["box"], []).append(x["first_token_ms"])
    out["interleaved"] = {"summary": summ, "by_box": {b: {"n": len(v), "first_token_p50": round(pct(v, 50), 1),
                                                            "max": round(max(v), 1)} for b, v in boxes.items()},
                          "rows": rows}

    # 3. prefix cache on a long system prompt
    long_system = SYSTEM + " Clinic policy handbook follows. " + " ".join(
        f"Policy {n}: patients must confirm appointments {n % 5 + 1} days ahead, bring their insurance card, "
        f"and arrive ten minutes early; late arrivals over fifteen minutes are rescheduled." for n in range(90))
    pc = []
    for i in range(6):
        r = m.stream("/chat/completions", {"model": MODEL, "stream": True, "messages": [
            {"role": "system", "content": long_system}, {"role": "user", "content": QUESTIONS[i]}]},
            test="prefix_cache.long_system", headers=NO_CACHE, kind="chat")
        usage = next((e["data"].get("usage") for e in r.events if isinstance(e["data"], dict) and e["data"].get("usage")), None)
        pc.append({"i": i, "first_token_ms": r.first_token_ms, "prompt_tokens": (usage or {}).get("prompt_tokens"),
                   "cached_tokens": ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
                   "box": r.headers.get("x-moss-box")})
        time.sleep(0.8)
    out["prefix_cache"] = pc

    # 4. five at once
    conc: list = []
    lock = threading.Lock()

    def one(k: int) -> None:
        mm = Moss("inference_extra")
        r = mm.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": QUESTIONS[k], "stream": True},
                      test="concurrent5", headers=NO_CACHE)
        with lock:
            conc.append({"first_token_ms": r.first_token_ms, "status": r.status, "box": r.headers.get("x-moss-box")})

    time.sleep(3)
    threads = [threading.Thread(target=one, args=(k,)) for k in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    out["concurrent5"] = {"first_token_ms": sorted(round(c["first_token_ms"] or 0, 1) for c in conc),
                          "statuses": [c["status"] for c in conc], "boxes": [c["box"] for c in conc],
                          "note": "includes a new TCP+TLS connection per call"}
    out["finished"] = time.time()
    (ROOT / "results" / "inference_extra.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("interleaved",)}, indent=1)[:3000])
    print(json.dumps(out["interleaved"]["summary"], indent=1), json.dumps(out["interleaved"]["by_box"]))


if __name__ == "__main__":
    main()

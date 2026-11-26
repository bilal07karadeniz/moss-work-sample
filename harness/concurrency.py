"""First-token latency with N calls started at the same instant, each on its own pre-warmed connection.

N in 1, 3, 5, 8 (inside the documented 10-request burst), 3 rounds each, 12 s apart so the
120-requests-per-minute budget refills. Writes logs/concurrency.jsonl and results/concurrency.json.
"""

from __future__ import annotations

import json
import threading
import time

from client import ROOT, Moss, pct
from inference_tests import MODEL, NO_CACHE, QUESTIONS, SYSTEM


def round_of(n: int, rnd: int) -> list[dict]:
    clients = [Moss("concurrency") for _ in range(n)]
    for c in clients:  # warm each connection (TCP + TLS) before the timed burst
        c.request("GET", "/models", test="warm")
    time.sleep(2)
    barrier = threading.Barrier(n)
    out: list[dict] = []
    lock = threading.Lock()

    def go(k: int) -> None:
        barrier.wait()
        r = clients[k].stream("/responses", {"model": MODEL, "instructions": SYSTEM, "stream": True,
                                             "input": QUESTIONS[(k + rnd * 7) % 20]},
                              test=f"burst.n{n}", headers=NO_CACHE)
        with lock:
            out.append({"first_token_ms": r.first_token_ms, "status": r.status, "box": r.headers.get("x-moss-box"),
                        "remaining": r.headers.get("x-ratelimit-remaining-requests")})

    threads = [threading.Thread(target=go, args=(k,)) for k in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return out


def main() -> None:
    res: dict = {"started": time.time(), "levels": {}}
    for n in (1, 3, 5, 8):
        calls = []
        for rnd in range(3):
            calls += round_of(n, rnd)
            time.sleep(12)
        ok = [c["first_token_ms"] for c in calls if c["status"] == 200 and c["first_token_ms"]]
        res["levels"][n] = {"calls": len(calls), "ok": len(ok), "statuses": sorted({c["status"] for c in calls}),
                            "p50": round(pct(ok, 50), 1) if ok else None, "p95": round(pct(ok, 95), 1) if ok else None,
                            "max": round(max(ok), 1) if ok else None, "sorted": sorted(round(v) for v in ok),
                            "boxes": sorted({c["box"] for c in calls if c["box"]})}
    (ROOT / "results" / "concurrency.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res["levels"], indent=1))


if __name__ == "__main__":
    main()

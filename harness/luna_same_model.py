"""Same model, two gateways: `openai/gpt-6-luna` through Moss's catalog vs through OpenRouter.

The brisvox A/B compares Moss `fastlane-1` with OpenRouter `openai/gpt-6-luna`, so it mixes the gateway
with the model. Here the model is the same: Chat Completions streaming, `reasoning_effort: "none"` on both
(as in the brisvox agent file), interleaved Moss / OpenRouter, 10 calls each, one warm connection per
gateway. OpenRouter's key comes from OPENROUTER_API_KEY (never logged). Moss calls are logged by the
harness client; OpenRouter calls go to logs/luna_same_model_openrouter.jsonl without the auth header.
Writes results/luna_same_model.json.
"""

from __future__ import annotations

import http.client
import json
import os
import ssl
import time

from client import ROOT, Moss, pct
from inference_tests import NO_CACHE, PACE_S, QUESTIONS, SYSTEM


def openrouter_stream(conn: http.client.HTTPSConnection, body: dict) -> dict:
    hdrs = {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    conn.request("POST", "/api/v1/chat/completions", body=json.dumps(body).encode(), headers=hdrs)
    resp = conn.getresponse()
    first, provider, text = None, None, []
    if resp.status != 200:
        err = resp.read()[:500].decode("utf-8", "replace")
        return {"status": resp.status, "first_token_ms": None, "error": err}
    while True:
        line = resp.readline()
        if not line:
            break
        if not line.startswith(b"data: "):
            continue
        payload = line[6:].strip()
        if payload == b"[DONE]":
            resp.read()
            break
        try:
            ev = json.loads(payload)
        except ValueError:
            continue
        provider = ev.get("provider") or provider
        for ch in ev.get("choices") or []:
            d = (ch.get("delta") or {}).get("content")
            if d:
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                text.append(d)
    return {"status": 200, "first_token_ms": first, "total_ms": (time.perf_counter() - t0) * 1000,
            "provider": provider, "text": "".join(text)}


def main() -> None:
    m = Moss("luna_same_model")
    orc = http.client.HTTPSConnection("openrouter.ai", 443, timeout=60, context=ssl.create_default_context())
    log = (ROOT / "logs" / "luna_same_model_openrouter.jsonl").open("a", encoding="utf-8")
    rows = {"moss": [], "openrouter": []}
    for i in range(20):
        q = QUESTIONS[i // 2 % 20]
        body = {"model": "openai/gpt-6-luna", "stream": True, "reasoning_effort": "none", "max_tokens": 120,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]}
        if i % 2 == 0:
            r = m.stream("/chat/completions", body, test="luna.moss", headers=NO_CACHE, kind="chat")
            rows["moss"].append({"status": r.status, "first_token_ms": r.first_token_ms,
                                 "error": r.body if r.status != 200 else None})
            if r.status != 200 and i == 0:
                body.pop("reasoning_effort")  # record and retry once without it, if Moss rejects the field
                r = m.stream("/chat/completions", body, test="luna.moss.no_reasoning_field", headers=NO_CACHE, kind="chat")
                rows["moss"][-1]["retry_without_reasoning_effort"] = {"status": r.status, "first_token_ms": r.first_token_ms}
        else:
            r2 = openrouter_stream(orc, body)
            rows["openrouter"].append(r2)
            log.write(json.dumps({"ts": time.time(), "request": {"url": "https://openrouter.ai/api/v1/chat/completions",
                                                                  "body": body}, "response": r2}) + "\n")
        time.sleep(PACE_S)
    log.close()
    out = {"started": time.time(), "rows": rows}
    for k, v in rows.items():
        ok = [x["first_token_ms"] for x in v if x.get("status") == 200 and x.get("first_token_ms")]
        out[k] = {"n": len(ok), "p50": pct(ok, 50), "p90": pct(ok, 90), "min": min(ok) if ok else None,
                  "max": max(ok) if ok else None}
    (ROOT / "results" / "luna_same_model.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ("moss", "openrouter")}, indent=1))
    print("openrouter providers:", sorted({x.get("provider") for x in rows["openrouter"] if x.get("provider")}))
    print("moss errors:", [x["error"] for x in rows["moss"] if x.get("error")][:2])


if __name__ == "__main__":
    main()

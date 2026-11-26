"""Follow-up after the Portal review: the project does have an index (`product-search-context`, a 100-item
clothing catalog made in the Portal on Nov 16), and the Portal's model catalog lists `openai/gpt-6-luna`.

1. Grounding: three questions whose answers are only in the catalog, asked with and without
   `X-Moss-Indexes`; then 10 streamed calls with the index for first token and `X-Moss-Retrieve-Ms`,
   against 10 without it.
2. Catalog: does `openai/gpt-6-luna` run on Moss without `X-Moss-Provider-Key`? (The bare name
   `gpt-6-luna` asked for the key in the error probes.) Five streamed first tokens if it does.
Writes logs/grounding_and_catalog.jsonl and results/grounding_and_catalog.json.
"""

from __future__ import annotations

import json
import os
import time

from client import ROOT, Moss, pct
from inference_tests import MODEL, NO_CACHE, PACE_S

INDEX = os.environ.get("MOSS_INDEX", "").strip() or "product-search-context"
QUESTIONS = [  # (question, words the catalog answer must contain)
    ("What fill power is the down in your insulated parka?", ["700"]),
    ("Which of your jackets has a zip-out fleece liner?", ["three-in-one", "3-in-1"]),
    ("What UPF rating does your performance polo shirt have?", ["30"]),
]
SYSTEM = "You are a clothing store's assistant. Answer in one short sentence. If you don't know, say so."


def text_of(body) -> str:
    if not isinstance(body, dict):
        return ""
    return "".join(c.get("text", "") for o in body.get("output", []) if o.get("type") == "message"
                   for c in o.get("content", []) if c.get("type") == "output_text")


def main() -> None:
    m = Moss("grounding_and_catalog")
    out: dict = {"index": INDEX, "started": time.time(), "qa": []}
    for q, must in QUESTIONS:
        row = {"question": q}
        for label, hdr in (("without", NO_CACHE), ("with", {**NO_CACHE, "X-Moss-Indexes": INDEX})):
            r = m.request("POST", "/responses", {"model": MODEL, "instructions": SYSTEM, "input": q},
                          test=f"grounding.{label}_index", headers=hdr)
            t = text_of(r.body)
            row[label] = {"status": r.status, "text": t, "ms": round(r.total_ms, 1),
                          "retrieve_ms": r.headers.get("x-moss-retrieve-ms"),
                          "correct": any(w in t.lower() for w in must)}
            time.sleep(PACE_S)
        out["qa"].append(row)
    lat = {"with": [], "without": [], "retrieve_ms": []}
    for i in range(20):
        q = QUESTIONS[i % 3][0]
        use = i % 2 == 0
        hdr = {**NO_CACHE, "X-Moss-Indexes": INDEX} if use else NO_CACHE
        r = m.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": q, "stream": True},
                     test=f"grounding.stream.{'with' if use else 'without'}", headers=hdr)
        if r.status == 200 and r.first_token_ms:
            lat["with" if use else "without"].append(r.first_token_ms)
            if use and r.headers.get("x-moss-retrieve-ms"):
                lat["retrieve_ms"].append(float(r.headers["x-moss-retrieve-ms"]))
        time.sleep(PACE_S)
    out["latency"] = {k: {"n": len(v), "p50": pct(v, 50), "max": max(v) if v else None} for k, v in lat.items()}

    luna = []
    for i in range(6):
        r = m.stream("/responses", {"model": "openai/gpt-6-luna", "instructions": SYSTEM, "stream": True,
                                    "input": QUESTIONS[i % 3][0]}, test="catalog.openai_gpt-6-luna", headers=NO_CACHE)
        luna.append({"status": r.status, "first_token_ms": r.first_token_ms, "box": r.headers.get("x-moss-box"),
                     "error": r.body if r.status != 200 else None})
        if r.status != 200:
            break
        time.sleep(PACE_S)
    ok = [x["first_token_ms"] for x in luna if x["status"] == 200 and x["first_token_ms"]]
    out["catalog_luna"] = {"calls": luna, "first_token_p50": pct(ok, 50) if ok else None}
    (ROOT / "results" / "grounding_and_catalog.json").write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                                                encoding="utf-8")
    print(json.dumps(out, indent=1, ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()

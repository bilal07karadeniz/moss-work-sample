"""Checks F4's table (30 Hacker News titles with comment counts) against the official HN API.

F4 was built to hit its 60 s limit; it finished instead, so its numbers need checking: an agent that
runs out of time could fill a table from memory. Counts only grow, so a real count read during the
task is equal to or a little below the API's value read afterwards. Writes results/f4_check.json.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from client import ROOT


def item(i: str) -> dict:
    with urllib.request.urlopen(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15) as r:
        return json.load(r)


def main() -> None:
    tasks = json.loads((ROOT / "results" / "tasks.json").read_text(encoding="utf-8"))
    f4 = next(t for t in tasks["tasks"] if t["label"].startswith("F4"))
    text = ""  # tasks.json keeps 1,500 characters of each answer; the full answer is in the log
    for line in (ROOT / "logs" / "tasks.jsonl").open(encoding="utf-8"):
        body = (json.loads(line).get("response") or {}).get("body")
        if isinstance(body, dict) and body.get("id") == f4["id"] and body.get("result"):
            text = body["result"].get("output_text") or text
    rows = re.findall(r"item\?id=(\d+)\)\s*\|\s*([\d,]+)", text)
    with ThreadPoolExecutor(8) as ex:
        items = list(ex.map(item, [i for i, _ in rows]))
    out = []
    for (i, claimed), d in zip(rows, items):
        claimed_n, api_n = int(claimed.replace(",", "")), d.get("descendants")
        out.append({"id": i, "title": d.get("title"), "task_count": claimed_n, "api_count_after": api_n,
                    "ok": api_n is not None and 0 <= api_n - claimed_n <= max(5, 0.05 * api_n)})
    res = {"checked_at": time.time(), "rows_in_table": len(rows), "consistent": sum(r["ok"] for r in out),
           "exact": sum(r["task_count"] == r["api_count_after"] for r in out), "rows": out}
    (ROOT / "results" / "f4_check.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{res['rows_in_table']} rows, {res['consistent']} consistent with the HN API, {res['exact']} exact")
    for r in out:
        if not r["ok"]:
            print("  mismatch:", r["id"], r["task_count"], r["api_count_after"])


if __name__ == "__main__":
    main()

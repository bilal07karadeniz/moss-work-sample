"""F4b: a task that cannot finish in its 60 s limit, so the time-limit path really fires.

F4 (top-30 comment counts) finished in 56 s because the counts are on the front page. Here each answer
needs a separate page: the author of the first comment on each of the top 30 stories. Answers that do
come back are checked against the official HN API (first entry of `kids`). Cap: $0.20.
Writes logs/tasks_timeout.jsonl and results/tasks_timeout.json.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request

from client import ROOT, Moss
from tasks_tests import iso, task

GOAL = ("Go to https://news.ycombinator.com. For each of the top 30 stories, open its comments page and read the "
        "username of the author of the first comment shown. Return a table of story id, title and that username "
        "for all 30 stories.")
TERMINAL = {"completed", "failed", "cancelled"}


def hn(path: str) -> dict:
    with urllib.request.urlopen(f"https://hacker-news.firebaseio.com/v0/{path}.json", timeout=15) as r:
        return json.load(r)


def main() -> None:
    m = Moss("tasks_timeout")
    body = task(GOAL, cost=0.20, runtime=60, start_url="https://news.ycombinator.com", label="F4b")
    r = m.request("POST", "/tasks", body, test="create.F4b")
    tid = r.body["id"]
    t: dict = {}
    for _ in range(60):
        time.sleep(5)
        t = m.request("GET", f"/tasks/{tid}", test="poll.F4b").body
        if t.get("status") in TERMINAL:
            break
    text = ((t.get("result") or {}).get("output_text") or "")
    claimed = re.findall(r"\b(\d{7,9})\b[^\n]*?\|\s*`?([A-Za-z0-9_-]{2,15})`?\s*\|?\s*$", text, re.M)
    checks = []
    for sid, user in claimed[:30]:
        item = hn(f"item/{sid}")
        first = hn(f"item/{item['kids'][0]}").get("by") if item.get("kids") else None
        checks.append({"id": sid, "claimed": user, "api_first_comment_by": first, "match": first == user})
    start, end = iso(t.get("started_at")), iso(t.get("finished_at"))
    res = {"id": tid, "status": t.get("status"), "error": t.get("error"), "limits": t.get("limits"),
           "run_s": round(end - start, 1) if start and end else None, "usage": t.get("usage"),
           "verification": t.get("verification"), "output_text": text, "rows_claimed": len(claimed),
           "rows_matching_api": sum(c["match"] for c in checks), "checks": checks}
    (ROOT / "results" / "tasks_timeout.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k not in ("output_text", "checks")}, indent=1, ensure_ascii=False))
    print(text[:1500])


if __name__ == "__main__":
    main()

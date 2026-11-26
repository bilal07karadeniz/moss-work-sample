"""Tasks API checks: 3 tasks with known answers, 4 built to fail, validation, idempotency, cancel, files.

Every task sets limits.max_cost_usd; the caps below add up to $1.41 (model calls). Browser time
and traffic bill on top ($0.02/h, $5/GB per the pricing page), which keeps the run under ~$1.70.
Writes logs/tasks.jsonl and results/tasks.json.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import uuid

from client import ROOT, Moss

POLL_S = 5
WALL_LIMIT_S = 35 * 60
HEAVY_GOAL = ("Go to https://news.ycombinator.com. For each of the top 30 stories, open its comments page and "
              "count the comments. Return a table of title and comment count for all 30.")


def task(goal: str, *, cost: float, runtime: int = 300, model: str = "moss-fast", start_url: str | None = None,
         schema: dict | None = None, effort: str | None = None, label: str) -> dict:
    env = {"type": "browser"}
    if start_url:
        env["start_url"] = start_url
    body = {"goal": goal, "environment": env, "model": model,
            "limits": {"max_runtime_seconds": runtime, "max_cost_usd": cost}, "metadata": {"label": label}}
    if schema:
        body["output_schema"] = schema
    if effort:
        body["effort"] = effort
    return body


SPECS = [
    # --- known answers ---------------------------------------------------------------
    {"label": "K1 example.com heading", "kind": "known", "expect": "Example Domain",
     "body": task("Open https://example.com and return the exact text of the page's main heading (the h1).",
                  cost=0.20, start_url="https://example.com",
                  schema={"type": "object", "properties": {"heading": {"type": "string"}}, "required": ["heading"]},
                  label="K1")},
    {"label": "K2 value from the Moss docs", "kind": "known", "expect": 1.75,
     "body": task("On the Moss documentation pricing page, find the price of OUTPUT tokens for the model "
                  "moss/fastlane-1, in US dollars per 1M tokens. Return only that number.",
                  cost=0.20, start_url="https://docs.moss.dev/docs/inference/pricing",
                  schema={"type": "object", "properties": {"price_usd_per_million": {"type": "number"}},
                          "required": ["price_usd_per_million"]}, label="K2")},
    {"label": "K3 top 5 Hacker News stories", "kind": "known", "expect": "hn_top5",
     "body": task("Go to https://news.ycombinator.com and return the titles of the top 5 stories on the front page, "
                  "in order, exactly as shown.", cost=0.30, model="moss-balanced",
                  start_url="https://news.ycombinator.com",
                  schema={"type": "object", "properties": {"titles": {"type": "array", "items": {"type": "string"},
                                                                     "minItems": 5, "maxItems": 5}},
                          "required": ["titles"]}, label="K3")},
    # --- built to fail ---------------------------------------------------------------
    {"label": "F1 missing data", "kind": "fail", "expect": "no answer, or an answer that says it is not there",
     "body": task("On https://example.com, find the phone number of the site's customer support team and return it.",
                  cost=0.15, start_url="https://example.com",
                  schema={"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]},
                  label="F1")},
    {"label": "F2 dead URL", "kind": "fail", "expect": "fails or says the page could not be reached",
     "body": task("Read this shop's opening hours from the page and return them.", cost=0.15,
                  start_url="https://moss-work-sample-dead-host.invalid/hours", label="F2")},
    {"label": "F3 $0.01 budget", "kind": "fail", "expect": "budget_exceeded, cost close to $0.01",
     "body": task(HEAVY_GOAL, cost=0.01, label="F3")},
    {"label": "F4 60 s time limit", "kind": "fail", "expect": "time_limit after ~60 s",
     "body": task(HEAVY_GOAL, cost=0.20, runtime=60, label="F4")},
]


def hn_top5() -> list[str]:
    """Ground truth from the official Hacker News API, at the time of the call."""
    base = "https://hacker-news.firebaseio.com/v0"
    with urllib.request.urlopen(f"{base}/topstories.json", timeout=20) as r:
        ids = json.load(r)[:5]
    titles = []
    for i in ids:
        with urllib.request.urlopen(f"{base}/item/{i}.json", timeout=20) as r:
            titles.append(json.load(r).get("title", ""))
    return titles


def norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def grade(spec: dict, t: dict, truth: dict) -> dict:
    status, err = t.get("status"), (t.get("error") or {})
    result = t.get("result") or {}
    output = result.get("output") or {}
    text = result.get("output_text") or ""
    g = {"status": status, "error_code": err.get("code"), "verification_passed": (t.get("verification") or {}).get("passed")}
    if spec["expect"] == "Example Domain":
        g["correct"] = norm(output.get("heading", text)) == norm("Example Domain")
    elif spec["expect"] == 1.75:
        val = output.get("price_usd_per_million")
        g["correct"] = val == 1.75 or "1.75" in text
    elif spec["expect"] == "hn_top5":
        got = [norm(x) for x in (output.get("titles") or [])]
        best = max((sum(a == b for a, b in zip(got, [norm(x) for x in tr])) for tr in truth.get("hn", [])), default=0)
        overlap = max((len(set(got) & {norm(x) for x in tr}) for tr in truth.get("hn", [])), default=0)
        g.update(in_order_matches=best, set_overlap=overlap, correct=best == 5)
    elif spec["label"].startswith("F1"):
        phone = str(output.get("phone", "")).strip()
        g["hallucinated"] = status == "completed" and any(ch.isdigit() for ch in phone)
        g["behaved"] = not g["hallucinated"]
    elif spec["label"].startswith("F2"):
        g["behaved"] = status == "failed" or any(w in text.lower() for w in ("not", "could", "unable", "error", "resolve"))
    elif spec["label"].startswith("F3"):
        g["behaved"] = err.get("code") == "budget_exceeded"
    elif spec["label"].startswith("F4"):
        g["behaved"] = err.get("code") == "time_limit"
    return g


def iso(ts: str | None) -> float | None:
    if not ts:
        return None
    from datetime import datetime
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def wait_all(m: Moss, ids: dict[str, str]) -> dict[str, dict]:
    done: dict[str, dict] = {}
    first_running: dict[str, float] = {}
    t0 = time.time()
    while len(done) < len(ids) and time.time() - t0 < WALL_LIMIT_S:
        for label, tid in ids.items():
            if label in done:
                continue
            r = m.request("GET", f"/tasks/{tid}", test=f"poll.{label}")
            body = r.body if isinstance(r.body, dict) else {}
            if body.get("status") == "running" and label not in first_running:
                first_running[label] = time.time()
            if body.get("status") in ("completed", "failed", "cancelled"):
                body["_seen_running_at"] = first_running.get(label)
                done[label] = body
            time.sleep(0.7)  # keep polling well under the 10-request burst limit
        time.sleep(POLL_S)
    return done


def validation(m: Moss) -> list[dict]:
    ok_env = {"type": "browser"}
    lim = {"max_runtime_seconds": 60, "max_cost_usd": 0.01}
    probes = [
        ("missing goal", {"environment": ok_env, "limits": lim}, 400),
        ("environment.type desktop", {"goal": "x", "environment": {"type": "desktop"}, "limits": lim}, 400),
        ("start_url ftp", {"goal": "x", "environment": {"type": "browser", "start_url": "ftp://example.com"},
                           "limits": lim}, 400),
        ("max_cost_usd 0.001", {"goal": "x", "environment": ok_env,
                                "limits": {"max_runtime_seconds": 60, "max_cost_usd": 0.001}}, 400),
        ("max_cost_usd 100", {"goal": "x", "environment": ok_env,
                              "limits": {"max_runtime_seconds": 60, "max_cost_usd": 100}}, 400),
        ("max_runtime_seconds 30", {"goal": "x", "environment": ok_env,
                                    "limits": {"max_runtime_seconds": 30, "max_cost_usd": 0.01}}, 400),
        ("max_runtime_seconds 9000", {"goal": "x", "environment": ok_env,
                                      "limits": {"max_runtime_seconds": 9000, "max_cost_usd": 0.01}}, 400),
        ("unknown top-level field", {"goal": "x", "environment": ok_env, "limits": lim, "priority": "high"}, 400),
        ("output_schema type array", {"goal": "x", "environment": ok_env, "limits": lim,
                                      "output_schema": {"type": "array"}}, 400),
        ("unknown model", {"goal": "x", "environment": ok_env, "limits": lim, "model": "moss-ultra"}, 400),
        ("effort extreme", {"goal": "x", "environment": ok_env, "limits": lim, "effort": "extreme"}, 400),
        ("goal 20,001 chars", {"goal": "a" * 20001, "environment": ok_env, "limits": lim}, 400),
        ("metadata 5 KB", {"goal": "x", "environment": ok_env, "limits": lim, "metadata": {"k": "v" * 5000}}, 400),
        ("limits as string", {"goal": "x", "environment": ok_env, "limits": "cheap"}, 400),
        ("empty goal", {"goal": "", "environment": ok_env, "limits": lim}, 400),
    ]
    out = []
    for name, body, want in probes:
        r = m.request("POST", "/tasks", body, test=f"validate.{name}")
        err = (r.body or {}).get("error") if isinstance(r.body, dict) else None
        row = {"probe": name, "want_status": want, "got_status": r.status,
               "code": (err or {}).get("code"), "type": (err or {}).get("type"),
               "message": (err or {}).get("message"), "ok": r.status == want}
        if r.status == 202 and isinstance(r.body, dict) and r.body.get("id"):  # accepted by mistake: stop it
            c = m.request("POST", f"/tasks/{r.body['id']}/cancel", test=f"validate.{name}.cancel")
            row["accepted_task_cancelled"] = (c.body or {}).get("status") if isinstance(c.body, dict) else c.status
        out.append(row)
        time.sleep(0.6)
    for name, path, method in (("get unknown task", "/tasks/task_" + "0" * 32, "GET"),
                               ("cancel unknown task", "/tasks/task_" + "0" * 32 + "/cancel", "POST"),
                               ("get malformed id", "/tasks/not-a-task-id", "GET")):
        r = m.request(method, path, test=f"validate.{name}")
        err = (r.body or {}).get("error") if isinstance(r.body, dict) else None
        out.append({"probe": name, "want_status": 404, "got_status": r.status, "code": (err or {}).get("code"),
                    "type": (err or {}).get("type"), "message": (err or {}).get("message"), "ok": r.status == 404})
    return out


def idempotency(m: Moss) -> tuple[dict, str | None]:
    key = f"moss-sample-{uuid.uuid4()}"
    body = task("Open https://example.com and return the page title.", cost=0.05, runtime=120,
                start_url="https://example.com", label="IDEM")
    a = m.request("POST", "/tasks", body, test="idem.first", headers={"Idempotency-Key": key})
    b = m.request("POST", "/tasks", body, test="idem.replay", headers={"Idempotency-Key": key})
    changed = dict(body, goal="Open https://example.com and return the first paragraph.")
    c = m.request("POST", "/tasks", changed, test="idem.conflict", headers={"Idempotency-Key": key})
    ida = (a.body or {}).get("id") if isinstance(a.body, dict) else None
    idb = (b.body or {}).get("id") if isinstance(b.body, dict) else None
    out = {"first_status": a.status, "replay_status": b.status, "same_id": ida is not None and ida == idb,
           "replay_header": b.headers.get("idempotent-replayed"), "conflict_status": c.status,
           "conflict_code": ((c.body or {}).get("error") or {}).get("code") if isinstance(c.body, dict) else None}
    if c.status == 202 and isinstance(c.body, dict) and c.body.get("id") not in (None, ida):
        m.request("POST", f"/tasks/{c.body['id']}/cancel", test="idem.conflict.cancel")
        out["conflict_created_a_task"] = True
    return out, ida


def cancel_checks(m: Moss) -> tuple[dict, list[str]]:
    out: dict = {}
    # Cancel straight after create (queued or barely started).
    q = m.request("POST", "/tasks", task("Open https://example.com and return its heading.", cost=0.05, runtime=120,
                                         label="CANCEL-QUEUED"), test="cancel.queued.create")
    qid = (q.body or {}).get("id")
    cq = m.request("POST", f"/tasks/{qid}/cancel", test="cancel.queued.cancel")
    out["queued"] = {"status_at_create": (q.body or {}).get("status"), "cancel_http": cq.status,
                     "status_after_cancel": (cq.body or {}).get("status"),
                     "cancel_requested": (cq.body or {}).get("cancel_requested")}
    # Cancel while running.
    r = m.request("POST", "/tasks", task(HEAVY_GOAL, cost=0.10, runtime=600, label="CANCEL-RUNNING"),
                  test="cancel.running.create")
    rid = (r.body or {}).get("id")
    t0 = time.time()
    status = (r.body or {}).get("status")
    files_before = None
    while status != "running" and time.time() - t0 < 300:
        time.sleep(3)
        status = (m.request("GET", f"/tasks/{rid}", test="cancel.running.wait").body or {}).get("status")
    if status == "running":
        fb = m.request("GET", f"/tasks/{rid}/files", test="files.before_finish")
        files_before = {"status": fb.status, "code": ((fb.body or {}).get("error") or {}).get("code")
                        if isinstance(fb.body, dict) else None}
        time.sleep(15)  # let it do some work so the cancel interrupts real activity
    sent = time.time()
    cr = m.request("POST", f"/tasks/{rid}/cancel", test="cancel.running.cancel")
    out["running"] = {"status_when_cancelled": status, "cancel_http": cr.status,
                      "returned_status": (cr.body or {}).get("status"),
                      "cancel_requested": (cr.body or {}).get("cancel_requested"), "cancel_sent_at": sent}
    # Cancel again (idempotent?) and cancel of a finished task are checked after the wait.
    out["files_before_finish"] = files_before
    return out, [i for i in (qid, rid) if i]


def main() -> None:
    m = Moss("tasks")
    report: dict = {"started": time.time(), "caps_usd": sum(s["body"]["limits"]["max_cost_usd"] for s in SPECS) + 0.20}
    report["validation"] = validation(m)
    truth = {"hn": [hn_top5()]}
    ids: dict[str, str] = {}
    created: dict[str, dict] = {}
    for spec in SPECS:
        r = m.request("POST", "/tasks", spec["body"], test=f"create.{spec['label']}")
        created[spec["label"]] = {"http": r.status, "body_status": (r.body or {}).get("status") if isinstance(r.body, dict) else None,
                                  "ms": round(r.total_ms, 1)}
        if r.status == 202:
            ids[spec["label"]] = r.body["id"]
        time.sleep(0.5)
    report["created"] = created
    idem, idem_id = idempotency(m)
    report["idempotency"] = idem
    cancel, cancel_ids = cancel_checks(m)
    report["cancel"] = cancel
    if idem_id:
        ids["IDEM"] = idem_id
    for n, cid in enumerate(cancel_ids):
        ids[f"CANCEL-{n}"] = cid
    finished = wait_all(m, ids)
    truth["hn"].append(hn_top5())  # the front page can move while a task runs: accept either snapshot
    report["hn_truth"] = truth["hn"]
    rows = []
    total = 0.0
    for label, t in finished.items():
        spec = next((s for s in SPECS if s["label"] == label), None)
        cost = ((t.get("usage") or {}).get("cost") or {})
        total += cost.get("total_usd") or 0.0
        start, end, made = iso(t.get("started_at")), iso(t.get("finished_at")), iso(t.get("created_at"))
        row = {"label": label, "id": t.get("id"), "model": t.get("model"), "status": t.get("status"),
               "error": t.get("error"), "cap_usd": (t.get("limits") or {}).get("max_cost_usd"),
               "runtime_cap_s": (t.get("limits") or {}).get("max_runtime_seconds"), "cost": cost,
               "usage": {k: (t.get("usage") or {}).get(k) for k in ("model_calls", "steps", "input_tokens",
                                                                       "output_tokens", "browser_seconds",
                                                                       "browser_traffic_mb")},
               "queue_s": round(start - made, 1) if start and made else None,
               "run_s": round(end - start, 1) if start and end else None,
               "output_text": (t.get("result") or {}).get("output_text") or "",
               "output": (t.get("result") or {}).get("output"), "verification": t.get("verification")}
        if spec:
            row["kind"], row["expect"] = spec["kind"], spec["expect"]
            row["grade"] = grade(spec, t, truth)
        rows.append(row)
    report["tasks"] = rows
    report["total_cost_usd"] = round(total, 4)
    # After the end: cancel of a finished task, files of a finished task.
    if "K1 example.com heading" in finished:
        kid = finished["K1 example.com heading"]["id"]
        cf = m.request("POST", f"/tasks/{kid}/cancel", test="cancel.finished")
        files = m.request("GET", f"/tasks/{kid}/files", test="files.list")
        report["cancel_finished"] = {"http": cf.status, "status": (cf.body or {}).get("status")}
        flist = (files.body or {}).get("data", []) if isinstance(files.body, dict) else []
        report["files"] = {"http": files.status, "count": len(flist), "items": flist[:20]}
        if flist:
            path = urllib.parse.quote(flist[0]["path"], safe="/")
            f = m.request("GET", f"/tasks/{kid}/files/{path}", test="files.download")
            report["files"]["first_download_http"] = f.status
    if cancel_ids:
        rid = cancel_ids[-1]
        t = finished.get(f"CANCEL-{len(cancel_ids) - 1}", {})
        end = iso(t.get("finished_at"))
        sent = report["cancel"]["running"]["cancel_sent_at"]
        report["cancel"]["running"]["final_status"] = t.get("status")
        report["cancel"]["running"]["seconds_to_cancelled"] = round(end - sent, 1) if end else None
        report["cancel"]["running"]["billed"] = ((t.get("usage") or {}).get("cost") or {})
        again = m.request("POST", f"/tasks/{rid}/cancel", test="cancel.again")
        report["cancel"]["running"]["cancel_again_http"] = again.status
    report["unfinished"] = [k for k in ids if k not in finished]
    report["finished_at"] = time.time()
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "tasks.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"total_cost_usd": report["total_cost_usd"], "unfinished": report["unfinished"],
                      "tasks": [(r["label"], r["status"], (r.get("grade") or {})) for r in rows]}, indent=2))


if __name__ == "__main__":
    main()

"""Builds results/RESULTS.md from results/inference.json and results/tasks.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from client import ROOT


def load(name: str) -> dict:
    p = ROOT / "results" / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def fmt(v, nd=0) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def inference_md(r: dict) -> list[str]:
    if not r:
        return ["Inference: not run."]
    when = datetime.fromtimestamp(r["started"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"## Inference API ({when}, from a desktop in Istanbul over the public internet)", "",
           f"`GET /models`: HTTP {r.get('models_status')}, {len(r.get('models', []))} models.", "",
           "### Latency on `moss/fastlane-1` (reply cache off, one short receptionist question per call)", "",
           "| Call | Metric | n | p50 ms | p95 ms | min | max | errors |", "|---|---|---|---|---|---|---|---|"]
    for s in r.get("latency", []):
        out.append(f"| {s['name']} | {s['metric'].replace('_ms', '')} | {s['n']} | {fmt(s.get('p50'))} | "
                   f"{fmt(s.get('p95'))} | {fmt(s.get('min'))} | {fmt(s.get('max'))} | {s['errors']} |")
    c = r.get("reply_cache", {})
    out += ["", "### Reply cache", "",
            f"Same request twice with the cache on: {fmt(c.get('first_ms'))} ms, then {fmt(c.get('repeat_ms'))} ms; "
            f"usage first `{json.dumps(c.get('first_usage'))}`, repeat `{json.dumps(c.get('repeat_usage'))}`; "
            f"same text: {c.get('same_text')}.", ""]
    g = r.get("grounding", {})
    out += ["### Grounding (`X-Moss-Indexes`)", ""]
    if g.get("index"):
        out += [f"Index `{g['index']}`: `X-Moss-Retrieve-Ms` {g.get('retrieve_ms')}; total {g.get('grounded_total_ms')} ms "
                f"with the index vs {g.get('plain_total_ms')} ms without; streamed first token "
                f"{fmt(g.get('grounded_stream_first_token_ms'))} ms.", "",
                f"- without index: {g.get('plain_text')!r}", f"- with index: {g.get('grounded_text')!r}", ""]
    else:
        out += ["No index in the project (`MOSS_INDEX` empty), so only the error path was tested.", ""]
    out += [f"Unknown index name: HTTP {g.get('unknown_index_status')}, "
            f"`{json.dumps((g.get('unknown_index_error') or {}).get('error') if isinstance(g.get('unknown_index_error'), dict) else g.get('unknown_index_error'))}`", ""]
    t = r.get("tools", {})
    out += ["### Tool call round trip (history resent, no server state)", "",
            f"- Responses: call leg HTTP {t.get('responses_call_status')} in {fmt(t.get('responses_call_ms'))} ms, "
            f"calls `{json.dumps(t.get('responses_function_calls'))}`; result leg {fmt(t.get('responses_answer_ms'))} ms "
            f"(streamed first token {fmt(t.get('responses_answer_stream_first_token_ms'))} ms): "
            f"{t.get('responses_answer')!r}; uses the tool result: {t.get('answer_mentions_time')}",
            f"- Result leg without `tools` resent: HTTP {t.get('no_tools_resent_status')}: "
            f"{json.dumps(t.get('no_tools_resent_body'))[:300]}",
            f"- Chat Completions: calls `{json.dumps(t.get('chat_tool_calls'))}`; answer "
            f"{t.get('chat_answer')!r} in {fmt(t.get('chat_answer_ms'))} ms", "",
            "### State and error probes", "",
            "| Probe | want | got | code | `x-should-retry` | matches docs |", "|---|---|---|---|---|---|"]
    for p in r.get("probes", []):
        out.append(f"| {p['probe']} | {p['want_status']} {p.get('want_code') or ''} | {p['got_status']} | "
                   f"{p.get('got_code') or ''} | {p.get('x_should_retry') or ''} | {'yes' if p['ok'] else '**no**'} |")
    msgs = [p for p in r.get("probes", []) if p.get("message")]
    if msgs:
        out += ["", "Error messages returned:", ""] + [f"- {p['probe']}: {p['message']}" for p in msgs]
    return out


def tasks_md(r: dict) -> list[str]:
    if not r:
        return ["Tasks: not run."]
    when = datetime.fromtimestamp(r["started"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"## Tasks API ({when})", "",
           f"Caps set: ${r.get('caps_usd', 0):.2f} of `max_cost_usd` in total. Billed (sum of `usage.cost.total_usd`): "
           f"**${r.get('total_cost_usd', 0):.4f}**.", "",
           "| Task | model | status / error | graded | queue s | run s | cap $ | model $ | browser $ | traffic $ | total $ | steps |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for t in r.get("tasks", []):
        g = t.get("grade") or {}
        verdict = ("correct" if g.get("correct") else "wrong") if "correct" in g else \
            ("as designed" if g.get("behaved") else "**not as designed**") if "behaved" in g else "-"
        if "in_order_matches" in g:
            verdict += f" ({g['in_order_matches']}/5 in order, {g['set_overlap']}/5 overlap)"
        c = t.get("cost") or {}
        err = (t.get("error") or {}).get("code")
        out.append(f"| {t['label']} | {t.get('model')} | {t.get('status')}{' / ' + err if err else ''} | {verdict} | "
                   f"{fmt(t.get('queue_s'), 1)} | {fmt(t.get('run_s'), 1)} | {fmt(t.get('cap_usd'), 2)} | "
                   f"{fmt(c.get('model_usd'), 4)} | {fmt(c.get('browser_usd'), 4)} | {fmt(c.get('traffic_usd'), 4)} | "
                   f"{fmt(c.get('total_usd'), 4)} | {fmt((t.get('usage') or {}).get('steps'))} |")
    out += ["", "Answers:", ""]
    for t in r.get("tasks", []):
        ans = json.dumps(t.get("output")) if t.get("output") else (t.get("output_text") or "")
        err = t.get("error") or {}
        out.append(f"- **{t['label']}**: {ans[:400]!s}" + (f" — error `{err.get('code')}`: {err.get('message')}" if err else ""))
    if r.get("hn_truth"):
        out += ["", f"Hacker News API top 5 at task creation: {r['hn_truth'][0]}",
                f"and after the tasks ended: {r['hn_truth'][-1]}"]
    i = r.get("idempotency", {})
    c = r.get("cancel", {})
    out += ["", "### Idempotency, cancel, files", "",
            f"- `Idempotency-Key` first create HTTP {i.get('first_status')}; replay HTTP {i.get('replay_status')}, same id "
            f"{i.get('same_id')}, `Idempotent-Replayed: {i.get('replay_header')}`; same key + other body HTTP "
            f"{i.get('conflict_status')} `{i.get('conflict_code')}`.",
            f"- Cancel right after create: {json.dumps(c.get('queued'))}",
            f"- Cancel while running: {json.dumps({k: v for k, v in (c.get('running') or {}).items() if k != 'cancel_sent_at'})}",
            f"- Files before the task ended: {json.dumps(c.get('files_before_finish'))}",
            f"- Cancel of a finished task: {json.dumps(r.get('cancel_finished'))}",
            f"- Files of a finished task: {json.dumps(r.get('files'))[:400]}",
            f"- Unfinished at the end of the wait: {r.get('unfinished')}", "",
            "### Validation", "", "| Probe | want | got | code / type | matches docs |", "|---|---|---|---|---|"]
    for p in r.get("validation", []):
        out.append(f"| {p['probe']} | {p['want_status']} | {p['got_status']} | {p.get('code') or p.get('type') or ''} | "
                   f"{'yes' if p['ok'] else '**no**'}{' (task cancelled: ' + str(p['accepted_task_cancelled']) + ')' if p.get('accepted_task_cancelled') else ''} |")
    return out


def main() -> None:
    lines = ["# Results", "", "Generated by `harness/report.py` from `results/*.json`; every request is in `logs/*.jsonl`.", ""]
    lines += inference_md(load("inference.json")) + [""] + tasks_md(load("tasks.json"))
    (ROOT / "results" / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote results/RESULTS.md")


if __name__ == "__main__":
    main()

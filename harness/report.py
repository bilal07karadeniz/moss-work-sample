"""Builds results/RESULTS.md from results/*.json and brisvox/results/ab.json."""

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
        out += ["`MOSS_INDEX` was empty in this run, so only the error path was tested here. The project does have an "
                "index (I missed it until the Portal review); grounding is measured in the follow-up section below.", ""]
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
    if any(p["probe"] == "33 indexes" for p in r.get("probes", [])):
        out += ["", "The `33 indexes` row is my probe's fault, not Moss's: the 33 names were made up, so they are unknown "
                "and the 403 for an index outside the project is correct. The 32-name cap was not tested."]
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
        out.append(f"- **{t['label']}**: {ans[:400]!s}" + (f". Error `{err.get('code')}`: {err.get('message')}" if err else "")
                   + (f" (regraded: {(t.get('grade') or {}).get('regraded')})" if (t.get('grade') or {}).get('regraded') else ""))
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


def extra_md(e: dict, c: dict) -> list[str]:
    if not e:
        return []
    n = e["network"]
    s = e["interleaved"]["summary"]
    out = ["## Inference API, follow-up runs", "",
           "### Where the time goes (warm connection)", "",
           f"- TCP connect {n['tcp_connect_ms_p50']} ms, TLS handshake {n['tls_handshake_ms_p50']} ms (p50 of 10) to "
           f"`{n['peer']}`; a warm `GET /models` (no model involved) takes {n['get_models_warm_ms_p50']} ms.",
           f"- So of the ~191 ms streamed first token, about {n['get_models_warm_ms_p50']} ms is the path and gateway "
           f"and ~55 ms is the model.", "",
           "### Responses vs Chat Completions streaming, interleaved A B A B (30 calls)", "",
           "| API | ttfb p50/p95 | first token p50/p95 | total p50/p95 | deltas per reply | gap between deltas |",
           "|---|---|---|---|---|---|"]
    for api in ("responses", "chat"):
        x = s[api]
        out.append(f"| {api} | {x['ttfb_ms']['p50']} / {x['ttfb_ms']['p95']} | {x['first_token_ms']['p50']} / "
                   f"{x['first_token_ms']['p95']} | {x['total_ms']['p50']} / {x['total_ms']['p95']} | "
                   f"{x['deltas_per_reply_p50']} | {x['median_gap_ms_p50']} ms |")
    out += ["", f"Every call landed on `x-moss-box` {', '.join(e['interleaved']['by_box'])}. Failures in this run: "
            f"{len(e.get('failures') or [])}.", "",
            "### Prefix cache on a ~3.2k-token system prompt", "",
            "| call | prompt tokens | cached tokens | first token ms |", "|---|---|---|---|"]
    for r in e.get("prefix_cache", []):
        out.append(f"| {r['i']} | {r['prompt_tokens']} | {r['cached_tokens']} | {fmt(r['first_token_ms'])} |")
    if c:
        out += ["", "### Calls started at the same instant (each on its own pre-warmed connection, 3 rounds)", "",
                "| calls at once | n | p50 ms | p95 ms | max ms | all first tokens (ms) |", "|---|---|---|---|---|---|"]
        for k, v in c["levels"].items():
            out.append(f"| {k} | {v['ok']}/{v['calls']} | {fmt(v['p50'])} | {fmt(v['p95'])} | {fmt(v['max'])} | "
                       f"{', '.join(map(str, v['sorted']))} |")
        out += ["", "The distribution is two groups: most calls 250-320 ms, and in every one of the 9 bursts of 3 or more calls, 1-3 calls landed 200-250 ms behind the rest (12 of 48 calls; per-burst breakdown from `logs/concurrency.jsonl`)."]
    return out


def tasks_extra_md(f4: dict, f4b: dict) -> list[str]:
    out: list[str] = []
    if f4:
        out += ["", "### F4 answer checked against the Hacker News API", "",
                f"F4 returned {f4['rows_in_table']} rows; {f4['consistent']} agree with the API's comment count read "
                f"afterwards ({f4['exact']} exact, the rest 1-4 lower because comments kept arriving). "
                "`harness/f4_check.py`, `results/f4_check.json`."]
    if f4b:
        u = f4b.get("usage") or {}
        out += ["", "### F4b: a task that cannot finish in 60 s", "",
                f"Goal: the author of the first comment on each of the top 30 stories (30 page loads). Status "
                f"`{f4b['status']}`, error `{json.dumps(f4b['error'])}`, ran {f4b['run_s']} s, {u.get('steps')} steps, "
                f"billed ${(u.get('cost') or {}).get('total_usd', 0):.4f}. `result` is `null`: nothing gathered in those "
                f"steps comes back. `harness/tasks_timeout.py`, `results/tasks_timeout.json`."]
    return out


def ab_md(ab: dict) -> list[str]:
    if not ab:
        return []
    out = ["## Moss inside brisvox: voice-to-voice A/B", "",
           "Same brisvox 0.1.4 release build, same agent file, same fixture and client; only the `[llm]` block changes "
           "(`brisvox/agent_moss.toml` vs `brisvox/agent_openrouter.toml`). Deepgram nova-3 STT, Deepgram Aura-2 TTS, "
           "Smart Turn v3.2. Blocks interleaved (moss, openrouter, moss, openrouter, then moss-nocache, openrouter, moss-nocache); "
           "10 calls / 20 turns each, one call at a time, over the public internet from Istanbul. V2V is measured at the client (end of user speech to first bot "
           "audio); stage times come from the server's turn timeline.", "",
           "| LLM | answered | V2V p50 | V2V p90 | V2V max | LLM first token p50 | LLM first sentence p50 | TTS first byte p50 | end of turn p50 |",
           "|---|---|---|---|---|---|---|---|---|"]
    names = {"moss": "Moss `moss/fastlane-1`, reply cache on (default)",
             "moss: reply-cache hits": "... of which reply-cache hits",
             "moss: cache misses": "... of which cache misses",
             "moss-nocache": "Moss `moss/fastlane-1`, `X-Moss-Cache: off`",
             "openrouter": "OpenRouter `openai/gpt-6-luna`"}
    for k, r in ab.items():
        if k == "proxy":
            continue
        st = r["server_stages_ms"]
        out.append(f"| {names.get(k, k)} | {r['answered']}/{r['turns']} | {fmt(r['v2v_ms']['p50'])} | {fmt(r['v2v_ms']['p90'])} | "
                   f"{fmt(r['v2v_ms']['max'])} | {fmt(st['llm_ttft']['p50'])} | {fmt(st['llm_first_sentence']['p50'])} | "
                   f"{fmt(st['tts_ttfb']['p50'])} | {fmt(st['end_of_turn_delay']['p50'])} |")
    out += ["", "The fixture asks the same two questions on every call, so with the reply cache on (Moss's default) 30 of "
            "40 turns were answered from the cache: the whole reply arrives in one chunk ~150 ms after the request. "
            "The `X-Moss-Cache: off` arm goes through `brisvox/nocache_proxy.py` on loopback, because brisvox's agent "
            "file cannot set request headers yet; the proxy keeps warm connections to Moss and adds ~3-4 ms."]
    px = ab.get("proxy")
    if px:
        out += ["", f"Proxy: {px['requests']} requests, statuses {px['statuses']}, {px['reconnects']} reconnects, upstream "
                f"first bytes p50 {fmt(px['upstream_first_bytes_ms']['p50'])} ms."]
    out += ["", "Raw: `brisvox/results/v2v-*.{md,json}`, `brisvox/results/server-*.log`, `brisvox/results/proxy-*.jsonl`, "
            "summary `brisvox/results/ab.json`."]
    return out


def followups_md(g: dict, luna: dict) -> list[str]:
    out: list[str] = []
    if g:
        out += ["## Grounding with the project's index (`harness/grounding_and_catalog.py`)", "",
                f"Index `{g['index']}` (100-item clothing catalog, created in the Portal on Nov 16). Same question with and "
                "without `X-Moss-Indexes`:", "", "| question | without index | with index | retrieve ms |", "|---|---|---|---|"]
        for r in g["qa"]:
            out.append(f"| {r['question']} | {r['without']['text']} | {r['with']['text']} | {r['with']['retrieve_ms']} |")
        lt = g["latency"]
        out += ["", f"Streamed first token, 10 calls each, interleaved: with the index p50 {fmt(lt['with']['p50'])} ms, "
                f"without {fmt(lt['without']['p50'])} ms; `X-Moss-Retrieve-Ms` p50 {fmt(lt['retrieve_ms']['p50'], 1)} ms "
                f"(max {fmt(lt['retrieve_ms']['max'], 1)}). The very first grounded call reported "
                f"{g['qa'][0]['with']['retrieve_ms']} ms of retrieval (cold index).", ""]
        c = g.get("catalog_luna") or {}
        if c:
            out += [f"`openai/gpt-6-luna` from the Moss catalog without `X-Moss-Provider-Key`: HTTP "
                    f"{sorted({x['status'] for x in c['calls']})}, Responses first token p50 {fmt(c.get('first_token_p50'))} ms "
                    f"(no `reasoning_effort` sent).", ""]
    if luna:
        out += ["## Same model, two gateways (`harness/luna_same_model.py`)", "",
                "`openai/gpt-6-luna`, Chat Completions streaming, `reasoning_effort: none`, interleaved, 10 calls each:", "",
                "| gateway | n | first token p50 | p90 | min | max |", "|---|---|---|---|---|---|"]
        for k in ("moss", "openrouter"):
            x = luna[k]
            out.append(f"| {k} | {x['n']} | {fmt(x['p50'])} | {fmt(x['p90'])} | {fmt(x['min'])} | {fmt(x['max'])} |")
        out += ["", "So in the brisvox A/B most of the gain is `fastlane-1` itself; with the model held equal, Moss's path is "
                "~115 ms faster at the median than OpenRouter's.", ""]
    out += ["## Portal cross-check (read on 2026-11-26)", "",
            "- Usage: inference `moss/fastlane-1` 73.8K in / 48K cached / 7.9K out = $0.03; browser tasks 11 = $0.14 "
            "(model tokens $0.13, cloud browser time 6 min = $0.00); cycle total $0.18 including a $0.01 ingest from Nov 16.",
            "- Logs & Traces / Analytics (24 h): 507 requests, server-side first token p50 49 ms, p95 128 ms, errors 0%, "
            "success rate 100%, reply cache 6.5%. My client measured 191 ms first token on a warm connection (RTT 43 ms).",
            "- None of the rejected requests (400/401/403, each with an `X-Moss-Request-Id`) nor the two 502s appear: the "
            "Error filter is empty, and looking up `ccec899d7b4c4f9287f72471083123ca` (the 400 for `previous_response_id`) "
            "returns \"No request with that ID in the last 30 days\", while successful ids are found.",
            "- Tasks list: the dead-host task (F2) and the missing-phone task (F1) show as Succeeded; success rate 78% (7 of 9).",
            "- K1's recording and screenshots (`results/k1_screenshots/`) show today's example.com, which has no `<h1>` "
            "(`results/example_com_2026-11-26.html`): K1's empty heading is the right answer, and the grade was corrected."]
    return out


def main() -> None:
    lines = ["# Results", "", "Generated by `harness/report.py` from `results/*.json`; every request is in `logs/*.jsonl`.", ""]
    lines += inference_md(load("inference.json")) + [""] + extra_md(load("inference_extra.json"), load("concurrency.json"))
    lines += [""] + tasks_md(load("tasks.json")) + tasks_extra_md(load("f4_check.json"), load("tasks_timeout.json"))
    ab = ROOT / "brisvox" / "results" / "ab.json"
    lines += [""] + ab_md(json.loads(ab.read_text(encoding="utf-8")) if ab.exists() else {})
    lines += [""] + followups_md(load("grounding_and_catalog.json"), load("luna_same_model.json"))
    (ROOT / "results" / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote results/RESULTS.md")


if __name__ == "__main__":
    main()

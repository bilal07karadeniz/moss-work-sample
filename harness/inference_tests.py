"""Inference API checks: latency, streaming, grounding, tool calls, state, error probes.

Writes logs/inference.jsonl (every request) and results/inference.json (summary).
"""

from __future__ import annotations

import json
import os
import statistics
import time

from client import ROOT, Moss, pct

MODEL = "moss/fastlane-1"
SYSTEM = ("You are a phone receptionist for a dental clinic. Answer in one short spoken sentence. "
          "Opening hours are 9 to 17, Monday to Friday.")
QUESTIONS = [
    "Are you open on Saturday?", "What time do you close on Friday?", "Can I book a cleaning next week?",
    "Do you take walk-ins?", "How early do you open?", "Is the clinic open on Sunday?",
    "Can I come at 8 in the morning?", "Are you open on Wednesday afternoon?", "What are your hours?",
    "Can I reschedule my appointment?", "Do you open at nine?", "Is 4 pm on Thursday possible?",
    "Do you work weekends?", "When can I call you?", "Are you open at lunchtime?",
    "Can I come after work at 6?", "Is Monday morning free?", "Do you close early on Fridays?",
    "Are you open today?", "What is the last appointment time?",
]
NO_CACHE = {"X-Moss-Cache": "off"}
PACE_S = 0.6  # stay well under the 10-request burst / 120 rpm limit


def summarize(name: str, results, key: str) -> dict:
    vals = [getattr(r, key) for r in results if r.status == 200 and getattr(r, key) is not None]
    out = {"name": name, "metric": key, "n": len(vals), "errors": sum(1 for r in results if r.status != 200)}
    if vals:
        out.update(p50=round(pct(vals, 50), 1), p95=round(pct(vals, 95), 1), min=round(min(vals), 1),
                   max=round(max(vals), 1), mean=round(statistics.mean(vals), 1))
    return out


def latency(m: Moss, runs: int) -> list[dict]:
    summary = []
    # Responses, streaming, warm connection
    res = []
    for i in range(runs):
        res.append(m.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": QUESTIONS[i % 20],
                                           "stream": True}, test="latency.responses.stream.warm", headers=NO_CACHE))
        time.sleep(PACE_S)
    summary += [summarize("responses stream (warm conn)", res, "first_token_ms"),
                summarize("responses stream (warm conn)", res, "total_ms")]
    # Responses, streaming, cold connection (new TCP+TLS every call)
    res = []
    for i in range(max(5, runs // 4)):
        res.append(m.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": QUESTIONS[(i + 7) % 20],
                                           "stream": True}, test="latency.responses.stream.cold", headers=NO_CACHE,
                            fresh=True))
        time.sleep(PACE_S)
    summary += [summarize("responses stream (new conn each)", res, "first_token_ms")]
    # Responses, blocking
    res = []
    for i in range(runs):
        res.append(m.request("POST", "/responses", {"model": MODEL, "instructions": SYSTEM,
                                                   "input": QUESTIONS[(i + 3) % 20]},
                             test="latency.responses.blocking", headers=NO_CACHE))
        time.sleep(PACE_S)
    summary += [summarize("responses blocking (full reply)", res, "total_ms")]
    # Chat Completions, streaming
    res = []
    for i in range(runs):
        res.append(m.stream("/chat/completions", {"model": MODEL, "stream": True, "messages": [
            {"role": "system", "content": SYSTEM}, {"role": "user", "content": QUESTIONS[(i + 11) % 20]}]},
            test="latency.chat.stream.warm", headers=NO_CACHE, kind="chat"))
        time.sleep(PACE_S)
    summary += [summarize("chat/completions stream (warm conn)", res, "first_token_ms"),
                summarize("chat/completions stream (warm conn)", res, "total_ms")]
    return summary


def cache_check(m: Moss) -> dict:
    body = {"model": MODEL, "instructions": SYSTEM, "input": "Say the clinic's opening hours."}
    a = m.request("POST", "/responses", body, test="cache.first")
    time.sleep(PACE_S)
    b = m.request("POST", "/responses", body, test="cache.repeat")
    usage = lambda r: (r.body or {}).get("usage") if isinstance(r.body, dict) else None
    return {"first_ms": round(a.total_ms, 1), "repeat_ms": round(b.total_ms, 1), "first_usage": usage(a),
            "repeat_usage": usage(b), "same_text": _text(a.body) == _text(b.body)}


def _text(body) -> str:
    if not isinstance(body, dict):
        return ""
    out = []
    for item in body.get("output") or []:
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                out.append(part.get("text", ""))
    return "".join(out)


def grounding(m: Moss) -> dict:
    index = os.environ.get("MOSS_INDEX", "").strip()
    out: dict = {"index": index or None}
    if index:
        q = os.environ.get("MOSS_INDEX_QUESTION", "What does the index say about shipping?")
        plain = m.request("POST", "/responses", {"model": MODEL, "input": q}, test="grounding.without_index",
                          headers=NO_CACHE)
        time.sleep(PACE_S)
        grounded = m.request("POST", "/responses", {"model": MODEL, "input": q}, test="grounding.with_index",
                             headers={**NO_CACHE, "X-Moss-Indexes": index})
        time.sleep(PACE_S)
        streamed = m.stream("/responses", {"model": MODEL, "input": q, "stream": True},
                            test="grounding.with_index.stream", headers={**NO_CACHE, "X-Moss-Indexes": index})
        out.update(plain_text=_text(plain.body), grounded_text=_text(grounded.body),
                   retrieve_ms=grounded.headers.get("x-moss-retrieve-ms"), grounded_total_ms=round(grounded.total_ms, 1),
                   plain_total_ms=round(plain.total_ms, 1), grounded_stream_first_token_ms=streamed.first_token_ms,
                   grounded_usage=(grounded.body or {}).get("usage") if isinstance(grounded.body, dict) else None)
    probe = m.request("POST", "/responses", {"model": MODEL, "input": "hi"}, test="grounding.unknown_index",
                      headers={"X-Moss-Indexes": "no-such-index-zz9"})
    out["unknown_index_status"] = probe.status
    out["unknown_index_error"] = probe.body
    return out


TOOL = {"type": "function", "name": "lookup_appointment",
        "description": "Look up a patient's next appointment by their phone number.",
        "parameters": {"type": "object", "properties": {"phone": {"type": "string"}}, "required": ["phone"]}}
TOOL_RESULT = json.dumps({"date": "2026-12-03", "time": "14:30", "dentist": "Dr. Aydin"})


def tool_round_trip(m: Moss) -> dict:
    instructions = SYSTEM + " Use lookup_appointment when the caller asks about their appointment."
    first_input = [{"role": "user", "content": "When is my next appointment? My number is 555-0142."}]
    r1 = m.request("POST", "/responses", {"model": MODEL, "instructions": instructions, "input": first_input,
                                         "tools": [TOOL]}, test="tools.responses.call", headers=NO_CACHE)
    out: dict = {"responses_call_status": r1.status, "responses_call_ms": round(r1.total_ms, 1)}
    calls = [i for i in (r1.body or {}).get("output", []) if i.get("type") == "function_call"] \
        if isinstance(r1.body, dict) else []
    out["responses_function_calls"] = [{"name": c.get("name"), "arguments": c.get("arguments")} for c in calls]
    if calls:
        history = first_input + list(r1.body["output"]) + [
            {"type": "function_call_output", "call_id": c["call_id"], "output": TOOL_RESULT} for c in calls]
        time.sleep(PACE_S)
        r2 = m.request("POST", "/responses", {"model": MODEL, "instructions": instructions, "input": history,
                                             "tools": [TOOL]}, test="tools.responses.result", headers=NO_CACHE)
        out.update(responses_answer=_text(r2.body), responses_answer_ms=round(r2.total_ms, 1),
                   responses_answer_status=r2.status,
                   answer_mentions_time="14:30" in _text(r2.body) or "2:30" in _text(r2.body))
        # Same second leg streamed: first token after a tool result is what a voice agent waits for.
        time.sleep(PACE_S)
        r2s = m.stream("/responses", {"model": MODEL, "instructions": instructions, "input": history,
                                      "tools": [TOOL], "stream": True}, test="tools.responses.result.stream",
                       headers=NO_CACHE)
        out["responses_answer_stream_first_token_ms"] = r2s.first_token_ms
        # The same call without resending tools (docs: send tools every time).
        time.sleep(PACE_S)
        r3 = m.request("POST", "/responses", {"model": MODEL, "instructions": instructions, "input": history},
                       test="tools.responses.result.no_tools", headers=NO_CACHE)
        out["no_tools_resent_status"] = r3.status
        out["no_tools_resent_body"] = r3.body if r3.status != 200 else _text(r3.body)
    # Chat Completions variant
    time.sleep(PACE_S)
    ctool = {"type": "function", "function": {"name": TOOL["name"], "description": TOOL["description"],
                                              "parameters": TOOL["parameters"]}}
    msgs = [{"role": "system", "content": instructions},
            {"role": "user", "content": "When is my next appointment? My number is 555-0142."}]
    c1 = m.request("POST", "/chat/completions", {"model": MODEL, "messages": msgs, "tools": [ctool]},
                   test="tools.chat.call", headers=NO_CACHE)
    out["chat_call_status"] = c1.status
    try:
        msg = c1.body["choices"][0]["message"]
        tcalls = msg.get("tool_calls") or []
    except (TypeError, KeyError, IndexError):
        msg, tcalls = None, []
    out["chat_tool_calls"] = [{"name": t["function"]["name"], "arguments": t["function"]["arguments"]} for t in tcalls]
    if tcalls:
        msgs2 = msgs + [msg] + [{"role": "tool", "tool_call_id": t["id"], "content": TOOL_RESULT} for t in tcalls]
        time.sleep(PACE_S)
        c2 = m.request("POST", "/chat/completions", {"model": MODEL, "messages": msgs2, "tools": [ctool]},
                       test="tools.chat.result", headers=NO_CACHE)
        try:
            out["chat_answer"] = c2.body["choices"][0]["message"]["content"]
        except (TypeError, KeyError, IndexError):
            out["chat_answer"] = c2.body
        out["chat_answer_ms"] = round(c2.total_ms, 1)
    return out


def state_and_errors(m: Moss) -> list[dict]:
    """Each probe: what we sent, the status and error code we expected, what came back."""
    probes = [
        ("previous_response_id", "POST", "/responses",
         {"model": MODEL, "input": "and on Saturday?", "previous_response_id": "resp_123"}, {}, None, 400,
         "unsupported_parameter"),
        ("unknown model", "POST", "/responses", {"model": "moss/does-not-exist", "input": "hi"}, {}, None, 400,
         "model_not_found"),
        ("vendor name without own key", "POST", "/responses", {"model": "gpt-6-luna", "input": "hi"}, {}, None, 400,
         "missing_provider_key"),
        ("unknown top-level field", "POST", "/responses", {"model": MODEL, "input": "hi", "max_tool_calls": 3}, {},
         None, 400, "unsupported_parameter"),
        ("built-in tool web_search", "POST", "/responses",
         {"model": MODEL, "input": "hi", "tools": [{"type": "web_search"}]}, {}, None, 400, "unsupported_parameter"),
        ("background true", "POST", "/responses", {"model": MODEL, "input": "hi", "background": True}, {}, None, 400,
         "unsupported_parameter"),
        ("33 indexes", "POST", "/responses", {"model": MODEL, "input": "hi"},
         {"X-Moss-Indexes": ",".join(f"i{n}" for n in range(33))}, None, 400, None),
        ("wrong key", "POST", "/responses", {"model": MODEL, "input": "hi"}, {}, "WRONG", 401, None),
        ("no auth header", "POST", "/responses", {"model": MODEL, "input": "hi"}, {}, "", 401, None),
        ("chat: messages missing", "POST", "/chat/completions", {"model": MODEL}, {}, None, 400, None),
        ("instructions as list", "POST", "/responses", {"model": MODEL, "input": "hi", "instructions": ["a"]}, {},
         None, 400, None),
        ("max_output_tokens=5", "POST", "/responses",
         {"model": MODEL, "input": "Tell me a long story about teeth.", "max_output_tokens": 5}, NO_CACHE, None, 200,
         "status=incomplete"),
    ]
    out = []
    for name, method, path, body, hdrs, auth, want_status, want_code in probes:
        auth_value = None
        if auth == "WRONG":
            auth_value = f"{m.project_id}:not-the-key"
        elif auth == "":
            auth_value = ""
        r = m.request(method, path, body, test=f"probe.{name}", headers=hdrs, auth=auth_value)
        err = r.body.get("error") if isinstance(r.body, dict) else None
        got_code = (err or {}).get("code") if isinstance(err, dict) else None
        if want_code == "status=incomplete" and isinstance(r.body, dict):
            got_code = f"status={r.body.get('status')}"
        out.append({"probe": name, "want_status": want_status, "got_status": r.status, "want_code": want_code,
                    "got_code": got_code, "message": (err or {}).get("message") if isinstance(err, dict) else None,
                    "request_id": r.headers.get("x-moss-request-id"), "x_should_retry": r.headers.get("x-should-retry"),
                    "ok": r.status == want_status and (want_code is None or want_code == got_code)})
        time.sleep(PACE_S)
    # Malformed JSON body
    r = m.request("POST", "/responses", None, test="probe.malformed json", raw_body=b'{"model": "moss/fastlane-1", "input": ')
    err = r.body.get("error") if isinstance(r.body, dict) else None
    out.append({"probe": "malformed JSON", "want_status": 400, "got_status": r.status, "want_code": None,
                "got_code": (err or {}).get("code") if isinstance(err, dict) else None,
                "message": (err or {}).get("message") if isinstance(err, dict) else str(r.body)[:200],
                "request_id": r.headers.get("x-moss-request-id"), "x_should_retry": r.headers.get("x-should-retry"),
                "ok": r.status == 400})
    # Stop early: drop the stream after the first token, then check the connection still serves the next call.
    s = m.stream("/responses", {"model": MODEL, "instructions": SYSTEM, "input": "Describe your clinic in detail.",
                                "stream": True}, test="probe.stop_early", headers=NO_CACHE, stop_after_first_token=True)
    nxt = m.request("GET", "/models", test="probe.after_stop_early")
    out.append({"probe": "stop stream after first token", "want_status": 200, "got_status": s.status,
                "want_code": None, "got_code": None, "message": f"first token {s.first_token_ms} ms; next call {nxt.status}",
                "request_id": s.headers.get("x-moss-request-id"), "x_should_retry": None, "ok": nxt.status == 200})
    return out


def main() -> None:
    m = Moss("inference")
    started = time.time()
    models = m.request("GET", "/models", test="models.list")
    model_ids = [d.get("id") for d in (models.body or {}).get("data", [])] if isinstance(models.body, dict) else []
    runs = int(os.environ.get("MOSS_LATENCY_RUNS", "20"))
    report = {
        "started": started, "models_status": models.status, "models": model_ids,
        "latency": latency(m, runs),
        "reply_cache": cache_check(m),
        "grounding": grounding(m),
        "tools": tool_round_trip(m),
        "probes": state_and_errors(m),
    }
    report["finished"] = time.time()
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "inference.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"latency": report["latency"], "probes_ok": sum(p["ok"] for p in report["probes"]),
                      "probes": len(report["probes"])}, indent=2))


if __name__ == "__main__":
    main()

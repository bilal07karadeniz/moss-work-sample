# Moss Inference review

Bilal Karadeniz, 26 Nov 2026. I build brisvox, an open-source voice agent engine in Rust, so I tested Moss as
its LLM. All numbers are from my own runs from Istanbul. Every request is in `logs/`, and the full numbers are in
`results/RESULTS.md`. I spent $0.18. Code, scripts and logs: https://github.com/bilal07karadeniz/moss-work-sample

**In one line:** I changed only the LLM in a brisvox voice agent, from OpenRouter `gpt-6-luna` to
`moss/fastlane-1`. Voice-to-voice latency went from 1521 to 957 ms (p50) and from 2109 to 1089 ms (p90).

## Portal

**Liked**

- The task page is the best part: a recording with captions, every step, and time and cost against the limits.
  The recording showed me that example.com has no `<h1>` any more, so it fixed a wrong grade in my own test.
- Logs & Traces finds a request by its ID for 30 days, with tokens and cost. Usage agrees with my logs.
- Project-wide retrieval, default model and system prompt in Configure.

**Didn't like**

- Failed requests are not in the Portal. My 22 rejected requests (400, 403) and two 502s are missing, and
  Analytics says 100% success. When I search the request ID that Moss sent back with a 400, I get "No request
  with that ID".
- It shows only server-side latency. The Portal says first token p50 is 49 ms. My client waits 191 ms on a warm
  connection with a 43 ms network round trip.
- The task list shows a task that could not open its page as "Succeeded".

**Would improve:** log every request that gets a request ID, errors too, and show client latency next to model
latency.

## Inference API

**Liked**

- Fast and steady: streamed first token 192 ms p50, 211 ms p95 on Responses, and 191 / 194 ms on Chat Completions.
- Grounding works and does not guess. With the index, 3 of 3 catalog questions were right. Without it, the model
  said it did not know. Retrieval took 9 ms.
- Error messages say what to do: "previous_response_id is not supported. Send the whole conversation in input."
- With the same model (`gpt-6-luna`) on both, Moss gave the first token in 617 ms and OpenRouter in 732 ms.

**Didn't like**

- Two calls got a 502 with an empty body: no request ID, no retry hint. The next call worked on another server,
  so the gateway could have retried it.
- When 3 to 8 calls arrive together, 1 to 3 of them come 200 to 250 ms late (p95 530 ms, against 207 ms for a
  single call). In voice, people stop talking at the same moments, so callers hear this delay.
- The first grounded call spent 926 ms on retrieval (cold index).
- 120 requests per minute is only about 8 voice calls that use tools.

**Would improve:** retry 502s inside the gateway, or return JSON with a request ID. Add a `Server-Timing` header.
Let me warm an index. Give realtime users a limit by open conversations, not requests per minute.

## Tasks API

**What I tested:** 3 tasks with known answers, 5 tasks built to fail, 18 validation errors, `Idempotency-Key`
replay and conflict, cancel (queued, running, finished, twice) and task files. Limits $1.61, billed $0.13.

**Results**

- Correct: the fastlane-1 output price from the Moss docs (1.75), HN top 5 (5 of 5 in order, checked against the
  HN API, 5.8 cents), and the example.com `<h1>` (empty, which is right, because the page has none now).
- $0.01 budget: `failed` with `budget_exceeded`, billed $0.0107.
- 60 s limit: one task finished in 56 s with 30 of 30 correct counts. A harder one stopped at 61 s with `time_limit`.
- Missing phone number and dead URL: both honest, no made-up answers.

**Where I broke it**

1. The dead URL task ended `completed`. The failure is only in the text, even though `no_answer` exists.
2. Missing data returns `""` with `verification.passed: true`, so "not found" and "found but empty" look the same.
3. A timeout returns `result: null` after 5 steps and 48k tokens of paid work.
4. Create says `queued`, but the task is already running, so a quick cancel is never instant. The API returns
   `trace_available` and `video_state`, but the docs have no endpoint for them.

**Liked:** idempotency works exactly as documented. Every validation error says what to fix. Cancel stops a
running task in 0.5 s. Queue time was 0 s for all 11 tasks.

**Would improve:** a `null` or `not_found` value in structured output, `failed` with `no_answer` when the agent
cannot do the job, partial output when a time or budget limit stops a task, and docs for trace and video.

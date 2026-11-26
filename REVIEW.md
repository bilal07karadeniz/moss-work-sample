# Moss Inference: a review from someone who ships voice agents

Bilal Karadeniz, 2026-11-26. I build [brisvox](https://github.com/bilal07karadeniz/brisvox), a Rust voice-agent
engine, and tested Moss the way I would decide whether to ship it as brisvox's LLM. Every claim below is in
`logs/*.jsonl` (timings and `X-Moss-*` headers, never the key) and `results/RESULTS.md`. Client: Istanbul, public
internet, Python stdlib. Spend: ~$0.18 of $1.70.

**Headline.** I swapped only the LLM in a brisvox agent, from OpenRouter `openai/gpt-6-luna` to `moss/fastlane-1`
(reply cache off). Voice-to-voice went from **1521 to 957 ms p50 and from 2109 to 1089 ms p90**. The two arms
ran 60 vs 40 turns in interleaved blocks, with the same STT, TTS and fixture. With the model held equal (luna
through both gateways), Moss's first token is 617 vs 732 ms p50. So `fastlane-1` is the win, and Moss's path is a
little faster on top.

## Inference API

**Liked**
- **Fast and tight:** streamed first token 191.9 / 211.0 ms p50 / p95 on Responses and 190.8 / 194.4 on Chat, with
  deltas every 17 ms. The prefix cache is automatic: 3,200 of 3,204 tokens cached and a first token of 204–218 ms
  on a long system prompt.
- **Grounding that works and doesn't invent:**
  - **Accuracy:** 3 of 3 catalog questions answered correctly with `X-Moss-Indexes`. Without the index, the
    model said "I don't have that detail" all 3 times instead of making something up.
  - **Retrieval time:** `X-Moss-Retrieve-Ms` 9.3 ms p50.
  - **First token:** 194 → 244 ms with grounding on.
- **Errors that name the fix:** `previous_response_id is not supported. Send the whole conversation in input.` All
  11 error probes returned the documented status. Being stateless fits voice engines, which resend history
  anyway, and tool round trips work on both APIs.

**Didn't like**
- **The error you most need to handle has no body.** Two calls in a row got a bare 502 from Caddy in 138 ms, with
  no body, no request id and no `x-should-retry`. The very next call was served by another box (`x-moss-box`
  `gh013`), so a retry inside the gateway would have hidden both. That was 2 of ~600 calls.
- **The first grounded call took 926 ms to retrieve** (a cold index; 9 ms after that). In voice, that lands on a
  caller's first question.
- **~140 ms in front of the model that no one shows.** The Portal says first token p50 is 49 ms. My client sees
  191 ms on a warm connection with a 43 ms RTT.
- **Simultaneous calls slow each other.** 1 call: 195 ms. 3–8 calls at once: p50 255–298 ms, and in all 9 bursts
  1–3 calls landed 200–250 ms behind the rest (p95 ~530 ms). A voice product's users finish talking at the same
  moments, so they hear this tail.
- **120 rpm (burst 10) is about 8 voice calls:** at one turn per 6 s and a tool call on half the turns, each call
  needs 15 rpm.

**Would improve:** retry edge 502s onto a healthy box, or at least return JSON, a request id and
`x-should-retry`. Add `Server-Timing` (gateway, queue, prefill, retrieval), so the 140 ms is visible. Let a client
warm an index. Offer a realtime limit counted in open conversations, not rpm.

## Tasks API

**Tested:** 3 tasks with known answers, 5 built to fail, 18 validation probes, `Idempotency-Key` replay and
conflict, and cancel (queued, running, finished, twice) plus files. The caps totalled $1.61 and $0.13 was billed.
Queue time was 0 s on all 11 tasks.

| Task | Result | Verdict |
|---|---|---|
| K1 example.com `<h1>` | `{"heading": ""}` | correct: today's page has no `<h1>`, and it didn't invent "Example Domain" |
| K2 output price on the Moss pricing page | `1.75`, 20 s, $0.007 | correct |
| K3 HN top 5 (`moss-balanced`) | 5/5 in order vs the HN API, 20 s, $0.058 | correct |
| F1 phone number that isn't there | `{"phone": ""}`, `completed`, `verification.passed: true` | honest, ambiguous |
| F2 dead host | `completed`; the reason is only in prose (`ERR_SOCKS_CONNECTION_FAILED`) | honest, wrong status |
| F3 $0.01 budget | `failed` / `budget_exceeded`, billed $0.0107 | works, 7% over |
| F4 30 comment counts in 60 s | done in 56 s; 30/30 counts match the HN API | better than designed |
| F4b 30 page loads in 60 s | `failed` / `time_limit` at 61.4 s, **`result: null`** | works, work lost |

(I first graded K1 wrong against the old "Example Domain" heading. The task's recording showed the redesigned
page, and its HTML confirmed it.)

**Where it broke**
1. **Failure lives in prose.** F2 could not load the page yet ended `completed`, although `no_answer` exists. The
   Portal then lists it as Succeeded and counts it in a 78% success rate.
2. **"Not found" and "found, empty" look the same.** F1 returns `""` with `passed: true`. Verification checks the
   schema shape only, so a caller cannot tell an answer from its absence.
3. **Timeouts discard paid work.** F4b ran 5 steps over 48k tokens, then returned nothing.
4. **Docs drift.** Create says `queued`, but the task is already running, so "a queued task is cancelled at once"
   never happens. `trace_available` and `video_state` have no documented endpoint, though the Portal plays the video.

**Liked:** idempotency matches the docs exactly. All 18 validation errors name the fix. Cancelling a running
task stops it in 0.5 s for $0.0004. HN top 5 cost 5.8 cents. The agent refuses to guess (K1, F1, F2).

**Would improve:** a `not_found` or `null` path in structured output; map "could not" to `failed` / `no_answer`;
return partial output on `time_limit` and `budget_exceeded`; document the trace and video routes.

## Portal

**Liked:**
- **Each task page** shows a recording with captions, a step timeline, and time and spend against their limits.
  That recording is how I caught my own K1 mistake.
- **Logs & Traces** finds any successful request by `X-Moss-Request-Id` for 30 days, with tokens and charge.
- **Usage** breaks spend down by model and agrees with my logs.
- **Configure** sets project-wide retrieval, a default model and a system prompt.
- **The cURL quickstart** spells out `Bearer $MOSS_PROJECT_ID:$MOSS_PROJECT_KEY`.

**Didn't like:**
- **Failures are invisible.** My 22 rejected requests that carried a valid key (400/403, each returned with a
  request id) and both 502s appear nowhere. The Error filter is empty, Analytics says 100% success, and looking up the request id of
  a 400 says "No request with that ID". The request id a client gets on an error leads nowhere.
- **Latency is shown only from the server's side (49 ms),** never the ~191 ms a client actually waits.

**Would improve:** log rejected and edge-failed requests under their ids, and show client-visible latency next to
model latency.

## How Moss fits brisvox's LLM stage

No code needed. brisvox's `openai_compatible` adapter takes `base_url = "https://inference.moss.dev/v1"`,
`model = "moss/fastlane-1"` and a key env holding `id:key` (`brisvox/agent_moss.toml`). brisvox already resends
history and runs tools itself, which is the contract Moss's stateless API expects, and its 10-minute connection
pool avoids the ~105 ms TCP+TLS cost of a cold call. Grounding can be switched on for the whole project in
Configure. Per-agent indexes or `X-Moss-Cache` need a header option that brisvox's agent file lacks today (the
library has it). brisvox should also warm the index at call start, to avoid the 926 ms first retrieval. For
hundreds of calls per container, the rpm limit and the +200–250 ms burst straggler are what I would ask Moss to
move.

| brisvox LLM | turns | V2V p50 | V2V p90 | LLM first token | first sentence |
|---|---|---|---|---|---|
| OpenRouter `openai/gpt-6-luna` | 60 | 1521 ms | 2109 ms | 671 ms | 799 ms |
| Moss `fastlane-1`, cache off | 40 | 957 ms | 1089 ms | 201 ms | 278 ms |
| Moss `fastlane-1`, cache on (30/40 hits) | 40 | 818 ms | 1087 ms | 153 ms | 153 ms |

End-of-turn detection took 288–302 ms p50 in every arm, so the gap is the LLM.

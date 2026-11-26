# Moss Inference: a review from someone who ships voice agents

Bilal Karadeniz, 2026-11-26. I build [brisvox](https://github.com/bilal07karadeniz/brisvox), a Rust voice-agent
engine, and tested Moss the way I would decide whether to ship it as brisvox's LLM. Every claim below is in
`logs/*.jsonl` (timings and `X-Moss-*` headers, never the key) and `results/RESULTS.md`. Client: Istanbul, public
internet, Python stdlib. Spend: ~$0.16 of $1.70.

**Headline.** I swapped only the LLM in a brisvox agent, from OpenRouter `openai/gpt-6-luna` to `moss/fastlane-1`
with the reply cache off. Voice-to-voice went from **1521 to 957 ms p50 and from 2109 to 1089 ms p90**. The two
arms ran 60 vs 40 turns in interleaved blocks, with the same STT, TTS and fixture. LLM first token inside the
pipeline: 671 → 201 ms. The models differ in size, and I did not grade answer quality.

## Inference API

**Liked**
- **Fast and tight:** streamed first token 191.9 / 211.0 ms p50 / p95 on Responses and 190.8 / 194.4 on Chat, with
  deltas every 17 ms. The prefix cache is automatic: on a 3.2k-token system prompt, 3,200 tokens were cached and the
  first token came at 204–218 ms.
- **The reply cache shows up in a live pipeline:** brisvox's repeated fixture hit it on 30 of 40 turns, with the
  whole reply arriving at ~150 ms.
- **Errors that name the fix:** `previous_response_id is not supported. Send the whole conversation in input.` All
  11 error probes returned the documented status. Being stateless fits voice engines, which resend history anyway,
  and tool round trips work on both APIs.

**Didn't like**
- **The error you most need to handle has no body.** Two calls in a row got a bare 502 from Caddy in 138 ms, with
  no body, no request id and no `x-should-retry`. The very next call was served by another box (`x-moss-box`
  `gh013`), then traffic went back to `g904212`, so a retry inside the gateway would have hidden both. That was 2
  of ~500 calls.
- **~95 ms before the model starts.** RTT is 43 ms, but a warm `GET /models`, which touches no model, takes 137 ms.
  That is most of the 191 ms first token, and nothing tells the client how it splits.
- **Simultaneous calls slow each other.** 1 call: 195 ms. 3–8 calls at once: p50 255–298 ms, and in all 9 bursts
  1–3 calls landed 200–250 ms behind the rest (p95 ~530 ms). A voice product's users finish talking at the same
  moments, so they hear this tail.
- **120 rpm (burst 10) is about 8 voice calls:** at one turn per 6 s and a tool call on half the turns, each call
  needs 15 rpm.

**Would improve:** retry edge 502s onto a healthy box, or at least return JSON, a request id and
`x-should-retry`. Add a `Server-Timing` header (gateway, queue, prefill). Offer a realtime limit counted in open
conversations, not rpm. State whether a reply-cache hit bills its 11 `output_tokens`. Grounding is untested: the
project has no index, and the unknown-index 403 is clear.

## Tasks API

**Tested:** 3 tasks with known answers, 5 built to fail, 18 validation probes, `Idempotency-Key` replay and
conflict, and cancel (queued, running, finished, twice) plus files. The caps totalled $1.61 and $0.130 was billed.
Queue time was 0 s on all 11 tasks.

| Task | Result | Verdict |
|---|---|---|
| K1 example.com `<h1>`, with schema | `{"heading": ""}` and **`verification.passed: true`** | **wrong, marked passed** |
| K2 output price on the Moss pricing page | `1.75`, 20 s, $0.007 | correct |
| K3 HN top 5 (`moss-balanced`) | 5/5 in order vs the HN API, 20 s, $0.058 | correct |
| F1 phone number that isn't there | `{"phone": ""}`, `completed` | honest |
| F2 dead host | `completed`; the reason is only in prose (`ERR_SOCKS_CONNECTION_FAILED`) | honest, wrong status |
| F3 $0.01 budget | `failed` / `budget_exceeded`, billed $0.0107 | works, 7% over |
| F4 30 comment counts in 60 s | done in 56 s; 30/30 counts match the HN API | better than designed |
| F4b 30 page loads in 60 s | `failed` / `time_limit` at 61.4 s, **`result: null`** | works, work lost |

**Where it broke**
1. **Verification passes empty answers.** example.com's heading is "Example Domain", and a free-text task on the
   same page read the title fine. K1 still returned `""` with `passed: true`, as did F1. The check is schema shape
   only, but a field named `passed` invites trust.
2. **Failure lives in prose.** F2 could not load the page and ended `completed`, although `no_answer` exists, so a
   program has to parse English to find out.
3. **Timeouts discard paid work.** F4b ran 5 steps over 45k tokens, then returned nothing.
4. **Docs drift.** Create says `queued`, but the task is already running, so "a queued task is cancelled at once"
   never happens. Task objects carry `trace_available` and `video_state: "ready"`, but neither has a documented
   endpoint.

**Liked:** idempotency matches the docs exactly (replay `Idempotent-Replayed: true`, conflict 422). All 18
validation errors name the fix. Cancelling a running task stops it in 0.5 s for $0.0004. HN top 5 cost 5.8 cents.

**Would improve:** name the check (`schema_valid` vs `goal_met`) and fail empty required strings. Map "could not"
to `failed` / `no_answer`. Return partial output on `time_limit` and `budget_exceeded`. Document trace and video.

## Portal

I used it only to create the project and key. The bearer is `project_id:key`, two values shown separately. My
first try put the id where the key goes, and the 401 said only "The project credentials were rejected". A "copy
bearer token" button, or "the key part starts with `moss_`", would fix that.

## How Moss fits brisvox's LLM stage

No code needed. brisvox's `openai_compatible` adapter takes `base_url = "https://inference.moss.dev/v1"`,
`model = "moss/fastlane-1"` and a key env holding `id:key` (`brisvox/agent_moss.toml`). brisvox already resends
history and runs tools itself, which is the contract Moss's stateless API expects. Its 10-minute connection pool
avoids the ~105 ms TCP+TLS cost of a cold call (300 vs 192 ms first token). To close for production: the agent file
cannot set request headers yet (the library can), so `X-Moss-Indexes` and `X-Moss-Cache` need a config option.
For hundreds of calls per container, the rpm limit and the +200–250 ms burst straggler are what I would ask Moss to
move.

| brisvox LLM | turns | V2V p50 | V2V p90 | LLM first token | first sentence |
|---|---|---|---|---|---|
| OpenRouter `openai/gpt-6-luna` | 60 | 1521 ms | 2109 ms | 671 ms | 799 ms |
| Moss `fastlane-1`, cache off | 40 | 957 ms | 1089 ms | 201 ms | 278 ms |
| Moss `fastlane-1`, cache on (30/40 hits) | 40 | 818 ms | 1087 ms | 153 ms | 153 ms |

End-of-turn detection took 288–302 ms p50 in every arm, so the gap is the LLM.

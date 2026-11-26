# Moss Inference work sample

My review of Moss Inference (Portal, Inference API, Tasks API), with the code, scripts and logs behind it.

- **[REVIEW.md](REVIEW.md)**: the one-page review ([REVIEW.pdf](REVIEW.pdf) is the same page as a PDF).
- **[results/RESULTS.md](results/RESULTS.md)**: every number, generated from `results/*.json`.
- `logs/*.jsonl`: one line per request and response, with timings and all `X-Moss-*` and rate-limit headers.

All runs were made on 2026-11-26 from Istanbul over the public internet, with the Python standard library only.
Total spend was about $0.18.

## How Moss fits brisvox's LLM stage

[brisvox](https://github.com/bilal07karadeniz/brisvox) is my open-source voice agent engine in Rust. Moss works
with it today without code: the `openai_compatible` LLM adapter takes
`base_url = "https://inference.moss.dev/v1"`, `model = "moss/fastlane-1"` and a key variable that holds
`project_id:project_key` (see `brisvox/agent_moss.toml`). brisvox already sends the full history every turn and
runs tools itself, which is what Moss's stateless API expects. Its connection pool keeps connections open for 10
minutes, which saves the ~105 ms of TCP and TLS that a cold call costs. Grounding can be turned on for the whole
project in the Portal. Per-agent indexes or `X-Moss-Cache` need a header setting that brisvox's agent file does
not have yet (the library does), and brisvox should warm the index when a call starts, because the first
retrieval took 926 ms.

The A/B below changes only the LLM. STT (Deepgram nova-3), TTS (Deepgram Aura-2), turn detection, the test audio
and the client stay the same. Blocks were interleaved, one call at a time.

| brisvox LLM | turns | V2V p50 | V2V p90 | LLM first token p50 | first sentence p50 |
|---|---|---|---|---|---|
| OpenRouter `openai/gpt-6-luna` | 60 | 1521 ms | 2109 ms | 671 ms | 799 ms |
| Moss `fastlane-1`, reply cache off | 40 | 957 ms | 1089 ms | 201 ms | 278 ms |
| Moss `fastlane-1`, reply cache on (30 of 40 hits) | 40 | 818 ms | 1087 ms | 153 ms | 153 ms |

End-of-turn detection took 288 to 302 ms p50 in every arm, so the difference is the LLM. With the same model
(`gpt-6-luna`) through both gateways, Moss's first token was 617 ms p50 and OpenRouter's 732 ms, so most of the
gain is `fastlane-1` itself.

## What is in the repo

- `harness/client.py`: HTTPS client, SSE reader and JSONL logging. The `Authorization` and `X-Moss-Provider-Key`
  headers are never logged, and the project ID and key are removed from every logged string.
- `harness/inference_tests.py`: first-token latency (warm and new connections), streaming vs blocking, Chat
  Completions streaming, the reply cache, a tool call round trip with the history resent (both APIs),
  `previous_response_id`, and error probes.
- `harness/inference_extra.py`: network floor (TCP, TLS, a warm `GET /models`), Responses vs Chat interleaved,
  delta timing, prefix cache on a ~3.2k-token prompt.
- `harness/concurrency.py`: first token with 1, 3, 5 and 8 calls started at the same moment.
- `harness/grounding_and_catalog.py`: grounding with the project's index (with and without `X-Moss-Indexes`), and
  a catalog vendor model without a provider key.
- `harness/luna_same_model.py`: the same model (`openai/gpt-6-luna`) through Moss and through OpenRouter.
- `harness/tasks_tests.py`: 3 tasks with known answers, 4 built to fail, request validation, `Idempotency-Key`
  replay and conflict, cancel, and task files. Every task sets `limits.max_cost_usd`.
- `harness/tasks_timeout.py`: a task that cannot finish in 60 s, so the time limit really fires.
- `harness/f4_check.py`: checks a task's 30 comment counts against the official Hacker News API.
- `harness/report.py`: builds `results/RESULTS.md`.
- `brisvox/`: the voice-to-voice A/B (`run_ab.sh`, the agent files, `nocache_proxy.py` for the cache-off arm,
  `ab_summary.py`, raw results and server logs in `brisvox/results/`). It needs a local brisvox build and
  Deepgram and OpenRouter keys; the machine-specific paths are at the top of the script.
- `scripts/make_pdf.py`: builds `REVIEW.pdf` from `REVIEW.md`.

## Run

```
cp .env.example .env        # fill MOSS_PROJECT_ID and MOSS_PROJECT_KEY (optional MOSS_INDEX)
python harness/inference_tests.py
python harness/inference_extra.py
python harness/concurrency.py
python harness/grounding_and_catalog.py      # MOSS_INDEX names the index (default product-search-context)
OPENROUTER_API_KEY=... python harness/luna_same_model.py
python harness/tasks_tests.py
python harness/tasks_timeout.py
python harness/f4_check.py
python harness/report.py
```

`MOSS_LATENCY_RUNS` (default 20) sets the number of samples per latency series.

`logs/run1-harness-bug/` keeps the first inference run, which had a bug in my client (see its `NOTE.md`). The two
502s in it are real server responses.

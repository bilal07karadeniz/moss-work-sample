# Moss Inference work sample

A small, dependency-free Python harness (standard library only, Python 3.11+) that exercises
Moss Inference (`https://inference.moss.dev/v1`): the Inference API (Responses and Chat
Completions on `moss/fastlane-1`) and the Tasks API (hosted browser agents).

- `harness/client.py`: HTTPS client, SSE reader, JSONL logging with timings and every `X-Moss-*` /
  rate-limit header. The `Authorization` and `X-Moss-Provider-Key` headers are never logged, and the
  project id and key are scrubbed from every logged string.
- `harness/inference_tests.py`: first-token latency (p50/p95, warm and new connections), streaming vs
  blocking, Chat Completions streaming, the reply cache, grounding with `X-Moss-Indexes`, a function-tool
  round trip with the history resent (both APIs), `previous_response_id`, and error probes.
- `harness/tasks_tests.py`: 3 tasks with known answers, 4 built to fail, request validation,
  `Idempotency-Key` replay and conflict, cancel (queued and running), and task files. Every task sets
  `limits.max_cost_usd`; the caps add up to $1.41.
- `harness/inference_extra.py`: network floor (TCP, TLS, a warm `GET /models`), Responses vs Chat streaming
  interleaved, delta cadence, prefix cache on a ~3.2k-token prompt.
- `harness/concurrency.py`: first token with 1, 3, 5 and 8 calls started at the same instant.
- `harness/f4_check.py`: checks the F4 task's 30 comment counts against the official Hacker News API.
- `harness/tasks_timeout.py`: F4b, a task that cannot finish in 60 s, so the time limit really fires.
- `harness/report.py`: builds `results/RESULTS.md` from `results/*.json`.
- `brisvox/`: voice-to-voice A/B inside brisvox, Moss vs OpenRouter as the LLM, everything else the same
  (`run_ab.sh`, the agent files, `nocache_proxy.py` for the reply-cache-off arm, `ab_summary.py`, raw results
  and server logs in `brisvox/results/`).
  Needs a local brisvox build and Deepgram/OpenRouter keys; machine-specific paths are at the top of the script.
- `logs/*.jsonl`: one line per request/response.
- `REVIEW.md`: the one-page review.

## Run

```
cp .env.example .env        # then fill MOSS_PROJECT_ID and MOSS_PROJECT_KEY (optional MOSS_INDEX)
python harness/inference_tests.py
python harness/inference_extra.py
python harness/concurrency.py
python harness/tasks_tests.py
python harness/tasks_timeout.py
python harness/f4_check.py
python harness/report.py
```

`MOSS_LATENCY_RUNS` (default 20) sets the samples per latency series.

`logs/run1-harness-bug/` keeps the first inference run, which had a client bug (see its `NOTE.md`); the two
502s in it are real server responses.

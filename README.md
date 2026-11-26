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
- `harness/report.py`: builds `results/RESULTS.md` from `results/*.json`.
- `logs/*.jsonl`: one line per request/response.
- `REVIEW.md`: the one-page review.

## Run

```
cp .env.example .env        # then fill MOSS_PROJECT_ID and MOSS_PROJECT_KEY (optional MOSS_INDEX)
python harness/inference_tests.py
python harness/tasks_tests.py
python harness/report.py
```

`MOSS_LATENCY_RUNS` (default 20) sets the samples per latency series.

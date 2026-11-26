# First run, superseded

These logs come from the first run (2026-11-26 ~06:50 UTC). The harness had a bug: after a Chat
Completions stream's `data: [DONE]` it did not drain the rest of the chunked body, so the next request
on that keep-alive connection failed and reconnected (new TCP + TLS, ~104 ms from this machine).
That made whichever call followed a Chat Completions stream look ~110 ms slower, and it first looked
like a Responses-vs-Chat difference. Fixed in `harness/client.py` (drain after `[DONE]`, log reconnects);
the numbers in `results/RESULTS.md` come from the rerun. The two bare 502s (Caddy via Google LB, empty
body, no `X-Moss-Request-Id`) at 06:57:31 UTC in `inference_extra.jsonl` are real server responses.

# Voice-to-voice latency (live)

- command: `brisvox-bench v2v --url ws://127.0.0.1:28090 --fixtures en_two_utterances --runs 10 --concurrency 1 --min-gap-ms 9000 --keep-invalid --out-md C:/Bilal/moss-work-sample/brisvox/results/v2v-moss-nocache-2.md --out-json C:/Bilal/moss-work-sample/brisvox/results/v2v-moss-nocache-2.json`
- brisvox 0.1.4 (release build), windows x86_64, 32 logical cores, 2 tokio workers (threads, not a CPU limit: other threads may use any core)
- live: one client, one call at a time, real time (SystemClock), 20 ms frames
- **not a Linux run** (windows): timers and scheduling differ from the Linux servers the engine targets, so these numbers show trends only; numbers for comparisons (with other engines, or against latency targets) come from a Linux run
- target: `ws://127.0.0.1:28090`
- 10 runs of each of en_two_utterances (utterances spaced at least 9000 ms apart, 20 ms frames, silence below peak 200, response window 10000 ms after the fixture)

**V2V** = arrival at the client of the first non-silent bot audio after the ground-truth end of the user's speech, minus that end of speech (docs/LATENCY.md). Percentiles are nearest rank over answered turns; below 100 samples p99 is the maximum.

## Summary

| scope | turns | answered | talked over | bot speaking | no response | p50 ms | p90 ms | p95 ms | p99 ms | max ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 20 | 20 | 0 | 0 | 0 | 952.179 | 1061.288 | 1085.595 | 1222.973 | 1222.973 |
| fixture en_two_utterances | 20 | 20 | 0 | 0 | 0 | 952.179 | 1061.288 | 1085.595 | 1222.973 | 1222.973 |
| language en | 20 | 20 | 0 | 0 | 0 | 952.179 | 1061.288 | 1085.595 | 1222.973 | 1222.973 |

## Per turn

| fixture | turn | lang | EOS at ms | answered/turns | p50 ms | p90 ms | p99 ms | max ms |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| en_two_utterances | 0 | en | 6670.000 | 10/10 | 957.213 | 1085.595 | 1222.973 | 1222.973 |
| en_two_utterances | 1 | en | 18430.000 | 10/10 | 925.089 | 988.260 | 1020.368 | 1020.368 |

## Calls

10 calls (0 barge-in calls, 0 memory calls), 0 failed. Largest client send lateness 26.206 ms (a frame sent later than its deadline delays the end of speech the server sees by as much).

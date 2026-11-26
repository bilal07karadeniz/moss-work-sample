#!/usr/bin/env bash
# A/B of brisvox's LLM stage: Moss (moss/fastlane-1, Chat Completions) vs OpenRouter (openai/gpt-6-luna),
# everything else identical (Deepgram nova-3 STT, Deepgram Aura TTS, Smart Turn v3.2, Silero VAD, the same
# fixture and client). Blocks interleave moss, openrouter, moss, openrouter; 10 calls / 20 turns each,
# one call at a time. Keys come from env files and are never printed.
# Machine-specific paths: a local brisvox checkout with release binaries and models.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BRISVOX=${BRISVOX:-/c/Projelerim/brisvox}
BIN=${BIN:-$HERE/bin}
OUT="$HERE/results"
mkdir -p "$BIN" "$OUT"
cp -f "$BRISVOX/target/release/brisvox-server.exe" "$BRISVOX/target/release/brisvox-bench.exe" "$BIN/"

run_block() {  # $1 = moss|openrouter, $2 = block number
  local which=$1 n=$2 log="$OUT/server-$1-$2.log"
  (
    set -a; . "$BRISVOX/.env"; . "$HERE/../.env"; set +a
    if [[ $which == moss* ]]; then
      export OPENAI_COMPATIBLE_API_KEY="$MOSS_PROJECT_ID:$MOSS_PROJECT_KEY"
    else
      export OPENAI_COMPATIBLE_API_KEY="${OPENAI_COMPATIBLE_API_KEY:-${OPENROUTER_API_KEY:-}}"
    fi
    export BRISVOX_AGENT_FILE="$HERE/agent_$which.toml" BRISVOX_WS_ADDR=127.0.0.1:28090 \
      BRISVOX_HEALTH_ADDR=127.0.0.1:28091 BRISVOX_MODELS_DIR="$BRISVOX/models" RUST_LOG=info
    if [[ $which == moss-nocache ]]; then  # reply cache off through a loopback proxy (nocache_proxy.py)
      py -3.13 "$HERE/nocache_proxy.py" 28095 "$which-$n" & proxy=$!
      sleep 3
    fi
    "$BIN/brisvox-server.exe" >"$log" 2>&1 &
    pid=$!
    for _ in $(seq 60); do curl -sf http://127.0.0.1:28091/readyz >/dev/null 2>&1 && break; sleep 1; done
    "$BIN/brisvox-bench.exe" v2v --url ws://127.0.0.1:28090 --fixtures en_two_utterances --runs 10 \
      --concurrency 1 --min-gap-ms 9000 --keep-invalid \
      --out-md "$OUT/v2v-$which-$n.md" --out-json "$OUT/v2v-$which-$n.json" 2>&1 | tail -5
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    if [[ -n ${proxy:-} ]]; then kill "$proxy" 2>/dev/null || true; fi
  )
}

# Blocks as arguments ("moss-nocache:1 openrouter:3 moss-nocache:2"); default is the first A/B.
blocks=("$@")
[[ ${#blocks[@]} -gt 0 ]] || blocks=(moss:1 openrouter:1 moss:2 openrouter:2)
for b in "${blocks[@]}"; do run_block "${b%%:*}" "${b##*:}"; done
echo "done"

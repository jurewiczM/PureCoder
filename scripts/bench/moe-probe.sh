#!/bin/bash
# Find a working MoE split for a 30B-A3B on 6 GB: attention on GPU, experts in RAM.
BENCH=${BENCH:-$HOME/models/bench}
M=${M:-$HOME/models/Qwen3-Coder-30B-A3B-Instruct-Q3_K_M.gguf}
B=$BENCH
cd "$(dirname "$0")/../.."
pkill -x llama-server 2>/dev/null; sleep 4     # nothing else may hold 8080
probe () {
  setsid nohup ./llama.cpp/build/bin/llama-server -m $M $1 --port 8080 --host 127.0.0.1 \
    > $B/moe.log 2>&1 < /dev/null &
  disown
  for i in $(seq 1 120); do sleep 2; curl -s -m 2 http://localhost:8080/health 2>/dev/null | grep -q '"ok"' && break; done
  if curl -s -m 2 http://localhost:8080/health 2>/dev/null | grep -q '"ok"'; then
    tps=$(curl -s -m 600 http://localhost:8080/completion \
      -d '{"prompt":"write a python function that sorts a list:","n_predict":80}' \
      | .venv/bin/python -c "import json,sys; print(f\"{json.load(sys.stdin)['timings']['predicted_per_second']:.1f}\")" 2>/dev/null)
    echo "$1 -> VRAM $(nvidia-smi --query-gpu=memory.used --format=csv,noheader) | RAM $(free -g | awk 'NR==2{print $3}')G | ${tps} tok/s"
  else
    echo "$1 -> FAILED: $(grep -iE 'error|out of memory|failed|cannot' $B/moe.log | head -1 | cut -c1-90)"
  fi
  cp $B/moe.log "$B/moe-$(echo $1 | tr -dc '0-9a-z').log"
  pkill -x llama-server; sleep 5
}
probe "-ngl 99 --cpu-moe -c 16384 -fa on -ctk q8_0 -ctv q8_0"
probe "-ngl 99 -ncmoe 36 -c 16384 -fa on -ctk q8_0 -ctv q8_0"
probe "-ngl 99 -ncmoe 30 -c 16384 -fa on -ctk q8_0 -ctv q8_0"

#!/bin/sh
# Starts the offline install assistant. Run as root inside the Arch live environment.
set -e
D=/aidata
mountpoint -q "$D" || { mkdir -p "$D"; mount -t exfat -L AIDATA "$D"; }
tar xzf "$D/llama-b11056-bin-ubuntu-x64.tar.gz" -C /tmp
exec python "$D/interview.py" --model "$D/gemma-4-E4B-it-Q4_K_M.gguf" --llama-dir /tmp/llama-b11056 --threads "$(nproc)"

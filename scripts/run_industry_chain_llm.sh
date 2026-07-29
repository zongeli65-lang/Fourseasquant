#!/bin/zsh

set -euo pipefail

readonly OLLAMA_BIN="/opt/homebrew/bin/ollama"

if [[ ! -x "${OLLAMA_BIN}" ]]; then
  print -u2 "未找到 Ollama：${OLLAMA_BIN}"
  print -u2 "请先按 docs/LOCAL_LLM_INSTALL_PLAN_v0.1.md 完成安装。"
  exit 1
fi

# 产业链候选筛选基线：仅本机、禁用云端模型、单模型、单请求、8K 上下文。
export OLLAMA_HOST="127.0.0.1:11434"
export OLLAMA_NO_CLOUD="true"
export OLLAMA_NUM_PARALLEL="1"
export OLLAMA_MAX_LOADED_MODELS="1"
export OLLAMA_CONTEXT_LENGTH="8192"
export OLLAMA_KV_CACHE_TYPE="f16"
export OLLAMA_KEEP_ALIVE="5m"

exec "${OLLAMA_BIN}" serve

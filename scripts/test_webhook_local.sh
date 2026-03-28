#!/usr/bin/env bash
# 本地验证 Webhook 入口是否可达（不经过 Gitee 云端）。
# 用法：
#   1) 终端 A：export GITEE_TOKEN=... DASHSCOPE_API_KEY=... （可选 GITEE_WEBHOOK_SECRET=...）
#      uvicorn app.main:app --host 127.0.0.1 --port 8000
#   2) 终端 B：./scripts/test_webhook_local.sh
#      若设置了 GITEE_WEBHOOK_SECRET，请同时 export，脚本会把其放入 X-Gitee-Token（明文密码方式）。
#
# 默认 payload 里是示例 PR；要跑通整条链路，把下面 JSON 里的 html_url 改成你仓库里真实、已打开的 PR。

set -euo pipefail
BASE_URL="${1:-http://127.0.0.1:8000}"
TOKEN_HEADER=()
if [[ -n "${GITEE_WEBHOOK_SECRET:-}" ]]; then
  TOKEN_HEADER=(-H "X-Gitee-Token: ${GITEE_WEBHOOK_SECRET}")
fi

curl -sS -X POST "${BASE_URL}/api/gitee/webhook" \
  -H "Content-Type: application/json" \
  "${TOKEN_HEADER[@]}" \
  -d '{
  "hook_name": "merge_request_hooks",
  "pull_request": {
    "state": "open",
    "html_url": "https://gitee.com/你的空间/你的仓库/pulls/1",
    "number": 1
  },
  "repository": {
    "path_with_namespace": "你的空间/你的仓库"
  }
}'
echo

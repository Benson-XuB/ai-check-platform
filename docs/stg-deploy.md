# Staging 部署：配置即用（含 Gitee Webhook）

部署到 stg 后**无需改代码**，完成下面三块即可：运行服务、配环境变量、在 Gitee 配 WebHook。

---

## 1. 运行服务

与现有 `Dockerfile` 一致：进程监听 `0.0.0.0:8000`。

```bash
# 方式 A：Compose（推荐，自动读根目录 .env）
docker compose up --build -d

# 方式 B：纯 docker run
docker build -t ai-pr-review .
docker run -d -p 8000:8000 --env-file .env ai-pr-review
```

若在 k8s / 云托管：把下面变量写进 Secret/环境配置，Ingress 或负载均衡把 **HTTPS** 指到该服务的 `8000`（或由 Sidecar 终止 TLS 再转发到 8000）。

仓库根目录有 `.env.example`，可将同名键录入平台（不要把真实密钥提交到 git）。

---

## 2. 环境变量（最小集合）

| 变量 | 是否必填 | 说明 |
|------|----------|------|
| `GITEE_TOKEN` | Webhook **必填** | Gitee 私人令牌 |
| `GITEE_WEBHOOK_SECRET` | **stg/生产强烈建议必填** | 与 Gitee WebHook 里配置的密钥一致；为空时服务端不验签（仅适合本地试错） |
| `KIMI_API_KEY`（或 `MOONSHOT_API_KEY`） | Webhook **必填**（默认走 Kimi） | 审查用 API Key |
| `DASHSCOPE_API_KEY` | 当 `WEBHOOK_LLM_PROVIDER=dashscope` 时 **必填** | 通义 Key |

更多可选键见 `.env.example`（ enrich、自动发帖开关、pass 次数、Kimi 等）。

可选：`DATABASE_URL` — 仅在使用 RAG / Symbol Graph 时需要，与 Webhook 能否工作无强依赖。

---

## 3. Gitee WebHook（创建 PR 自动触发）

1. 打开 **仓库 → 管理 → WebHooks**（名称以 Gitee 界面为准）。
2. **URL**：  
   `https://<你的 stg 域名>/api/gitee/webhook`  
   无路径前缀时即为根域名后直接接 `/api/gitee/webhook`。若 stg 带统一前缀（如 `https://stg.example.com/prreview`），需让网关仍把 **`/api/gitee/webhook` 转到本应用**，或自行增加反向代理规则（本仓库默认无全局 `root_path`）。
3. **密钥**：与 `GITEE_WEBHOOK_SECRET` **完全一致**（支持 Gitee 文档中的明文密码或签名密钥方式，与服务端校验逻辑一致）。
4. **事件**：勾选 **Pull Request / 合并请求**。若只希望「新建 MR」触发而不要在每次 push 重审，在 Gitee 里只选 **打开/创建** 类事件（具体选项以 Gitee 当前版本为准）。

保存后可用「测试」或新建一条测试 MR 验证。

---

## 4. 验收

| 步骤 | 预期 |
|------|------|
| 浏览器打开 `https://<stg>/` | 能打开审查页（与当前行为一致） |
| 在 Gitee 新建 MR | stg 服务日志出现对 `POST /api/gitee/webhook` 的请求；随后 PR 上出现 AI 评论（默认 `WEBHOOK_AUTO_POST_COMMENTS=true`） |

审查在后台执行，HTTP 会快速返回；若长时间无评论，查日志里 `Gitee` / `审查` 相关错误（Token、Key、权限、超时）。

---

## 5. 常见说明

- **不需要**为 stg 单独改仓库代码；若 stg 必须挂在子路径且无法改网关，再考虑给 FastAPI 配 `root_path` 或前缀路由（属少数情况）。
- 前端仍可在浏览器里填 Token/Key；**Webhook 只吃服务端环境变量**，与是否有人打开网页无关。
- 飞书登录等如后续接入，仍是独立配置，与 Webhook 并行。

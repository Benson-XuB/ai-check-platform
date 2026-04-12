# AI PR Review

Gitee PR 上下文感知代码审查工具。聚焦 CI 做不到的事：逻辑 bug、设计问题、可读性、边界情况、业务语义。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --reload --port 8000
```

访问 http://127.0.0.1:8000（首页为 **Gitee / GitHub 登录入口**；自备 Key 与 PR 链接的表单在 `/manual`）

**公网部署**：务必 **HTTPS**；建议设 `PRELAUNCH_TRUST_X_FORWARDED_FOR=1`（或 `PUBLIC_TRUST_X_FORWARDED_FOR`）以便在反代后按真实 IP 限流。限流项见 `.env.example`（PR 拉取、LLM 审查、评论、RAG、Prelaunch 各自独立计数）。`HTTP_MAX_BODY_MB` 限制大 JSON（如超大 diff）；ZIP 上限为 `PRELAUNCH_MAX_REPO_MB`。

## 功能

- **拉取 PR（多平台）**：选择 **Gitee** 或 **GitHub**，填写对应 Token 与 PR 链接；API 为 `POST /api/vcs/fetch-pr`（`platform` + `vcs_token`，兼容旧字段 `gitee_token`）。`POST /api/gitee/fetch-pr` 仍可用。
- **AI 审查**：默认 **Kimi**，可切换通义千问；基于上下文进行审查（多 pass 智能审查仅通义）
- **结构化输出**：severity (Critical/Important/Minor) + category
- **行级评论**：可将审查结果精准发送到 PR 对应行
- **Gitee 登录与自动报告**：见下文「Gitee SaaS」；合并请求 WebHook 触发后只写入站内报告，**默认不向 PR 发评论**，LLM 使用服务端配置的 Key。

## Gitee SaaS（OAuth + 自动审查报告）

- 入口：<http://127.0.0.1:8000/app>
- 流程：**Gitee OAuth 登录** → 点击 **同步 WebHook**（对当前令牌下有权限的仓库调用 Gitee API 注册合并请求 Hook）→ 新建/更新合并请求时自动拉取并审查 → 结果保存在 **`pr_review_reports` 表**，在 `/app` 查看；**不写回 Gitee 评论**。
- 审查使用的 LLM 与密钥：与全站一致，读环境变量 **`DASHSCOPE_API_KEY`** 或 **`KIMI_API_KEY`**（由 `PUBLIC_DEFAULT_LLM_PROVIDER` 决定），用户无需自带 Key。
- **必须**配置 **`DATABASE_URL`**（与 RAG / Symbol Graph 共用同一库即可）；并创建 Gitee 第三方应用（回调 URL 与下方 `GITEE_OAUTH_REDIRECT_URI` 完全一致）。

| 环境变量 | 说明 |
|----------|------|
| `GITEE_OAUTH_CLIENT_ID` / `GITEE_OAUTH_CLIENT_SECRET` | Gitee OAuth 应用凭据 |
| `GITEE_OAUTH_REDIRECT_URI` | 回调地址，如 `http://127.0.0.1:8000/auth/gitee/callback` |
| `GITEE_OAUTH_SCOPES` | 可选；默认 `user_info pull_requests projects hook`（`hook` 用于 API 注册 WebHook） |
| `PUBLIC_BASE_URL` | 站点根 URL，用于拼接 WebHook 地址 |
| `GITEE_WEBHOOK_SECRET` | 与 Gitee WebHook「密码」一致，用于 `X-Gitee-Token` 校验 |
| `SESSION_SECRET` | 会话 Cookie 签名（生产请使用强随机值） |
| `SESSION_HTTPS_ONLY` | 生产环境 HTTPS 下设为 `1` |

可选（默认偏省资源）：`SAAS_WEBHOOK_ENRICH_CONTEXT`、`SAAS_WEBHOOK_USE_SYMBOL_GRAPH`、`SAAS_WEBHOOK_USE_TREESITTER`、`SAAS_WEBHOOK_USE_PYRIGHT`、`SAAS_WEBHOOK_USE_SEMANTIC_CONTEXT`、`SAAS_WEBHOOK_USE_DEFAULT_REVIEW`、`SAAS_WEBHOOK_DEFAULT_PASSES`。

## 配置

- **Gitee Token**：Gitee 设置 → 私人令牌
- **Kimi（默认）**：月之暗面开放平台
- **通义千问**：阿里云百炼控制台

## 部署（Docker）

**镜像内已自动 `pip install`，无需进容器再装依赖。**

```bash
# 仅 Docker：构建并运行（单体入口：SaaS + Prelaunch 都在一个进程里）
docker build -t ai-pr-review .
docker run -p 8000:8000 --env-file .env ai-pr-review
# 无 .env 时可省略 --env-file（仅打开页面；审查 / Webhook 需密钥）

# 推荐：Compose 一键（自动读项目根目录 .env）
cp .env.example .env   # 按需填写
docker compose up --build -d
```

访问 http://localhost:8000（若改了端口则使用 `HOST_PORT`）。多用户可同时使用同一实例，凭证由前端输入或 localStorage 保存，Webhook 等用服务端环境变量。

**Stg / 生产启用「PR 创建自动审查」**：只要部署可用 HTTPS，配置环境变量 + Gitee WebHook 即可，无需改代码。参见 [docs/stg-deploy.md](docs/stg-deploy.md)，环境变量模板见仓库根目录 [.env.example](.env.example)。

## 拆分部署（同 repo 两个 service）

为避免 Prelaunch 与 SaaS 的依赖/部署互相影响，仓库额外提供两个独立入口：

- **SaaS 服务**：`uvicorn apps.saas_api.main:app`
- **Prelaunch 服务**：`uvicorn apps.prelaunch_api.main:app`

对应 Dockerfile：

- `Dockerfile.saas`：不包含 CLI 扫描器
- `Dockerfile.prelaunch`：包含 gitleaks/trivy + PDF 依赖

Railway 示例配置文件：

- `railway.saas.json`（指向 `Dockerfile.saas`）
- `railway.prelaunch.json`（指向 `Dockerfile.prelaunch`）

## 产品收敛：Go / No-Go 规格

上线前「能不能发、必须先改什么」的 MVP 定义见 **[docs/plans/go-nogo-mvp-spec.md](docs/plans/go-nogo-mvp-spec.md)**（与下方 Prelaunch 实现对齐中）。

## Prelaunch：上线前整仓扫描（工程实现）

- 页面：<http://127.0.0.1:8000/prelaunch>（或你的部署域名 `/prelaunch`）
- 健康检查：`GET /api/prelaunch/health`（本机是否已装 gitleaks / semgrep / bandit / npm / trivy）
- 设计：[docs/plans/2026-03-28-prelaunch-scan-design.md](docs/plans/2026-03-28-prelaunch-scan-design.md)
- 实现计划：[docs/plans/2026-03-28-prelaunch-scan-implementation-plan.md](docs/plans/2026-03-28-prelaunch-scan-implementation-plan.md)
- 运维：[docs/prelaunch-operators.md](docs/prelaunch-operators.md)

默认工作目录：`.prelaunch_workspace/`（可用 `PRELAUNCH_WORKSPACE` 覆盖）。启动时会按 `PRELAUNCH_JOB_TTL_HOURS` 清理过期任务目录。PDF 依赖 **WeasyPrint**（Dockerfile 已装依赖库）；未生成 PDF 时可用 HTML 打印。

与 Snyk 等 SaaS 的关系见 [docs/prelaunch-vs-snyk.md](docs/prelaunch-vs-snyk.md)。**Docker 镜像**预装 gitleaks、trivy、Node/npm，Python 包内含 semgrep/bandit/pip-audit（构建目标 **linux/amd64**）。

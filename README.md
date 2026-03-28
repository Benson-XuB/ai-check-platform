# AI PR Review

Gitee PR 上下文感知代码审查工具。聚焦 CI 做不到的事：逻辑 bug、设计问题、可读性、边界情况、业务语义。

## 快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn app.main:app --reload --port 8000
```

访问 http://127.0.0.1:8000

**公网部署**：务必 **HTTPS**；建议设 `PRELAUNCH_TRUST_X_FORWARDED_FOR=1`（或 `PUBLIC_TRUST_X_FORWARDED_FOR`）以便在反代后按真实 IP 限流。限流项见 `.env.example`（PR 拉取、LLM 审查、评论、RAG、Prelaunch 各自独立计数）。`HTTP_MAX_BODY_MB` 限制大 JSON（如超大 diff）；ZIP 上限为 `PRELAUNCH_MAX_REPO_MB`。

## 功能

- **拉取 PR（多平台）**：选择 **Gitee** 或 **GitHub**，填写对应 Token 与 PR 链接；API 为 `POST /api/vcs/fetch-pr`（`platform` + `vcs_token`，兼容旧字段 `gitee_token`）。`POST /api/gitee/fetch-pr` 仍可用。
- **AI 审查**：默认 **Kimi**，可切换通义千问；基于上下文进行审查（多 pass 智能审查仅通义）
- **结构化输出**：severity (Critical/Important/Minor) + category
- **行级评论**：可将审查结果精准发送到 PR 对应行

## 配置

- **Gitee Token**：Gitee 设置 → 私人令牌
- **Kimi（默认）**：月之暗面开放平台
- **通义千问**：阿里云百炼控制台

## 部署（Docker）

**镜像内已自动 `pip install`，无需进容器再装依赖。**

```bash
# 仅 Docker：构建并运行
docker build -t ai-pr-review .
docker run -p 8000:8000 --env-file .env ai-pr-review
# 无 .env 时可省略 --env-file（仅打开页面；审查 / Webhook 需密钥）

# 推荐：Compose 一键（自动读项目根目录 .env）
cp .env.example .env   # 按需填写
docker compose up --build -d
```

访问 http://localhost:8000（若改了端口则使用 `HOST_PORT`）。多用户可同时使用同一实例，凭证由前端输入或 localStorage 保存，Webhook 等用服务端环境变量。

**Stg / 生产启用「PR 创建自动审查」**：只要部署可用 HTTPS，配置环境变量 + Gitee WebHook 即可，无需改代码。参见 [docs/stg-deploy.md](docs/stg-deploy.md)，环境变量模板见仓库根目录 [.env.example](.env.example)。

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

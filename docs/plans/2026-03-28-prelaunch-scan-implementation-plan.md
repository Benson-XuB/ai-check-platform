# Prelaunch Scan（上线前扫描）Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在同一仓库内新增「Git 克隆 → 多工具扫描 → 归一化 → LLM 增强 → Web 报告 + PDF」的 MVP，服务一人公司/小团队上线前自查（设计依据见 [2026-03-28-prelaunch-scan-design.md](./2026-03-28-prelaunch-scan-design.md)）。

**Architecture:** 在现有 FastAPI 应用中增加独立路由前缀 `app/routers/prelaunch.py` + 服务层 `app/services/prelaunch/*`；任务异步使用 `BackgroundTasks` + 进程内 `asyncio.create_task` 或 `run_in_executor` 跑子进程（MVP 无 Redis）。扫描器以 **子进程** 调用 `git`、`gitleaks`、`semgrep`、`bandit`、`npm`、`trivy`（宿主机或镜像内安装）。结果 JSON 落盘于 `PRELAUNCH_WORKSPACE/<job_id>/`，PDF 由 **Jinja2 HTML 模板 + WeasyPrint**（或 Playwright，实现时二选一）生成。

**Tech Stack:** FastAPI、Pydantic v2、Jinja2、子进程、现有 `review` 服务的 LLM 调用模式复用（Kimi / DashScope）；可选 `httpx` 已存在。

**约束:** MVP 单实例；多副本部署需后续换 Redis 队列 + 共享存储。免责声明文案出现在 Web/PDF 页脚。

---

### Task 1: 配置与工作区约定

**Files:**
- Create: `app/services/prelaunch/__init__.py`
- Create: `app/services/prelaunch/config.py`（读 `PRELAUNCH_WORKSPACE`、`PRELAUNCH_JOB_TTL_HOURS`、`PRELAUNCH_MAX_REPO_MB` 等）
- Modify: `.env.example`（追加上述变量说明）
- Modify: `docs/stg-deploy.md` 或新建 `docs/prelaunch-operators.md`（运维需安装的二进制列表）

**Step 1:** 在 `config.py` 中实现 `get_workspace_root() -> Path`，若目录不存在则 `mkdir(parents=True)`，默认 `./.prelaunch_workspace`（仅开发，生产用 env 指绝对路径）。

**Step 2:** 定义常量列表 `REQUIRED_SCANNER_HINTS`：文档字符串列出 `gitleaks`、`semgrep`、`bandit`、`npm/node`、`trivy` 最低版本检测方式（`which` + `--version`），供健康检查用。

**Step 3:** 在 `app/main.py` 增加可选 `GET /api/prelaunch/health` 或在现有根路由文档中链接，返回各 CLI 是否可用（避免启动失败，仅报告 `available: false`）。

**Step 4:** Commit: `chore(prelaunch): workspace config and operator hints`

---

### Task 2: 数据模型与 Job 存储

**Files:**
- Create: `app/services/prelaunch/schemas.py`（`JobStatus` enum、`ScanFinding`、`NormalizedFinding`、`PrelaunchJobRecord`）
- Create: `app/services/prelaunch/store.py`（`create_job`、`get_job`、`update_job`、`append_log`；MVP 用 **单文件 JSON** `job.json` 每任务目录一份，加 `filelock` 或单进程假设写清）

**Schema 要点:**
- `NormalizedFinding`: `id`, `severity` (Critical|High|Medium|Low|Info), `category` (secret|dependency|sast|…), `title`, `file`, `line`, `snippet`, `sources: list[str]`（工具名）, `raw_refs: dict`
- `PrelaunchJobRecord`: `job_id`, `status`, `repo_url_redacted`, `ref`, `created_at`, `updated_at`, `error`, `paths: {raw_gitleaks, raw_semgrep, …}`, `normalized_path`, `llm_enhanced_path`, `pdf_path`

**Step 1:** 单元测试 `tests/test_prelaunch_store.py`：创建 job → 更新状态 → 读取一致。

**Step 2:** Commit: `feat(prelaunch): job record and file store`

---

### Task 3: Git 克隆

**Files:**
- Create: `app/services/prelaunch/git_clone.py`
- Test: `tests/test_prelaunch_git_clone.py`（mock `subprocess.run`，不测真实网络）

**实现:**
- 函数 `clone_repo(url: str, token: str | None, ref: str | None, dest: Path) -> None`
- URL 注入 token：HTTPS `https://oauth2:TOKEN@host/...` 或 Gitee/GitHub 文档推荐方式；**禁止**把带 token 的 URL 写入 `job.json`，仅存 `host/path`。
- `git clone --depth 1 --branch <ref>`；若 ref 缺省用默认分支。
- 超时 `subprocess` `timeout=600`；失败抛带 stderr 的异常。

**Step 1:** 写失败路径测试（非 0 退出码）。

**Step 2:** Commit: `feat(prelaunch): git clone with timeout`

---

### Task 4: 项目探测（语言/包管理器）

**Files:**
- Create: `app/services/prelaunch/detect.py`
- Test: `tests/test_prelaunch_detect.py`（临时目录造 `package.json`、`pom.xml`、`requirements.txt`）

**输出:** `ProjectProfile`: `has_python`, `has_node`, `has_java`, `package_managers: list`, `lockfiles: list`

**Step 1:** 实现基于路径存在性的探测，忽略 `node_modules` 深度（仅根与常见子包可选 YAGNI）。

**Step 2:** Commit: `feat(prelaunch): project profile detection`

---

### Task 5: 扫描器封装 — gitleaks + semgrep

**Files:**
- Create: `app/services/prelaunch/runners/gitleaks.py`
- Create: `app/services/prelaunch/runners/semgrep.py`
- Create: `app/services/prelaunch/runners/base.py`（`run_cmd(args, cwd, timeout)` 统一日志）

**实现:**
- `gitleaks detect --no-git -s <repo> -f json -r <out.json>`（或官方推荐 no-git 目录扫描）
- `semgrep scan --config auto --json -o <out.json> <repo>`（MVP `--config auto`；后续可换 `p/security-audit` 等）

**Step 1:** 测试 mock subprocess 返回样例 JSON 文件路径。

**Step 2:** Commit: `feat(prelaunch): gitleaks and semgrep runners`

---

### Task 6: 扫描器封装 — bandit + npm audit + trivy fs

**Files:**
- Create: `app/services/prelaunch/runners/bandit.py`（`profile.has_python` 为真时在 repo 根跑 `bandit -r . -f json -o ...`，exclude 大目录）
- Create: `app/services/prelaunch/runners/npm_audit.py`（检测 `package-lock.json` → `npm audit --json`；`pnpm-lock.yaml` → `pnpm audit --json` 若可用）
- Create: `app/services/prelaunch/runners/trivy.py`（存在 `pom.xml`/`build.gradle` 时 `trivy fs --format json -o ... .`）

**Step 1:** 各 runner 在 CLI 缺失时返回结构化 `{ "skipped": true, "reason": "..." }` 写入 raw 文件，不使整 job 失败。

**Step 2:** Commit: `feat(prelaunch): bandit npm trivy runners`

---

### Task 7: 解析器与归一化

**Files:**
- Create: `app/services/prelaunch/parsers/`（`gitleaks.py`, `semgrep.py`, `bandit.py`, `npm.py`, `trivy.py`）— 每文件一个 `parse_*(raw: dict) -> list[NormalizedFinding]`
- Create: `app/services/prelaunch/normalize.py`（合并、按 `file+line+rule_id` 去重、`sources` 合并）

**严重级别映射:** 各解析器内建映射表；未知映射为 `Medium`。

**Step 1:** fixture JSON（可从各工具文档截短样例）做快照测试。

**Step 2:** Commit: `feat(prelaunch): parse and normalize findings`

---

### Task 8: LLM 增强层

**Files:**
- Create: `app/services/prelaunch/llm_report.py`
- 复用: `app/services/review.py` 中 `_call_dashscope` / Kimi HTTP 模式（抽 **薄封装** `app/services/llm_client.py` 若尚不存在；YAGNI 则直接在 `llm_report.py` 内 httpx 调用与 `call_kimi` 对齐）

**输入:** `normalized_findings` JSON + `profile` + 目录树浅表（`find` 深度 2 或 `os.walk` 截断）  
**输出:** Pydantic 模型 `LlmReport`: `executive_summary`, `top_risks: list`, `finding_notes: dict[id, {explanation, fix, false_positive_hint}]`, `architecture_section: str`, `compliance_checklist: list[{item, done: bool|null}]`

**Step 1:** Prompt 中强制 JSON schema；解析失败重试一次，再失败则 `architecture_section` 与 `finding_notes` 为空并记日志。

**Step 2:** Commit: `feat(prelaunch): llm report enrichment`

---

### Task 9: HTTP API

**Files:**
- Create: `app/routers/prelaunch.py`
- Modify: `app/main.py`（`include_router(prelaunch.router)`）

**端点:**
- `POST /api/prelaunch/jobs` body: `{ "repo_url", "git_token"?, "ref"?, "llm_provider"?, "llm_api_key"? }` → 返回 `{ "job_id" }`
- `GET /api/prelaunch/jobs/{job_id}` → 状态 + 完成时 `report` 摘要或嵌套 key
- `GET /api/prelaunch/jobs/{job_id}/report` → `text/html` 或前端用 JSON；MVP 返回 **渲染好的 HTML** 字符串也可
- `GET /api/prelaunch/jobs/{job_id}/report.pdf` → `application/pdf`（未完成 404）

**异步:** `BackgroundTasks.add_task(run_prelaunch_pipeline, job_id)`，`run_prelaunch_pipeline` 内顺序：clone → runners → normalize → llm → render html → pdf。

**Step 1:** 集成测试用 mock pipeline（注入 fake job 目录）验证 404/200。

**Step 2:** Commit: `feat(prelaunch): REST API for jobs and reports`

---

### Task 10: HTML 模板与 PDF

**Files:**
- Create: `app/templates/prelaunch_report.html`（Jinja2）
- Create: `app/services/prelaunch/render.py`（`render_html(job, normalized, llm) -> str`）
- Create: `app/services/prelaunch/pdf.py`（`html_to_pdf(html: str, out: Path)`）

**PDF:** 首选 **WeasyPrint**（纯 Python 依赖，Linux 需系统库 `pango` 等；Dockerfile 增加 `apt`）。若镜像体积敏感，可改 **Playwright** `chromium` 打印 PDF（任务内注明二选一）。

**Step 1:** 页脚固定免责声明段落（设计稿 §8）。

**Step 2:** Commit: `feat(prelaunch): HTML report and PDF export`

---

### Task 11: 前端页面（MVP）

**Files:**
- Create: `static/prelaunch.html`（表单：repo URL、token、ref、LLM provider/key；轮询 `GET /jobs/{id}`；完成后链接「打开报告」「下载 PDF」）
- Modify: `README.md` 增加 Prelaunch 小节与 `/static/prelaunch.html` 或路由 `GET /prelaunch` 返回该页

**可选:** `GET /prelaunch` 在 `main.py` 用 `FileResponse` 挂载，避免用户记静态路径。

**Step 1:** 手动浏览器点通（文档写验收步骤）。

**Step 2:** Commit: `feat(prelaunch): minimal web UI`

---

### Task 12: Docker 与依赖

**Files:**
- Modify: `Dockerfile` 或新增 `Dockerfile.prelaunch`（多阶段：基础 Python + `apt` 安装 `git`、WeasyPrint 依赖、gitleaks/semgrep/trivy 二进制从官方 release `ADD`）
- Modify: `requirements.txt`（`jinja2`、`weasyprint`、如需要 `filelock`）
- Modify: `docker-compose.yml`（`PRELAUNCH_WORKSPACE` volume mount）

**注意:** `npm` 需 `node` 镜像或 apt 安装 `nodejs`；镜像会变大，在 README 标注 **Slim 开发镜像** 与 **Full 扫描镜像** 可选。

**Step 1:** `docker build` 在 CI 或本地验证（若环境无 docker 则文档说明）。

**Step 2:** Commit: `chore(prelaunch): container deps for scanners and pdf`

---

### Task 13: 文档与法务提示

**Files:**
- Modify: `docs/plans/2026-03-28-prelaunch-scan-design.md`（状态改为「已有实现计划」链接本文件）
- Create: `docs/prelaunch-disclaimer.md`（中英文简短免责声明，供页面引用）

**Step 1:** Commit: `docs(prelaunch): disclaimer and operator guide`

---

## 测试与验收（整体验收）

1. 本地安装 `gitleaks`、`semgrep`、`bandit`、`trivy`、`node`+`npm`（或仅 Docker 全包）。  
2. `uvicorn app.main:app` → 打开 `/prelaunch`，提交 **公开测试仓库**（无 token）。  
3. 等待 `complete` → Web 报告含 findings + LLM 段 + 清单；PDF 下载大小 > 0。  
4. 检查工作区目录在 job 完成后可配置是否删除（MVP 可保留 24h 便于调试）。

---

## 执行方式（实现时选一种）

**Plan complete and saved to `docs/plans/2026-03-28-prelaunch-scan-implementation-plan.md`. Two execution options:**

1. **Subagent-Driven（本会话）** — 每任务派生子代理，任务间人工检查，迭代快。需使用 **subagent-driven-development** 技能。

2. **Parallel Session（新会话）** — 新开会话加载 **executing-plans** 技能，按任务批量执行并设检查点。

**Which approach?**（回复 `1` 或 `2`；若回复 `1`，将从 Task 1 开始实现。）

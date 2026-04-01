# Prelaunch Batch Jobs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 支持一次提交多个 GitHub repo URL，后端为每个 repo 创建一个独立 prelaunch job 并异步执行；同时修复 ZIP 提交路径的线程启动参数问题；提供一个本地脚本便于批量触发。

**Architecture:** 在现有 `POST /api/prelaunch/jobs` 保持不变的前提下，新增 `POST /api/prelaunch/jobs/batch`（输入 repo 列表，输出 job_id 列表）。批量接口内部循环调用既有 `start_job()`，每个 job 独立存储与状态。另修复 `start_job_from_zip()` 传参错误，避免 ZIP job 直接失败。

**Tech Stack:** FastAPI、Pydantic、现有 `app/services/prelaunch/*` 线程式 worker、pytest。

---

### Task 1: 批量创建 jobs 的 API Schema + 路由

**Files:**
- Modify: `app/routers/prelaunch.py`
- Test: `tests/test_prelaunch_batch_jobs.py` (new)

**Step 1: Write the failing test**

目标：调用 `POST /api/prelaunch/jobs/batch`，传入 2 个 repo，返回 `job_ids` 长度为 2，且每个 job record 可被 `store.load_record(job_id)` 读取。

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_prelaunch_batch_jobs.py`
Expected: 404 或路由不存在。

**Step 3: Write minimal implementation**

- 新增 Pydantic body：`repo_urls: list[str]` + 共享字段（`git_token?`, `ref?`, `llm_provider`, `llm_api_key`）
- 在 router 里循环调用 `start_job(...)`，收集 job_id
- 返回 `{ "ok": true, "job_ids": [...] }`

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_prelaunch_batch_jobs.py`
Expected: PASS

---

### Task 2: 修复 ZIP 提交 job 的线程启动参数

**Files:**
- Modify: `app/services/prelaunch/pipeline.py`
- Test: `tests/test_prelaunch_zip_job.py` (new)

**Step 1: Write the failing test**

目标：调用 `start_job_from_zip(...)` 时不会因为线程 target 参数错位导致异常（可通过 mock `threading.Thread` 捕获 args/kwargs，确保 `job_id` 与 `zip_path` 被正确传入）。

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest -q tests/test_prelaunch_zip_job.py`
Expected: FAIL（参数不匹配）。

**Step 3: Write minimal implementation**

- 修正 `threading.Thread(... args=...)`，确保调用 `run_prelaunch_pipeline(job_id, repo_url, git_token, ref, llm_provider, llm_api_key, zip_path=...)`
- `repo_url/git_token/ref` 用空值，pipeline 分支会走 `zip_path`。

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest -q tests/test_prelaunch_zip_job.py`
Expected: PASS

---

### Task 3: 本地批量触发脚本（不依赖前端）

**Files:**
- Create: `scripts/prelaunch_batch_submit.py`
- Modify: `scripts/README.md`（加一段用法）

**Step 1: Write a small script**

脚本功能：
- 读取 repo 列表（命令行参数或 `--file repos.txt`）
- 调用 `POST /api/prelaunch/jobs/batch`（默认 `http://127.0.0.1:8000`）
- 打印 `job_ids`，并可选轮询 `GET /api/prelaunch/jobs/{id}` 输出状态（`--watch`）

**Step 2: Manual run**

Run:
- `python3 scripts/prelaunch_batch_submit.py --base-url http://127.0.0.1:8000 --llm-api-key xxx https://github.com/org/a https://github.com/org/b`
Expected: 输出 job_ids。

---

### Task 4: 回归测试

**Files:**
- (none)

**Step 1: Run full tests**

Run: `python3 -m pytest -q`
Expected: PASS


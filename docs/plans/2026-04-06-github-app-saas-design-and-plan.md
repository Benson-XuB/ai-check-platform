# GitHub App SaaS (Checks + Approve-to-Post) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace Gitee SaaS automation with GitHub App installation + webhook-triggered reviews, and notify PRs via GitHub Checks; detailed PR comments are posted only after the user clicks “Agree” in the SaaS UI.

**Architecture:** GitHub App webhooks trigger a background review job. The job uses an installation access token to fetch PR diff/context via existing `vcs_dispatch` GitHub path, runs existing `run_review_core`, stores results in DB, and updates a GitHub Check Run with a link to the report. When the user clicks “Agree”, the server replays stored review comments back to GitHub via the same installation token.

**Tech Stack:** FastAPI, SQLAlchemy, GitHub App (JWT + installation tokens), GitHub REST API (checks + PR fetch/comment), existing review pipeline.

---

### Task 1: Add GitHub App installation persistence

**Files:**
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/storage/models.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/storage/init_db.py` (no change expected; relies on `create_all`)

**Step 1: Write failing tests**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_github_app_installation_flow.py`
- Test scenarios:
  - upsert installation row by `installation_id`
  - resolve `user_id` from `installation_id`

**Step 2: Run test to verify it fails**
- Run: `python -m pytest -q tests/test_github_app_installation_flow.py`
- Expected: FAIL (models not defined).

**Step 3: Minimal implementation**
- Add models:
  - `GitHubAppInstallation(user_id, installation_id, account_login, account_type, created_at, updated_at)`
  - `GitHubPrBinding(report_id, user_id, installation_id, owner, repo, pr_number, head_sha, check_run_id, posted_at)`

**Step 4: Run tests**
- Expected: PASS.

---

### Task 2: Implement GitHub App auth + installation callback

**Files:**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/app/routers/saas_github.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/main.py`

**Step 1: Write failing tests**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_saas_github_install_callback.py`
- Test scenarios:
  - callback with missing `installation_id` → 400
  - callback creates `AppUser` if not present and stores in session
  - callback persists installation mapping

**Step 2: Run tests to verify it fails**
- Run: `python -m pytest -q tests/test_saas_github_install_callback.py`
- Expected: FAIL (router missing).

**Step 3: Minimal implementation**
- Routes:
  - `GET /auth/github/install` → redirect to GitHub App installation URL (from env `GITHUB_APP_SLUG` or explicit URL)
  - `GET /auth/github/callback` → validate `state` from session, bind installation to `AppUser`, redirect to `/app`

**Step 4: Run tests**
- Expected: PASS.

---

### Task 3: Add GitHub App token + webhook signature verification utilities

**Files:**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/app/services/github_app.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/requirements.txt`

**Step 1: Write failing tests**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_github_app_crypto.py`
- Test scenarios:
  - signature verifier accepts a known payload/signature pair (HMAC-SHA256)
  - private key normalization (supports `\n` escaped env)

**Step 2: Run tests**
- Run: `python -m pytest -q tests/test_github_app_crypto.py`
- Expected: FAIL.

**Step 3: Minimal implementation**
- Add dependency: `PyJWT[crypto]` (and `cryptography` if needed).
- Implement:
  - `verify_github_webhook(headers, body, secret)` for `X-Hub-Signature-256`
  - `get_installation_token(installation_id)` using App JWT to call `POST /app/installations/{id}/access_tokens`

**Step 4: Run tests**
- Expected: PASS.

---

### Task 4: Webhook-triggered review + Check Run notifications

**Files:**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/app/routers/github_webhook.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/main.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/services/github_pr.py` (only if needed for check runs)
- Create/Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/services/github_checks.py`

**Step 1: Write failing tests**
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_github_webhook_review_and_checkrun.py`
- Test scenarios:
  - invalid signature → 401
  - valid `pull_request` event enqueues background task
  - background task stores `PrReviewReport` and `GitHubPrBinding`
  - check run created in-progress then updated completed (mock requests)

**Step 2: Run tests**
- Run: `python -m pytest -q tests/test_github_webhook_review_and_checkrun.py`
- Expected: FAIL.

**Step 3: Minimal implementation**
- `POST /api/github/webhook`
  - verify signature
  - accept only `pull_request` + action in `{opened,synchronize,reopened}`
  - extract `installation.id`, `repository.full_name`, PR number and `pull_request.head.sha`
  - resolve `user_id` via `GitHubAppInstallation`
  - enqueue `process_saas_github_pull_request_webhook(...)`
- `process_saas_github_pull_request_webhook`
  - fetch installation token
  - create check run (status=in_progress, details_url=report URL placeholder)
  - `run_fetch_pr(FetchPRRequest(platform="github", pr_url=...))`
  - `run_review_core(ReviewRequest(... platform llm key ...))`
  - save report + binding + update check run completed with a short summary and `details_url` to report

**Step 4: Run tests**
- Expected: PASS.

---

### Task 5: “Agree” endpoint: post stored comments back to GitHub

**Files:**
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/routers/saas_github.py`
- Create: `/Users/xubaotian/Desktop/web/ai-check-platform/app/services/github_postback.py`
- Test: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_github_agree_postback.py`

**Step 1: Write failing tests**
- agree requires session user owns report
- agree posts a single PR comment if line-level comment fails (mock)
- marks binding as posted and returns ok

**Step 2: Run tests**
- Run: `python -m pytest -q tests/test_github_agree_postback.py`
- Expected: FAIL.

**Step 3: Minimal implementation**
- `POST /api/saas/github/reports/{report_id}/agree`
  - load report + binding
  - parse `result_json` into comment list
  - get installation token
  - post comments via `vcs_dispatch.post_comment(platform="github", ...)` using `head_sha` for `commit_id`
  - update binding `posted_at`
  - optionally update check run output to “Posted”

**Step 4: Run tests**
- Expected: PASS.

---

### Task 6: Disable / hide Gitee SaaS in production

**Files:**
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/routers/saas_gitee.py`
- Modify: `/Users/xubaotian/Desktop/web/ai-check-platform/app/routers/gitee_webhook.py`

**Step 1: Implement gating**
- Add env `SAAS_ENABLE_GITEE` default false in production (true locally).
- Return 404 or 503 for Gitee SaaS endpoints when disabled.

**Step 2: Tests**
- Add: `/Users/xubaotian/Desktop/web/ai-check-platform/tests/test_gitee_saas_gating.py`

---

### Manual test checklist (staging/prod)

- Configure env:
  - `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`
  - `PUBLIC_BASE_URL`, `DATABASE_URL`, `SESSION_SECRET`
  - `DASHSCOPE_API_KEY` (or `KIMI_API_KEY`)
- Install app on a test repo and open a PR.
- Confirm PR shows a Check Run:
  - transitions in_progress → completed
  - details URL links to report in SaaS
- Open SaaS report list, open report detail.
- Click Agree → confirm PR receives comments; binding marked posted.


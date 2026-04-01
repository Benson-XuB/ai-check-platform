# 脚本说明

## test_review_local.py（推荐先跑）

基于本地 resumehub 测试 Review，**无需真实 PR**。使用内含故意 bug 的 `candidates_risky.py`。

```bash
export DASHSCOPE_API_KEY="你的通义千问 Key"
python scripts/test_review_local.py
```

预期能 catch：SQL 注入、硬编码密钥、边界检查、错误泄露、幻觉 API。

---

## prelaunch_batch_submit.py

批量提交多个仓库做 Prelaunch 扫描（不依赖前端页面）。

### 1. 启动服务

```bash
uvicorn app.main:app --port 8000
```

### 2. 运行

```bash
export DASHSCOPE_API_KEY="你的通义千问 Key"

# 直接传多个 repo url
python scripts/prelaunch_batch_submit.py --llm-api-key "$DASHSCOPE_API_KEY" \
  https://github.com/org/repo1 \
  https://github.com/org/repo2

# 从文件读取（每行一个 repo url，可用 # 注释）
python scripts/prelaunch_batch_submit.py --llm-api-key "$DASHSCOPE_API_KEY" --file repos.txt

# 可选：轮询查看状态
python scripts/prelaunch_batch_submit.py --llm-api-key "$DASHSCOPE_API_KEY" --watch --file repos.txt
```

---

## run_review_and_report.py

运行 PR 审查并生成报告 + Catch 比率标注模板。

### 1. 设置 Token（二选一）

**方式 A：环境变量（推荐）**
```bash
export GITEE_TOKEN="你的 Gitee 私人令牌"
export DASHSCOPE_API_KEY="你的通义千问 API Key"
```

**方式 B：命令行参数**
```bash
python scripts/run_review_and_report.py https://gitee.com/owner/repo/pulls/123 \
  --gitee-token "xxx" --llm-key "sk-xxx"
```

### 2. 运行

```bash
# 基础（4 维度审查，直接调用服务）
python scripts/run_review_and_report.py https://gitee.com/owner/repo/pulls/123

# 含上下文增强 + Pyright
python scripts/run_review_and_report.py https://gitee.com/owner/repo/pulls/123 --enrich --pyright

# 通过 HTTP API（需先启动 uvicorn）
uvicorn app.main:app --port 8000 &
python scripts/run_review_and_report.py https://gitee.com/owner/repo/pulls/123 --api-url http://127.0.0.1:8000
```

### 3. 输出

- `review_output/owner_repo_pr123_YYYYMMDD_HHMMSS.json`：完整报告
- `review_output/owner_repo_pr123_YYYYMMDD_HHMMSS_eval_labels.csv`：标注模板

---

## compute_catch_ratio.py

根据人工标注计算 Catch 比率。

### 1. 标注

打开 `*_eval_labels.csv`，在 `human_caught` 列填写：
- `1`：该评论成功 catch 了真实问题
- `0`：误报或无效建议
- 空：跳过，不参与计算

### 2. 计算

```bash
python scripts/compute_catch_ratio.py --labels review_output/owner_repo_pr123_20250101_120000_eval_labels.csv
```

### 3. 输出

- Catch 比率 = 成功 catch 数 / 已标注数
- 按严重程度、类别的细分比率

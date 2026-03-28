# AI PR Review — 未实现项与测试指南

---

## 一、尚未实现的功能

### 1.1 高/中优先级（文档中已规划）

| 项 | 说明 | 依赖 |
|----|------|------|
| **ai_risk 标记** | 输出 JSON 中为每条 comment 增加 `ai_risk: high\|medium\|low`，便于筛选 AI 相关风险 | 仅需改 review 解析与 prompt |
| **Linter 集成** | 3 分钟法则第一步：在 Pyright 之前跑 linter（ruff/flake8）并把结果注入 prompt | 需本地安装 ruff 或 flake8 |

### 1.2 低优先级（商业化差距）

| 项 | 说明 |
|----|------|
| **Auto-fix / 行内修复** | 生成可直接应用的 patch，而非仅文字建议 |
| **Walkthrough / 序列图** | Mermaid 图展示变更流程、组件交互 |
| **预估审查工作量** | 1–5 复杂度评分 |
| **关联 Issue/PR** | 从 PR 描述解析关联 issue，注入上下文 |
| **IDE / CLI 集成** | VS Code 插件、命令行工具 |
| **YAML 配置化** | `.prreview.yaml` 定制规则、忽略路径、Summary 格式 |
| **单元/集成测试** | 当前无 `tests/` 目录，无自动化测试 |

### 1.3 可选/环境相关

| 项 | 说明 | 何时需要 |
|----|------|----------|
| **PostgreSQL + pgvector** | RAG 索引、Symbol Graph | 使用 RAG 或 Symbol Graph 时 |
| **Pyright** | 类型检查、Phase0 Summary 的静态分析 | 4 维度审查 + Python 仓库 |
| **npm/pyright** | Pyright 需通过 npm 或 pip 安装且可用 | 勾选 Pyright 时 |

---

## 二、测试前准备

### 2.1 环境要求

```
Python 3.9+
pip install -r requirements.txt
```

### 2.2 可选环境变量

| 变量 | 用途 | 不配置时 |
|------|------|----------|
| `DATABASE_URL` | PostgreSQL 连接，用于 RAG、Symbol Graph | 相关功能跳过，不报错 |

示例：
```bash
export DATABASE_URL="postgresql+psycopg://user:pass@localhost:5432/prreview"
```

### 2.3 必需凭证（按测试场景）

| 凭证 | 用途 |
|------|------|
| **Gitee Token** | 拉取 PR、文件内容，发送评论 |
| **通义千问 API Key** | 单次/三阶段/4 维度审查、RAG embedding |
| **Kimi API Key** | 仅当选择 Kimi 时 |

获取方式：
- Gitee: 设置 → 私人令牌
- 通义千问: 阿里云百炼 / DashScope 控制台
- Kimi: 月之暗面开放平台

### 2.4 启动服务

```bash
cd /Users/xubaotian/Desktop/prreview
uvicorn app.main:app --reload --port 8000
```

浏览器访问：http://127.0.0.1:8000

---

## 三、测试场景（从简到全）

### 3.1 最小验证（无 DB、无 Pyright）

**目的**：确认基础流程可用。

1. 不配置 `DATABASE_URL`
2. 打开 http://127.0.0.1:8000
3. 填写 Gitee Token、通义千问 API Key
4. 输入任意 Gitee PR 链接，例如：`https://gitee.com/owner/repo/pulls/1`
5. **仅勾选**「使用 Mock 审查」
6. 点击「开始 Review」

**预期**：秒级返回 2 条预设 Mock 评论，说明前端 ↔ 后端链路正常。

---

### 3.2 单次 LLM 审查（无 DB）

**目的**：验证真实 LLM 调用。

1. 取消勾选 Mock
2. 不勾选：三阶段、4 维度、语义检索、Symbol Graph、Tree-sitter、Pyright
3. 点击「开始 Review」

**预期**：30–60 秒内返回 JSON 格式的 comments，含 file、line、severity、category、suggestion。

---

### 3.3 上下文增强（测试/import 关联）

**目的**：验证 `enrich_context` 是否拉取测试和 import 相关文件。

1. 勾选「包含测试与 import 相关文件（Python/Vue）」
2. 使用包含 Python 或 Vue 的 PR
3. 点击「开始 Review」

**预期**：`file_contexts` 中应包含 `test_xxx.py`、`xxx_test.py` 或 import 相关文件。可在 Network 面板查看请求体中的 `file_contexts` 键数量是否增加。

---

### 3.4 三阶段审查（multipass）

**目的**：验证 Pass1 → Pass2 → Pass3 编排。

1. 勾选「三阶段审查」
2. 确保 LLM 提供商为「通义千问」
3. 点击「开始 Review」

**预期**：耗时更长（约 1–2 分钟），评论中可能出现 Critical 条目，且 Pass3 会对 Critical 做深化。可观察 status 文案变化。

---

### 3.5 4 维度串行审查（核心功能）

**目的**：验证 Phase0 Summary + 4 维度 + 合并。

**前置**：
- LLM 提供商：通义千问
- 建议勾选「Pyright」以生成 Summary 中的静态分析（需本地安装 pyright）

**步骤**：
1. 勾选「4 维度串行审查」
2. 可选：勾选「Tree-sitter」「Pyright」（Pyright 需 `enrich_context` 或直接拉取 .py 文件）
3. 点击「开始 Review」

**预期**：
- 耗时约 2–4 分钟（1 次 Summary + 4 次维度）
- 返回的 comments 按 (file, line) 聚合，同一位置可能合并多条建议
- 评论会覆盖正确性、安全、质量、依赖等维度

**注意**：若 PR 无 Python 文件，Pyright 可能不产生诊断；Summary 仍会生成，只是 static_analysis_points 为空。

---

### 3.6 RAG 注入（需 DB）

**目的**：验证 RAG 检索并注入 Phase0 Summary。

**前置**：
1. 配置 `DATABASE_URL`
2. 确保数据库已执行 `CREATE EXTENSION IF NOT EXISTS vector;`
3. 先对目标仓库建 RAG 索引

**建索引**：
```bash
curl -X POST http://127.0.0.1:8000/api/rag/index \
  -H "Content-Type: application/json" \
  -d '{
    "repo_key": "owner/repo",
    "source_type": "policy",
    "embedding_api_key": "YOUR_DASHSCOPE_KEY",
    "documents": [
      {"content": "本项目禁止硬编码密钥，敏感配置必须使用环境变量。", "source_path": "CONTRIBUTING.md"}
    ]
  }'
```

**测试**：
1. 勾选「4 维度串行审查」
2. 使用 `owner/repo` 对应的 PR 链接
3. 点击「开始 Review」

**预期**：Phase0 Summary 的「适用规范」中应出现与 RAG 文档相关的内容；4 维度 prompt 会引用该 Summary。

---

### 3.7 Symbol Graph（需 DB）

**目的**：验证 caller/callee 扩展。

**前置**：`DATABASE_URL` 已配置，且 init_db 已创建 symbol 相关表。

**步骤**：
1. 勾选「Symbol Graph（PostgreSQL）」
2. 勾选「包含测试与 import 相关文件」（以便有足够 .py 文件）
3. 使用 Python 仓库的 PR
4. 点击「开始 Review」

**预期**：`file_contexts` 中会包含通过 caller/callee 关系扩展得到的文件。

---

### 3.8 语义检索（无 RAG 索引）

**目的**：验证用 diff 向量检索 file_contexts 内 Top-5 片段。

**步骤**：
1. 勾选「语义检索」
2. 不勾选 4 维度（二者互斥时优先 4 维度）
3. 不勾选三阶段，使用单次审查
4. 点击「开始 Review」

**预期**：`file_contexts` 会被语义检索扩展，评论可能更贴合 diff 语义。

---

## 四、API 级测试（curl）

### 4.1 Mock 审查

```bash
curl -X POST http://127.0.0.1:8000/api/review \
  -H "Content-Type: application/json" \
  -d '{
    "diff": "diff --git a/foo.py b/foo.py\n+def bar(): pass",
    "pr_title": "test",
    "pr_body": "",
    "llm_api_key": "sk-xxx",
    "use_mock": true
  }'
```

### 4.2 单次审查（需真实 API Key）

```bash
curl -X POST http://127.0.0.1:8000/api/review \
  -H "Content-Type: application/json" \
  -d '{
    "diff": "diff --git a/foo.py b/foo.py\n+def bar(): pass",
    "pr_title": "add bar",
    "pr_body": "",
    "llm_provider": "dashscope",
    "llm_api_key": "YOUR_DASHSCOPE_KEY",
    "use_mock": false
  }'
```

### 4.3 4 维度审查（含 repo_key）

```bash
curl -X POST http://127.0.0.1:8000/api/review \
  -H "Content-Type: application/json" \
  -d '{
    "diff": "diff --git a/foo.py b/foo.py\n+def bar(): pass",
    "pr_title": "add bar",
    "pr_body": "",
    "file_contexts": {"foo.py": "def bar(): pass"},
    "llm_provider": "dashscope",
    "llm_api_key": "YOUR_DASHSCOPE_KEY",
    "use_dimension_review": true,
    "repo_key": "owner/repo"
  }'
```

### 4.4 Fetch PR

```bash
curl -X POST http://127.0.0.1:8000/api/gitee/fetch-pr \
  -H "Content-Type: application/json" \
  -d '{
    "pr_url": "https://gitee.com/owner/repo/pulls/123",
    "gitee_token": "YOUR_GITEE_TOKEN",
    "enrich_context": true,
    "use_pyright": true
  }'
```

---

## 五、常见问题排查

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 拉取 PR 失败 | Token 无效或无权 | 检查 Gitee 私人令牌权限 |
| 审查返回 500 | LLM API Key 错误或超时 | 检查 Key、网络、DashScope 控制台 |
| RAG 无结果 | 未建索引或 repo_key 不一致 | 先 index，确保 repo_key 与 PR 的 owner/repo 一致 |
| Symbol Graph 无扩展 | 无 DATABASE_URL 或表未建 | 配置 DB、跑 init_db |
| Pyright 无输出 | 未装 pyright 或非 Python PR | `npm i -g pyright` 或 `pip install pyright`，确保有 .py 变更 |
| 4 维度很慢 | 串行 5 次 LLM 调用 | 正常，约 2–4 分钟 |

---

## 六、推荐测试顺序

1. Mock 审查 → 确认基础链路
2. 单次 LLM 审查 → 确认 API 可用
3. 勾选 enrich_context → 确认上下文增强
4. 勾选 4 维度串行 → 确认核心流程
5. 配置 DB → RAG 索引 → 4 维度 + repo_key → 确认 RAG 注入
6. 可选：Pyright、Symbol Graph、三阶段等

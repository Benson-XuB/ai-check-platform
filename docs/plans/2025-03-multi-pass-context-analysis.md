# 多轮审查 + 上下文增强 — 实现分析（先分析不改代码）

> 目标：把「AST 符号图 + 向量检索 + 测试关联」三种上下文合并进 `file_contexts`，再通过「预筛选 → 主审查 → 高风险」三阶段调用 LLM。本文只做**实现分析**，不写实现代码。

---

## 1. 当前数据流（现状）

```
前端
  → POST /api/gitee/fetch-pr { pr_url, gitee_token }
  ← { ok, data: { diff, file_contexts, title, body, owner, repo, number, head_sha } }

  → POST /api/review { diff, pr_title, pr_body, file_contexts, llm_provider, llm_api_key }
  ← { ok, data: { comments } }
```

- **file_contexts 来源**：仅来自 `gitee.fetch_pr` 的 L2——对 PR 变更文件列表逐文件调 Gitee Contents API（`ref=head_sha`），得到变更文件的**完整内容**。
- **审查**：单次 LLM 调用（DashScope qwen-plus 或 Kimi moonshot），一次 prompt（diff + file_contexts），输出 JSON/Markdown 评论列表。

---

## 2. 你设计的扩展

### 2.1 上下文增强（合并进 file_contexts）

| 来源 | 目标 | 说明 |
|------|------|------|
| **1. AST / Call Graph** | 找到变更函数的调用方/被调用方所在文件 | 需要解析变更文件中的函数/方法，建调用关系，再拉取相关文件内容 |
| **2. 向量索引 (text-embedding-v3)** | 用 diff 语义检索 Top-5 相关代码片段 | 需仓库内代码的向量表示；用 diff 做 query，检索最相关片段 |
| **3. 测试文件关联** | test_* / *_test.py 等约定匹配 → 注入测试覆盖情况 | 由变更文件路径推导可能存在的测试文件，拉取内容或至少标注「有无对应测试」 |

「所有上下文合并」→ 最终仍是一个 `file_contexts: Dict[str, str]`（或带元数据的结构），在 **Pass 2** 注入。

### 2.2 三阶段审查

| 阶段 | 模型 | 输入 | 输出 | 约 token |
|------|------|------|------|----------|
| **Pass 1 预筛选** | qwen-long | 仅 diff，无上下文 | 识别高风险区域 | ~500 |
| **Pass 2 主审查** | qwen-turbo | diff + 全量 file_contexts | 生成全部 comments | ~50K |
| **Pass 3 高风险** | qwen-max（可选） | 仅 Critical 类 issue | 深度安全/逻辑分析 | ~5K |

---

## 3. 与现有代码的对接点

### 3.1 上下文增强 — 插在哪里

- **当前**：`file_contexts` 只在 `gitee.fetch_pr()` 里由「PR 变更文件 + Gitee Contents API」生成。
- **扩展方式（二选一或组合）**：
  - **A. 在 fetch-pr 内扩展**：在 `gitee.fetch_pr` 里，拿到 `files` 和 `head_sha` 后，再调用「AST 服务、向量服务、测试匹配」三个能力，把额外路径/片段合并进 `file_contexts`，再对新增路径用 Gitee Contents API 拉内容。
  - **B. 独立「上下文增强」层**：`fetch-pr` 只做现有 L1+L2；新模块 `context_enrichment` 接收 `(owner, repo, head_sha, changed_files, diff)`，返回「扩展后的 file_contexts」（或「要追加的 path → content」），由 router 或 gitee 层合并后再调 review。

建议 **B**：保持 gitee 只负责「拉 PR + 拉文件内容」，上下文增强单独成层，便于关掉某一种（如先不做向量）或换实现。

### 3.2 三阶段审查 — 插在哪里

- **当前**：`review.py` 里单次 `call_dashscope` / `call_kimi`，一个 prompt 一次返回。
- **扩展**：
  - 在 **review 服务内部**做编排：
    1. **Pass 1**：只传 `diff`，用 qwen-long，解析返回的「高风险区域」（需约定输出格式，例如文件+行或区域描述）。
    2. **Pass 2**：用 qwen-turbo，传 `diff + file_contexts`，得到完整 `comments`。
    3. **Pass 3（可选）**：从 comments 里筛出 `severity=Critical`，只把这些片段 + 相关上下文再发给 qwen-max，做深度分析，可追加/替换原 Critical 条目的 suggestion。

- **路由**：仍可保持 `POST /api/review` 一个入口；请求体里可加 `use_multipass: bool` 或 `review_mode: "single" | "multipass"`，后端根据参数决定走单次还是三阶段。

---

## 4. 各能力实现要点与风险

### 4.1 AST 符号图 / Call Graph

- **要做的事**：从「变更文件」里解析出函数/类/方法，建调用关系（谁调谁、被谁调），得到「相关文件路径列表」，再拉取这些文件内容并入 `file_contexts`。
- **依赖**：
  - 能访问到仓库在 `head_sha` 下的文件内容（已有：Gitee Contents API）。
  - 语言相关：Python 可用 `ast`；其他语言需 tree-sitter 或外部工具。当前项目以 Python 为主的话，可先只做 Python。
- **风险**：
  - 多语言仓库：要限定语言或接受「只对部分文件建图」。
  - 调用关系可能跨文件、跨仓库（仅同仓库内可拉取）。
- **输出**：`List[str]` 额外文件路径 → 用现有 Gitee API 拉内容，合并进 `file_contexts`（注意去重、大小限制）。

### 4.2 向量索引 (text-embedding-v3)

- **要做的事**：用 diff 的语义去检索「仓库内与本次改动最相关的 Top-5 代码片段」，把这些片段并入上下文。
- **依赖**：
  - **Embedding 模型**：text-embedding-v3（DashScope），需 API Key。
  - **被检索的 corpus**：要么事先建好索引（如 CI 或 push 时对 repo 建索引），要么临时：根据 PR 拉取仓库部分文件/目录，切 chunk，现场 embed 再检索。
- **风险**：
  - **索引从哪来**：若没有现成索引，每次 review 都现场扫仓库、embed，成本和延迟都高；若做索引，需要触发时机（如 PR 创建时、定时、或首次打开某 repo）。
  - **多语言/大仓库**：chunk 策略、过滤（只索引源码）要设计。
- **输出**：若干「(path, snippet)」或「(path, start_line, end_line)」→ 转成 content 并入 `file_contexts`（可带前缀如 `[语义检索] path` 便于 prompt 区分）。

### 4.3 测试文件关联

- **要做的事**：由变更文件路径推断「可能存在的测试文件」（如 `foo.py` → `test_foo.py` / `foo_test.py`），拉取这些文件内容或至少标记「有无对应测试」。
- **实现**：约定规则（如 test_*、*_test.py、目录 tests/ 等），从 `files` 里的 filename 生成候选路径，用 Gitee Contents API 拉取存在则加入 `file_contexts`；可选：在 prompt 里加一句「以下为可能相关的测试文件」。
- **「注入测试覆盖情况」**：若指「是否被测试覆盖」需要执行测试才能知道，当前方案只能做「关联了哪些测试文件」，不能做真实覆盖率；若仅指「把测试文件内容注入上下文」，则实现如上即可。

### 4.4 三阶段 LLM 调用

- **Pass 1（qwen-long，仅 diff）**：
  - 需定义输出格式，例如：`{"high_risk_areas": [{"file": "...", "line": N, "reason": "..."}]}` 或纯自然语言再解析。
  - 若 Pass 2 不依赖 Pass 1 的结果，Pass 1 可仅用于「前端展示高风险」或「决定是否触发 Pass 3」；若 Pass 2 要依赖 Pass 1 做聚焦，则需把 Pass 1 输出结构化后传入 Pass 2 的 prompt。

- **Pass 2（qwen-turbo，diff + 全量 file_contexts）**：
  - 与现有单次审查一致，只是模型改为 qwen-turbo，且 `file_contexts` 已包含 AST + 向量 + 测试 的扩展内容。

- **Pass 3（qwen-max，仅 Critical）**：
  - 输入：Pass 2 中 `severity=Critical` 的条目对应的代码片段 + 必要上下文。
  - 输出：可约定为「对每条 Critical 的深化建议」，再写回或追加到原 comments。

- **模型与厂商**：qwen-long / qwen-turbo / qwen-max 均为 DashScope；若保留 Kimi 作为「单轮审查」的选项，可设计为：multipass 模式仅用 DashScope，单轮模式仍支持 Kimi。

---

## 5. 数据流小结（扩展后）

```
1) 前端 POST /api/gitee/fetch-pr
   → 后端 gitee.fetch_pr → 得到 diff, file_contexts(L2), head_sha, files[]

2)（可选）上下文增强
   → context_enrichment.enrich(owner, repo, head_sha, files, diff, file_contexts)
   → AST：变更文件解析 → 调用关系 → 额外路径 → Gitee 拉内容
   → 向量：diff embedding + 检索 Top-5 片段 → 并入
   → 测试：变更文件 → 约定测试路径 → Gitee 拉内容
   → 返回合并后的 file_contexts

3) 前端 POST /api/review（或带 use_multipass=true）
   → Pass 1: review_svc.pass1_prefilter(diff) → 高风险区域（可选给前端或仅内部用）
   → Pass 2: review_svc.pass2_main(diff, file_contexts, pr_title, pr_body) → comments
   → Pass 3（可选）: 筛 Critical → review_svc.pass3_deep_critical(...) → 深化 Critical
   → 合并 comments 返回
```

---

## 6. 实现顺序建议（仍不写代码，只排期）

1. **测试文件关联**：只依赖 Gitee API 和命名约定，无新服务，先做；合并进 `file_contexts` 的接口先定好（如统一在「上下文增强」层）。
2. **AST 调用图（仅 Python）**：在服务内解析变更的 .py 文件，产出额外路径列表，再拉内容；不依赖向量，不依赖新基础设施。
3. **三阶段审查（Pass 1 / 2 / 3）**：在 review 服务里加多轮编排与模型切换（qwen-long / qwen-turbo / qwen-max）；可先不做 Pass 1 结果对 Pass 2 的输入依赖，只做「Pass 1 仅展示，Pass 2 全量，Pass 3 仅 Critical」。
4. **向量检索**：依赖 embedding API + 索引或现场 chunk；可放在最后，或先做「仅当前 PR 变更文件 + 同目录文件」的轻量版（不做全库索引）。

---

## 7. 需要事先敲定的点

- **AST**：是否只做 Python？多语言是否用 tree-sitter 或暂不支持？
- **向量**：索引由谁、在何时构建？若无索引，是否接受「仅对本次 PR 变更相关目录做临时 embed+检索」？
- **「测试覆盖情况」**：是「把测试文件内容注入上下文」即可，还是要将来接测试执行结果（覆盖率）？
- **多轮与单轮**：multipass 是否仅 DashScope？Kimi 是否只保留单轮模式？
- **Pass 1 输出**：是否必须结构化给 Pass 2 用，还是仅作「高风险提示」独立展示？

以上都确定后，再按「先上下文合并，再多轮编排」的顺序落实现代码即可。

---

## 8. 已实现（2025-03）

- **4 维度串行审查**：`app/services/review.py` — `review_multidim`。流程：Phase0 生成 PR Summary（Pyright + RAG + PR）→ Phase1-4 串行调用 4 个维度 prompt（正确性/边界、安全、质量、依赖与并发）→ 按 `(file, line)` 去重聚合。RAG 需 `repo_key`，Pyright 从 `file_contexts["[pyright diagnostics]"]` 提取。路由 `use_dimension_review`，前端传 `repo_key`。
- **测试文件关联**：`app/services/context_enrichment.py` — `get_test_candidate_paths`（Python test_*/_test.py，Vue/JS .test.js/.spec.js 等），拉取后并入 `file_contexts`。
- **Python/Vue import 相关**：同模块 — `get_import_related_paths`（Python ast 解析 import/from；JS 正则 import/require），可选 `repo_tree_paths`（Gitee tree API）解析 Python 模块路径。
- **上下文增强接入**：`app/routers/gitee.py` — `fetch-pr` 支持 `enrich_context: bool`，为 True 时在 L2 基础上调用 `enrich_file_contexts`，合并测试与 import 相关文件；`app/services/gitee.py` 新增 `fetch_file_content`、`get_repo_tree_paths`。
- **三阶段审查**：`app/services/review.py` — `pass1_prefilter`（qwen-long，仅 diff）、`pass2_main`（qwen-turbo，diff+全量上下文）、`pass3_deep_critical`（qwen-max，仅 Critical 深化）；`review_multipass` 编排三者；路由 `POST /api/review` 支持 `use_multipass`，仅 DashScope 时生效。
- **Pass1 结果参与 Pass2**：`_build_prompt` 增加参数 `high_risk_areas`；Pass2 的 prompt 中注入「以下为预筛选识别的高风险区域，请重点审查：…」；`review_multipass` 将 Pass1 的返回值传入 `pass2_main(..., high_risk_areas=...)`。
- **向量检索**：`app/services/embedding.py` — DashScope `text-embedding-v3`，对 `file_contexts` 按行分块、embed diff 与各块、余弦相似度取 Top-5 片段并入上下文；路由在 `use_semantic_context=True` 且 DashScope 时先调用 `enrich_file_contexts_with_semantic_search` 再审查。
- **前端**：`static/index.html` — 勾选「包含测试与 import 相关文件」「语义检索」「三阶段审查」并传入后端。

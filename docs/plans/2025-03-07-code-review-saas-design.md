# AI Code Review SaaS 设计文档

> **目标**：基于现有 aiprreview 雏形，升级为「Gitee 专用 + 在线 SaaS + FastAPI」的高质量 Code Review 工具。

---

## 0. 产品定位（核心）

**不做**：替代 CI（构建、测试、Sonar、ESLint 等硬规则）。

**聚焦**：CI 做不到的事——
- 逻辑 bug、设计问题、可读性、边界情况、业务语义
- 像 reviewer 一样理解上下文，给出建议
- **软判断**（需人脑），与 CI 的**硬规则**互补

---

## 1. 上下文感知审查（Context-Aware Review）

### 1.1 为何需要全局上下文

仅看 diff 无法判断逻辑是否正确。AI 需要：
- 知道改动所在的**完整文件**长什么样
- 知道改动的**调用关系**（谁调用它、它调用了谁）
- 对项目有**整体认知**（结构、约定、意图）

这样才能判断：这段新逻辑是否和周围代码一致、是否遗漏边界、变量/消息是否表意正确。

### 1.2 方案：分层获取上下文

| 层级 | 内容 | 获取方式 | 用途 |
|------|------|----------|------|
| L1 改动 | diff（变更行） | Gitee PR files API | 知道改了什么 |
| L2 文件级 | 每个变更文件的**完整内容**（base + head） | Gitee contents API | 看改动在文件中的位置和上下文 |
| L3 关联文件 | 被改文件 import 的同仓库文件 | 解析 import，再拉取 | 看依赖、调用关系 |
| L4 项目概况 | README、目录结构、关键配置 | archive 或 tree API | 理解项目是做什么的 |

**实现顺序**：先做 L1+L2（改动 + 完整文件），再做 L3、L4。

### 1.3 Token 与裁剪策略

- 单次请求有 token 上限，需裁剪
- 策略：优先 L1 diff + L2 变更文件完整内容；L3 只拉直接 import 且非 node_modules/vendor 的文件；L4 仅 README + 顶层目录

### 1.4 Gitee API 获取文件内容

```
GET /repos/{owner}/{repo}/contents/{path}?ref={sha}
```
- `ref` 用 PR 的 head_sha（变更后）或 base_sha（变更前）
- 对 PR files 中的每个 `filename` 调用即可得到完整文件内容

---

## 2. 审查维度（LLM 关注点）

### 2.1 逻辑与正确性

- 条件分支是否覆盖所有情况（边界、异常）
- 状态是否一致、有无竞态
- 空指针、除零、数组越界等常见错误
- 逻辑是否符合业务意图

### 2.2 语义与命名

- 变量、函数、类名是否表意清晰
- 错误信息、日志、用户提示是否准确、有用
- 与项目既有命名风格是否一致

### 2.3 设计与可读性

- 职责是否清晰、是否过度耦合
- 抽象是否合理
- 可读性：结构、注释、复杂度

### 2.4 明确不做

- 不重复 lint 能做的事（缩进、括号、引号风格）
- 不重复 Sonar/静态分析能做的格式化、已知规则

---

## 3. 架构概览

```
┌─────────────────┐     HTTPS      ┌──────────────────────────────┐
│   Browser       │ ◄────────────► │   FastAPI Backend            │
│   (SPA/静态页)   │                │   - Gitee 代理               │
│   Token/Key     │                │   - LLM 审查（结构化 prompt）   │
└─────────────────┘                │   - 行级评论下发               │
                                   └──────────────────────────────┘
                                            │
                         ┌──────────────────┼──────────────────┐
                         ▼                  ▼                  ▼
                   Gitee API          Kimi/DashScope      (可选) Redis
                   拉取 PR/diff        生成 review           限流/缓存
```

**技术栈**：
- 后端：Python 3.11+ / FastAPI
- 前端：现有 HTML/JS，后续可演进为 Vue/React
- 部署：单实例（Docker / 云函数 / Vercel + 自托管 API）→ 多人在线访问同一 URL

**多用户模型**：无账号式 SaaS。用户访问 URL，每次输入 Gitee Token + LLM Key（或使用 localStorage 持久化），服务端不存储凭证。支持多用户并发使用。

---

## 4. 核心能力

### 4.1 现有能力（保留）

| 能力 | 现状 |
|------|------|
| 拉取 Gitee PR | 已实现 |
| 获取 diff | 已实现 |
| AI 审查 | 通义千问，可扩展 Kimi |
| 行级评论下发 | 已实现 `post-comment` + `_compute_diff_position` |

### 4.2 升级点（高质量）

| 升级项 | 说明 | 对应 skill |
|--------|------|------------|
| **结构化 Review 输出** | severity (Critical/Important/Minor) + category (security/correctness/perf/style/yagni) | requesting-code-review |
| **审查 Prompt 强化** | 可验证、可执行；禁止空泛夸奖；YAGNI 意识；每项带 file/line/severity/suggestion | receiving-code-review |
| **解析容错** | 支持 JSON 结构化输出优先，正则兜底 | 工程健壮性 |
| **前端筛选** | 按 severity 过滤；编辑后再发送 | 用户体验 |

---

## 5. API 设计

### 5.1 端点（FastAPI）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 前端静态页 |
| POST | `/api/gitee/fetch-pr` | 拉取 PR + diff + 变更文件完整内容（body: pr_url, gitee_token） |
| POST | `/api/review` | 统一审查入口（body: diff, file_contexts, pr_title, pr_body, llm_provider, llm_api_key） |
| POST | `/api/gitee/post-comment` | 下发评论到 PR（body: owner, repo, number, path, line, comment, commit_id, diff, gitee_token） |

### 5.2 LLM 提供商抽象

```python
# 支持多后端，通过 llm_provider 切换
llm_provider: "kimi" | "dashscope"
```

- `kimi`：月之暗面 API
- `dashscope`：通义千问（现有）

---

## 6. 审查质量：Prompt 设计

### 6.1 系统原则（来自 receiving-code-review）

- 只给可验证、可执行的建议
- 每条带 severity：Critical / Important / Minor
- 禁止空泛夸奖（如「写得不错」）
- YAGNI：对疑似未使用代码，优先建议删除或标注

### 6.2 输出格式（结构化）

**首选：JSON**

```json
{
  "comments": [
    {
      "file": "src/auth.py",
      "line": 42,
      "severity": "Important",
      "category": "security",
      "suggestion": "此处未校验 userId，可能越权访问，建议添加权限检查"
    }
  ]
}
```

**兜底：Markdown 正则**（兼容现有 `## 文件:` / `### 第 N 行` 格式）

### 6.3 User Prompt 模板（含上下文）

```
你是一名 Code Reviewer。你的关注点是 CI/静态工具难以覆盖的：

1. 逻辑 bug：条件分支遗漏、状态不一致、竞态、空指针、除零、数组越界
2. 设计问题：职责不清、过度耦合、抽象不合理
3. 可读性：命名、注释、变量/消息表意是否清晰
4. 边界情况：空值、异常、超时、重试
5. 业务语义：实现是否符合需求、是否有隐含假设

不要重复 lint/格式化/Sonar 能做的事。

规则：
- 每条建议必须可验证、可执行，禁止空泛夸奖
- severity: Critical(阻塞)/Important(重要)/Minor(建议)
- category: logic|design|readability|edge_case|semantic|security
- 对疑似未使用的代码，优先建议删除(YAGNI)

请以 JSON 格式返回：
{"comments":[{"file":"path","line":N,"severity":"...","category":"...","suggestion":"..."}]}

---
PR 信息：
- 标题：{pr_title}
- 描述：{pr_body}

---
变更的 diff：
{diff}

---
以下为变更文件的完整内容（用于理解上下文）：
{file_contexts}
```

---

## 7. 前端改动

- 解析优先用 JSON，失败时用现有正则
- 每条评论展示：`[Critical] security: 建议内容`
- 可按 severity 筛选（如只显示 Critical + Important）
- 支持编辑后再发送
- 传递 diff / commit_id / path / line 给 `post-comment`，保持行级评论

---

## 8. 目录结构（FastAPI）

```
prreview/
├── app/
│   ├── main.py           # FastAPI app
│   ├── routers/
│   │   ├── gitee.py      # fetch-pr, post-comment
│   │   └── review.py     # /api/review
│   ├── services/
│   │   ├── gitee.py      # Gitee API 封装
│   │   ├── review.py     # LLM 调用 + 解析
│   │   └── llm/
│   │       ├── base.py    # 抽象
│   │       ├── kimi.py
│   │       └── dashscope.py
│   └── config.py
├── static/
│   └── index.html
├── requirements.txt
├── Dockerfile
└── docs/
    └── plans/
        └── 2025-03-07-code-review-saas-design.md
```

---

## 9. 部署与安全

### 9.1 部署方式

- **方案 A**：Docker 部署到自有服务器 / 云主机
- **方案 B**：Railway / Render / Fly.io 等 PaaS
- **方案 C**：前端 Vercel，API 用 serverless（需拆成函数，略复杂）

推荐起步用 **方案 A 或 B**，便于加 CORS、限流、健康检查。

### 9.2 安全

- Token/Key 仅从前端传入，不落库
- HTTPS
- 限流：按 IP 或（如有）userId 限流
- CORS：配置可信前端域名

---

## 10. 实施顺序

1. 搭建 FastAPI 项目骨架，迁移现有 `/api/gitee/*`、`/api/dashscope/review` 逻辑
2. **上下文获取**：fetch-pr 时拉取变更文件的完整内容（L1+L2），组装 `file_contexts` 传给 LLM
3. 实现 Kimi 支持，统一 `/api/review` 接口
4. 升级 Prompt（含上下文、审查维度）+ 结构化 JSON 输出，实现双解析（JSON + 正则）
5. （可选）L3 关联文件：解析 import，拉取同仓库依赖文件
6. 前端：severity 展示、筛选、编辑后发送
7. Docker + 部署文档

---

## 11. 与现有 aiprreview 的关系

- 设计基于 `aiprreview/proxy.py` 和 `index.html` 的逻辑
- 迁移时保留：`parse_pr_url`、`_compute_diff_position`、`post-comment` 行级逻辑
- 新增：FastAPI 结构、Kimi、结构化 prompt、前端筛选

---

## 12. 验收标准

- [ ] 输入 Gitee PR 链接 + Token + LLM Key，能完成审查并下发评论
- [ ] **上下文感知**：审查时传入变更文件的完整内容，LLM 能基于上下文判断逻辑
- [ ] 支持 Kimi 与 DashScope 两种 LLM
- [ ] 评论含 severity、category，可按 severity 筛选
- [ ] 行级评论能正确落在 diff 对应行
- [ ] 支持 Docker 一键部署，多用户可同时访问

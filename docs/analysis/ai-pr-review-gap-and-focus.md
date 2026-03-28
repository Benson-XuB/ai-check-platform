# AI PR Review 与商业化产品差距 + AI 生成代码审查聚焦点

> 主旨：本工具面向 **AI 写的代码** 的审查。AI 生成代码有系统性、可预测的失败模式，与传统人工代码的缺陷分布不同，审查策略应针对性调整。

---

## 一、与商业化 PR Review 产品的差距

### 1.1 功能对比（vs CodeRabbit / Codium / GitHub Copilot）

| 能力 | 本工具 | CodeRabbit | Codium/Qodo | GitHub Copilot |
|------|--------|------------|-------------|----------------|
| **基础审查** | ✓ diff + file_contexts + LLM | ✓ | ✓ | ✓ |
| **多阶段/多维度** | ✓ 三阶段 + 4 维度串行 | ✓ 多 agent 并行 | 较少 | 单次 |
| **上下文增强** | ✓ 测试/import/语义检索/Symbol Graph/Pyright | ✓ 40+ linter + agentic 探索 | ✓ | ✓ |
| **PR Summary** | ✓ Phase0 生成（Pyright+RAG+PR） | ✓ 自动追加 PR 描述 | ✓ | 部分 |
| **RAG 规范注入** | ✓ | ✓ Living memory | 部分 | 部分 |
| **序列图/架构图** | ✗ | ✓ Mermaid walkthrough | ✗ | ✗ |
| **一行修复建议 / Auto-fix** | ✗ | ✓ 一键应用 | 有限 | ✓ 行内建议 |
| **预估审查工作量** | ✗ | ✓ 1–5 复杂度 | ✗ | ✗ |
| **关联 Issue/PR** | ✗ | ✓ | ✗ | ✓ |
| **IDE / CLI 集成** | ✗ 仅 Web | ✓ VS Code/Cursor/Windsurf + CLI | ✓ | ✓ IDE 内 |
| **平台支持** | Gitee | GitHub/GitLab/Bitbucket/Azure | GitHub 等 | GitHub |
| **配置化** | 无 YAML | ✓ .coderabbit.yaml | ✓ | ✓ |
| **延迟** | 串行较慢（5 次 LLM） | ~90s | 2–4min | 较快 |
| **假阳性率** | 未量化 | ~15% | ~25% | 未公开 |

### 1.2 主要差距归纳

1. **无 Auto-fix / 行内修复**：商业化产品可一键应用建议，本工具仅输出评论，需要人工改代码。
2. **无 Walkthrough / 序列图**：缺少 Mermaid 图、变更流程可视化，人类 reviewer 快速理解改动的能力弱。
3. **无 IDE / CLI**：只能在 Web 使用，无法嵌入开发流。
4. **无配置化**：不能按仓库定制规则、Summary 格式、忽略路径等。
5. ~~**无「AI 代码专项」检查**~~：✅ 已实现，4 维度均有 AI 专项检查项。

---

## 二、AI 生成代码的常见问题（应重点审查）

基于文献（Augment、Veracode、学术论文）与实践，AI 生成代码失败有 **8 类系统性模式**，与传统人工代码的随机性不同，适合用固定检查清单覆盖。

### 2.1 八大失败模式（按优先级）

| 模式 | 描述 | 典型表现 | 现有覆盖 |
|------|------|----------|----------|
| **1. 幻觉 API / 不存在库** | 调用或导入不存在的函数/库 | `import fakelib`、`obj.non_existent_method()`、假包名 | 部分（依赖维度） |
| **2. 错误属性 / 错误参数** | API 存在但属性/参数错误 | `optimizer.parallel_io_enable` 等虚构参数 | 部分（Pyright 能发现类型） |
| **3. 安全漏洞** | 功能正确但存在安全风险 | SQL 拼接、未校验输入、错误日志泄露 | ✓ 安全维度 |
| **4. 缺少边界条件** | 只覆盖“正常路径” | 空值、空数组、越界、超时未处理 | ✓ 正确性与边界 |
| **5. 错误处理假设 happy path** | 异常处理不完整 | try 只 log 不处理、错误信息暴露实现细节 | ✓ 正确性与边界 |
| **6. 性能反模式** | 小数据正确、生产负载失败 | O(n²) 循环、循环内字符串拼接、不当数据结构 | ✓ 质量维度 |
| **7. 数据模型不匹配** | 假设的 schema 与真实不符 | 假设 API 返回 `user.id`，实际是 `user.user_id` | ✗ 未专项 |
| **8. 缺少上下文依赖** | 单测通过、集成失败 | 环境变量未定义、跨服务依赖不存在 | ✗ 未专项 |

### 2.2 数据支撑

- 约 **1/5** AI 代码片段引用虚假库（Sloppy Squatting 等研究）
- 约 **45%** AI 生成代码含安全漏洞（Veracode 2025），Java 场景 >70%
- 幻觉 API 在高频 API 上较少，在 **低频 API** 上错误率明显更高（如 GPT-4o 仅 38.58% 有效）
- 错误往往“看起来合理”，容易被忽略

### 2.3 快速筛查顺序（3 分钟法则）

1. **先跑 linter**：语法、格式、风格
2. **再跑类型检查**：Pyright 等，优先暴露幻觉 API/错误属性
3. **最后跑测试**：集成和回归问题

本工具已接入 Pyright，符合这一顺序；可进一步在 Prompt 中强调「优先质疑新增 import 和 API 调用」。

---

## 三、针对「AI 写代码」的审查强化建议

### 3.1 新增或强化维度

| 建议 | 说明 |
|------|------|
| **新增「API/依赖真实性」** | 专门检查：新增 import 是否真实、调用方法是否在库中存在、参数是否符合文档（可结合 Pyright + 简单包名白名单） |
| **新增「数据契约匹配」** | 检查：对 API/DB 返回结构的假设是否与现有类型/接口一致，是否有多余或缺失字段访问 |
| **强化「边界条件」** | 在正确性 prompt 中显式加入：空数组、null/None、0、负数、极大值、Unicode、超时、网络失败 |
| **强化「错误处理」** | 检查：异常是否只 log 不处理、错误信息是否泄露栈/路径/配置 |

### 3.2 Prompt 增强示例

在现有维度 prompt 中增加「AI 代码专项」段落，例如：

```
【AI 生成代码特别关注】（若本次改动疑似由 AI 生成，请重点检查）
1. 新增的 import 和库调用：是否可能为幻觉 API？是否需在 PyPI/npm 等验证？
2. 对 JSON/API 响应的属性访问：是否与项目现有类型定义或文档一致？
3. 错误处理：是否假设 happy path，对 null/异常/超时处理不足？
4. 环境与配置：是否依赖未在仓库中声明的环境变量或外部服务？
```

### 3.3 与现有实现的衔接

- **Pyright**：已用于 Phase0 Summary，且能发现类型/属性错误，可视为「API 真实性」的主要防线
- **RAG**：可索引项目 API 文档、README、架构说明，用于核对「数据契约」和「依赖真实性」
- **4 维度**：「依赖与并发」中已有「API 真实性」，可拆成更细的「API 幻觉检查」子项

---

## 四、优先级排序（建议落地顺序）

1. ~~**高优先级**：在 4 维度 prompt 中加入「AI 代码专项」检查项（无需新接口）~~ ✅ 已实现
2. ~~**中优先级**：新增「数据契约/模型匹配」维度，或并入「依赖与并发」~~ ✅ 已实现（并入依赖与并发）
3. **中优先级**：输出结构化 JSON 时，增加 `ai_risk: high|medium|low` 等标记，便于筛选
4. **低优先级**：Auto-fix、Walkthrough 图、IDE 插件（工程量大，可后续迭代）

---

## 五、参考来源

- Augment Code: [Debugging AI-Generated Code: 8 Failure Patterns](https://www.augmentcode.com/guides/debugging-ai-generated-code-8-failure-patterns-and-fixes)
- Veracode 2025: AI-Generated Code Security Study
- arXiv: Bugs in LLM-Generated Code, API Hallucination in Code LLMs
- CodeRabbit Docs: Architecture, Walkthroughs, Autofix
- 智源社区：LLM 代码幻觉、API 文档增强

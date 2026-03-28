# 上线前安全与架构自查工具 — 设计稿

**日期**：2026-03-28  
**状态**：设计稿（已有 [实现计划](./2026-03-28-prelaunch-scan-implementation-plan.md)）  
**与现有产品关系**：与「Gitee PR Review」并列；PR 管迭代 diff，本工具管 **发布前整仓扫描 + 报告**。

---

## 1. 目标用户与价值

- **用户**：一人公司 / 小团队，无专职安全、架构、测试做完整上线前流程。  
- **价值**：用 **自动化扫描（可复现）+ AI（解读、排序、建议、架构/清单类叙述）** 替代「Sonar + 多类安全扫描 + 架构评审会议 + 部分验收条目」中 **可产品化** 的部分。  
- **非目标**：不替代渗透测试、等保/合规认证、律师意见；产品内须显著展示免责声明。

---

## 2. 输入与输出

### 2.1 输入

- **Git 仓库 URL + 凭证**（Token / 只读 deploy key），服务端 **clone** 到隔离工作目录（建议 shallow clone + 可选 ref：`branch` / `tag`）。  
- 可选：排除路径（`node_modules`、`vendor`、`.git` 等默认排除）。

### 2.2 输出

| 形态 | 说明 |
|------|------|
| **Web** | 单次扫描任务页：总览、按类别（依赖 / 密钥 / SAST / 架构与清单）分页、严重级别筛选、原始 findings 可展开。 |
| **PDF** | 与 Web **同内容结构**的可下载报告（见 §6），便于存档或发给合作方。 |

---

## 3. 语言范围与扫描策略（首发）

对 **Python、Node（npm/yarn/pnpm）、Java、JavaScript** 做 **检测与报告**；**JavaScript** 与 Node 重叠部分合并策略：以 **仓库内 manifest / 构建配置** 判定（见下）。

### 3.1 分层

1. **语言无关（全仓）**  
   - 密钥与敏感模式：**gitleaks**（或等价）  
   - 通用 SAST 规则：**Semgrep**（社区规则集 + 安全向规则，按语言启用）  

2. **Python**  
   - 依赖：`pip-audit`（或 `uv`/`pip` + OSV）  
   - 安全向静态：**bandit**（与 Semgrep 互补，择优去重）  

3. **Node / JavaScript**  
   - 若存在 `package.json`：`npm audit` / `pnpm audit` / `yarn npm audit`（按锁文件选择）  
   - 纯前端无 Node 构建：仍跑 **Semgrep（JS/TS）** + **gitleaks**；不强行要求 `package.json`  

4. **Java**  
   - 若存在 Maven/Gradle：依赖 CVE（**OWASP Dependency-Check** 或 **trivy fs** 二选一，以许可证与镜像体积权衡）  
   - 静态：**Semgrep（Java）**；可选后续加 **SpotBugs**（Phase 2，因需 JVM 与构建产物）  

### 3.2 去重与优先级

- 多工具同一位置、同类问题：**合并为一条 finding**，保留工具来源列表。  
- 严重级别：统一映射为 **Critical / High / Medium / Low / Info**（含「仅 AI 建议、无扫描器锚点」单独标记，避免与扫描结果混淆）。

---

## 4. AI 层职责（在扫描结果之上）

- **汇总摘要**：执行摘要、Top N 风险、按类别统计。  
- **逐条增强**：在附 **文件路径 + 行号 + 短代码片段** 的前提下，生成可读说明、修复建议、误报可能性提示。  
- **轻量 Architecture Review**：基于目录结构、主要入口（如 `main`/`app`/`server`）、依赖清单、扫描结果热点，输出 **结构化章节**（边界、数据流风险、单点建议）；标明 **启发式，非正式架构评审决议**。  
- **验收 / 合规向清单**：生成 **可勾选清单**（HTTPS、备份、隐私政策链接、日志脱敏等），**无法律结论**，仅「常见实践条目」。  

**约束**：无扫描器锚点的纯 AI 推断单独归类（如 `heuristic`），UI 与 PDF 中与「工具 findings」分区展示。

---

## 5. 系统架构（建议）

```text
[API] 提交任务 (repo URL, ref, creds)
   → [Worker] 沙箱工作目录 clone
   → [Runner] 并行/串行调用 gitleaks / semgrep / bandit / npm audit / dep-check…
   → [Normalizer] 统一 schema + 去重
   → [LLM] 摘要 + 逐条增强 + 架构章节 + 清单
   → [Store] 任务状态 + JSON 结果
[API/Web] 展示 + 触发 PDF 生成
```

- **任务异步**：clone + 扫描可能分钟级；Web 轮询或 SSE/WebSocket 展示进度。  
- **凭证**：短期内存或加密落盘，任务结束后删除工作目录；文档中写数据保留策略。

---

## 6. PDF 生成

- **内容**：与 Web 报告同构（封面：项目名、ref、扫描时间、工具版本列表；正文：摘要、Findings、架构节、清单）。  
- **实现选项（实现阶段再定）**：  
  - **HTML → PDF**：先用服务端模板渲染 HTML（与 Web 共用组件或同源数据），**Playwright** / **WeasyPrint** 出 PDF；或  
  - **结构化 PDF**：ReportLab / fpdf2 直接排版（开发成本高，版式统一易控）。  
- **推荐倾向**：**HTML 模板 + 一次渲染 Web + PDF**（数据源同一 JSON），降低双份维护。

---

## 7. MVP 与 Phase 2

**MVP**  
- Git clone + **gitleaks + Semgrep**（五语言覆盖）+ **Python bandit + npm audit**（有则执行）  
- Java：**Semgrep + trivy fs**（或仅 Semgrep + 依赖扫描二选一，先保证一条稳定链路）  
- Web 报告 + PDF 下载  
- AI：摘要 + finding 增强 + 架构短文 + 清单  

**Phase 2**  
- SonarQube / SonarScanner 可选集成（需服务端资源与客户 Sonar 实例或托管）  
- Java SpotBugs、容器镜像扫描（若提供 Dockerfile）  
- 与现有 **prreview** 共享登录 / 租户（若产品统一）

---

## 8. 风险与合规

- 客户代码属敏感数据：传输 TLS、存储最小化、工作目录清理。  
- 对外文案：**自查辅助工具**；扫描器许可证（GPL 等）需在分发说明中列明。  

---

## 9. 待实现时依赖的决策（已在本稿确定）

- 语言：**Python、Node、Java、JavaScript**（与 Node 重叠按仓库自动判定）。  
- 报告：**Web + PDF 下载**。  

后续实现前可再定：任务队列（Redis/RQ/Celery）、单租户先上还是多租户、Java 依赖扫描具体选型（Dependency-Check vs trivy）。

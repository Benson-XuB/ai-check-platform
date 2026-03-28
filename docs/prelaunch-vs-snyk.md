# Prelaunch 与 Snyk 等 SaaS 的关系（FAQ）

## 需要接入 Snyk 吗？

**不需要。** Prelaunch 是自托管的「上线前一把扫」工具，与 Snyk 无依赖关系。Snyk 可作为**市场参照**：持续监控、GitHub 原生集成、商业依赖库更强。

## 什么时候用 Prelaunch，什么时候用 Snyk？

| 场景 | 更贴切 |
|------|--------|
| 代码在 **Gitee** / 必须 **数据不出境** | Prelaunch（或 CI 里自跑开源 CLI） |
| 代码在 **GitHub**，要 **每次 PR 自动拦** | Snyk / GHAS 等更省心 |
| **发布前**要一份 **HTML/PDF + 中文 AI 摘要** | Prelaunch |
| **长期**跟踪依赖与许可证策略 | Snyk / Sonar 等 |

## 可以一起用吗？

可以：日常用 SaaS 做持续扫描，**发版前**再用 Prelaunch 出统一报告给客户或内审。

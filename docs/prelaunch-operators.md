# Prelaunch 运维说明

## 宿主机 / 镜像内建议安装的 CLI

扫描在 **clone 后的仓库目录** 内执行；未安装的工具会跳过并在 `raw_*.json` 中标记 `skipped`。

| 工具 | 用途 | 检查命令 |
|------|------|----------|
| git | 克隆 | `git --version` |
| gitleaks | 密钥泄露 | `gitleaks version` |
| semgrep | 多语言 SAST | `semgrep --version` |
| bandit | Python 静态 | `bandit --version` |
| pip-audit | Python requirements CVE | `pip-audit --version` |
| npm / pnpm / yarn | 前端依赖审计 | `npm --version` |
| trivy | 依赖/文件漏洞 | `trivy --version` |

**Docker 镜像**：`Dockerfile` 已包含 **gitleaks、trivy、Node/npm**，且 `requirements.txt` 含 **semgrep、bandit、pip-audit**（构建时 `pip install`）。镜像为 **linux/amd64** 优化；ARM 需自行替换 gitleaks/trivy 下载地址或本地构建。

**pip-audit**：当前 MVP 仅当仓库根目录存在 **`requirements.txt`** 时执行；纯 `pyproject.toml` 项目可先导出依赖再扫。

**任务清理**：服务启动时会按 `PRELAUNCH_JOB_TTL_HOURS`（默认 24）删除 **已完成/失败** 且超过保留期的任务目录。

## 环境变量

见仓库根目录 `.env.example` 中 `PRELAUNCH_*`。

## 健康检查

`GET /api/prelaunch/health` 返回各 CLI 是否在 `PATH` 中。

## PDF

依赖 Python 包 **WeasyPrint**；Debian/Ubuntu 需安装 Pango/Cairo 等（见 Dockerfile `apt` 列表）。若 PDF 生成失败，用户可使用 HTML 报告「打印为 PDF」。

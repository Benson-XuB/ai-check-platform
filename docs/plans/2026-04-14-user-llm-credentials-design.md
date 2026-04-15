# 用户级 LLM 凭证与默认模型（Gitee + GitHub）

**状态：** 设计已定稿，待实现  
**日期：** 2026-04-14  

## 1. 目标

- 登录用户（`AppUser`，含 Gitee OAuth 与 GitHub App 安装绑定）可在站内维护 **多条** LLM 配置（提供商 + 模型标识 + API Key），并 **指定一条为默认（Use this）**。
- **自动审查（A）**：Gitee SaaS WebHook（`process_saas_merge_request_webhook`）与 GitHub SaaS WebHook（`process_saas_github_pull_request_webhook`）在调用 `run_review_core` 时，优先使用该用户的默认凭证；**若未配置、凭证无效或解密失败，则回退到现有站点级环境变量**（与当前 `platform_llm_key()` 行为一致）。
- **手动审查（B）**：手动审查页支持选择提供商/模型、输入 Key、**测试连接**，并可保存到凭证表、设为默认（与 A 共用数据）。

## 2. 非目标（YAGNI）

- 不提供「团队/组织」级共享 Key（仅 `user_id` 维度）。
- 不要求首版支持任意 OpenAI 兼容 Base URL（仅扩展已有 `dashscope` / `kimi` 等与 `app/routers/review.py` 一致的路径）；若后续需要可再加 `provider=openai_compatible` + `base_url` 字段。
- 不在首版做密钥轮转审计 UI（仅服务端日志区分 `user_llm` vs `platform_fallback`）。

## 3. 数据模型

### 3.1新表 `user_llm_credentials`

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | PK | |
| `user_id` | FK → `app_users.id` | 所有者 |
| `label` | String(128), nullable | 用户备注，如「个人通义」 |
| `provider` | String(32) | `dashscope` \| `kimi`（与 `ReviewRequest.llm_provider` 对齐） |
| `model` | String(128), nullable | 可选；为空则沿用各 provider 现有默认模型逻辑 |
| `api_key_encrypted` | Text | **加密存储**（见 §5） |
| `created_at` / `updated_at` | timestamptz | |

索引：`user_id`；可选 `(user_id, provider)` 非唯一。

### 3.2 `app_users` 扩展

- `active_llm_credential_id`：nullable FK → `user_llm_credentials.id`，**ON DELETE SET NULL**。
- 「Use this」即更新该字段指向选定凭证；列表页展示当前默认标记。

约束：应用层保证 `active_llm_credential_id` 若非空，则对应行的 `user_id` 必须等于当前用户（可在 API 层校验，避免跨用户引用）。

## 4. 解析逻辑（自动审查）

新增例如 `resolve_llm_for_review(app_user_id: int) -> tuple[str, str, Optional[str]]`：

1. 读 `AppUser.active_llm_credential_id`；若为空 → **回退** `platform_llm_key()`（并返回 model=`None` 表示走代码内默认）。
2. 若存在：加载对应 `UserLlmCredential`，解密 `api_key`；若行不存在或解密失败 → 记录 warning，**回退** `platform_llm_key()`。
3. 若 key 非空：返回 `(provider, key, model)`。

**替换调用点：**

- `app/services/gitee_saas.py`：`process_saas_merge_request_webhook` 中当前 `provider, llm_key = platform_llm_key()` 改为上述解析；若用户 key 失败且平台也无 key，保持现有失败落库文案，可区分 error 信息含 `fallback exhausted`。
- `app/services/github_saas.py`：`process_saas_github_pull_request_webhook` 同样替换。

**日志：** 审查开始时打结构化日志（不含 key）：`llm_source=user_credential|platform_env`，`credential_id`（若适用）。

## 5. 安全

- **传输**：仅 HTTPS 部署下强制 `SESSION_HTTPS_ONLY`；与现有会话一致。
- **存储**：使用服务端密钥 `LLM_CREDENTIAL_ENCRYPTION_KEY`（或复用 `SESSION_SECRET` 派生，**推荐独立32-byte Fernet/AES-GCM key**）加密 `api_key_encrypted`；密钥仅存环境变量，不入库。
- **API 响应**：列表/详情仅返回 `key_last4` 或 `key_masked`；**永不**返回完整 key。
- **测试接口**：接受明文 key 时仅内存使用，默认不落库；或接受 `credential_id` 对已存 key 做 ping。

## 6. HTTP API（建议挂载 SaaS 应用，需登录会话）

统一前缀例如 `/api/saas/llm/`（Gitee/GitHub 共用 `AppUser.id`）：

|方法 | 路径 | 说明 |
|------|------|------|
| GET | `/credentials` | 当前用户凭证列表 + 哪条为 active |
| POST | `/credentials` | 创建（body: label, provider, model?, api_key 明文） |
| PATCH | `/credentials/{id}` | 更新 label/model；可选更新 api_key（非空则替换密文） |
| DELETE | `/credentials/{id}` | 删除；若删的是 active，则 `active_llm_credential_id` 置空 |
| POST | `/credentials/{id}/activate` | Use this |
| POST | `/credentials/test` | body: 临时 `{provider, model?, api_key}` 或 `{credential_id}`，调用最小 LLM 探测 |

鉴权：与现有 `/api/saas/gitee/me` 类似，从 session 取 `user_id`；GitHub 侧需确认现有 session 是否同一 `AppUser`（安装流程应已绑定）。

## 7.前端

- **Gitee**：`static/app-gitee.html` 增加「模型与密钥」卡片：列表、添加表单、测试、设为默认。
- **GitHub**：`static/app.html`（或等价控制台页）增加同样模块。
- **手动审查**：`static/manual-review.html` 增加可选「使用已保存的默认凭证」开关，或下拉选择一条凭证 + 覆盖 Key；保留「仅本次输入 Key」以兼容未登录场景（若手动页允许匿名则仍走请求体 key）。

## 8. `ReviewRequest` / `run_review_core`

- 若需 per-user **model** 覆盖：在 `ReviewRequest` 增加可选 `llm_model: Optional[str]`，并在 `run_review_core` → `review_svc.*` 调用链中传入（`call_dashscope` 等已有 `model` 参数的可直连）。
- WebHook 路径将 `resolve_llm_for_review` 的第三元组传入该字段。

## 9. 数据库迁移

- 项目若使用 `init_db` 建表：在 `app/storage/models.py` 增加模型并确保 `init_db` 创建新表；生产环境若有 Alembic 则补迁移（以仓库现状为准）。

## 10. 测试建议

- 单元：`resolve_llm_for_review` — 无凭证回退、有凭证优先、解密失败回退、平台无 key 失败。
- 集成：伪造 session 调用 CRUD；test 接口对 mock provider。

## 11. 实现顺序建议

1. 模型 + 加密工具 + `resolve_llm_for_review` + 替换 Gitee/GitHub SaaS WebHook 调用。  
2. REST API + 会话鉴权。  
3. `app-gitee` / `app` 控制台 UI。  
4. 手动审查页集成与 e2e 冒烟。

---

**已确认产品决策：** 允许回退站点级 `DASHSCOPE_API_KEY` / `KIMI_API_KEY`；Gitee 与 GitHub 用户共用 `AppUser` 维度凭证表。

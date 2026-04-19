-- 用户 LLM 凭证：支持自定义 Base URL + 模型（HTTPS 校验在应用层）
-- 已有库执行一次：psql "$DATABASE_URL" -f scripts/sql/migrate_user_llm_custom_endpoint_postgres.sql

ALTER TABLE user_llm_credentials
    ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE user_llm_credentials
    ADD COLUMN IF NOT EXISTS custom_base_url TEXT;

ALTER TABLE user_llm_credentials
    ADD COLUMN IF NOT EXISTS custom_model VARCHAR(256);

UPDATE user_llm_credentials SET is_custom = false WHERE is_custom IS NULL;

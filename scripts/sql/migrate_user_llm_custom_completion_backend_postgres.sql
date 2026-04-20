-- 自定义 LLM：记录探测得到的调用后端（anthropic | litellm）
-- psql "$DATABASE_URL" -f scripts/sql/migrate_user_llm_custom_completion_backend_postgres.sql

ALTER TABLE user_llm_credentials
    ADD COLUMN IF NOT EXISTS custom_completion_backend VARCHAR(16);

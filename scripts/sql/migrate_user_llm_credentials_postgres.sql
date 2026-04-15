-- 已有库升级：用户 LLM 凭证与 app_users.active_llm_credential_id
-- create_all() 不会改已有表结构，部署新代码后需在 PostgreSQL 上执行一次本脚本。
-- 用法：psql "$DATABASE_URL" -f scripts/sql/migrate_user_llm_credentials_postgres.sql
-- （若 URL 带 +psycopg，请换成 libpq 可识别的 postgresql://...）

CREATE TABLE IF NOT EXISTS user_llm_credentials (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES app_users(id),
    preset_id VARCHAR(96) NOT NULL,
    api_key_encrypted TEXT NOT NULL,
    label VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_user_llm_preset UNIQUE (user_id, preset_id)
);

CREATE INDEX IF NOT EXISTS ix_user_llm_credentials_user_id ON user_llm_credentials (user_id);

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS active_llm_credential_id INTEGER;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_app_users_active_llm_credential_id'
    ) THEN
        ALTER TABLE app_users
            ADD CONSTRAINT fk_app_users_active_llm_credential_id
            FOREIGN KEY (active_llm_credential_id)
            REFERENCES user_llm_credentials (id)
            ON DELETE SET NULL;
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS ix_app_users_active_llm_credential_id ON app_users (active_llm_credential_id);

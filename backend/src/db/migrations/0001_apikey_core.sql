-- APIKey 分发系统核心表（见 doc/apikey-distribution-plan.md §3）
-- 金额一律用整数微美元(micro-usd, BIGINT)，$1 = 1_000_000，避免浮点误差。

-- 1) 用户账号（自助注册）
CREATE TABLE IF NOT EXISTS ak_users (
    id                BIGSERIAL PRIMARY KEY,
    email             TEXT        NOT NULL UNIQUE,
    password_hash     TEXT        NOT NULL,
    role              TEXT        NOT NULL DEFAULT 'user',    -- user | admin
    status            TEXT        NOT NULL DEFAULT 'active',  -- active | disabled
    balance_micro_usd BIGINT      NOT NULL DEFAULT 0,         -- 余额，注册时充 $20
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2) 下游用户令牌（只存 hash；扣费走 ak_users.balance）
CREATE TABLE IF NOT EXISTS ak_api_keys (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT      NOT NULL REFERENCES ak_users(id) ON DELETE CASCADE,
    name                TEXT        NOT NULL DEFAULT 'default',
    key_prefix          TEXT        NOT NULL,                  -- 展示用，如 sk-substantia-AbC1…
    key_hash            TEXT        NOT NULL UNIQUE,           -- sha256(明文)
    status              TEXT        NOT NULL DEFAULT 'active', -- active | disabled | revoked
    quota_cap_micro_usd BIGINT,                               -- 单 key 封顶；NULL=不限
    spent_micro_usd     BIGINT      NOT NULL DEFAULT 0,        -- 该 key 累计花费（配合 quota_cap）
    rate_limit_rpm      INT,                                  -- 每分钟请求上限；NULL=不限
    allowed_models      JSONB,                                -- 模型白名单；NULL/[]=全部
    expires_at          TIMESTAMPTZ,
    last_used_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ak_api_keys_user ON ak_api_keys(user_id);

-- 3) 逐模型定价（计费核心）
CREATE TABLE IF NOT EXISTS ak_model_prices (
    id                      BIGSERIAL PRIMARY KEY,
    model                   TEXT        NOT NULL UNIQUE,
    display_name            TEXT,
    input_micro_usd_per_1k  BIGINT      NOT NULL DEFAULT 0,   -- 输入每 1k token 微美元
    output_micro_usd_per_1k BIGINT      NOT NULL DEFAULT 0,   -- 输出每 1k token 微美元
    enabled                 BOOLEAN     NOT NULL DEFAULT true,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4) 加额度/充值申请（$20 试用是注册自动给的，不走这里）
CREATE TABLE IF NOT EXISTS ak_topup_requests (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT      NOT NULL REFERENCES ak_users(id) ON DELETE CASCADE,
    requested_micro_usd BIGINT      NOT NULL,
    reason              TEXT,
    status              TEXT        NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    review_note         TEXT,
    reviewed_by         BIGINT      REFERENCES ak_users(id),
    reviewed_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ak_topups_status ON ak_topup_requests(status);

-- 注：上游凭据(slot=subscription/api_key)、容器编排、HRW 路由由「容器团队」的
-- services/claude/* 负责（slot 持久化在 slots.json，见 doc/claude-docker-plan.md）。
-- 本系统不再建 ak_credentials/ak_containers，admin 通过 services.claude.store/registry
-- 管理 slot 池。usage 日志只记录命中的 slot_id（TEXT）。

-- 5) 用量/计费日志
CREATE TABLE IF NOT EXISTS ak_usage_logs (
    id                BIGSERIAL PRIMARY KEY,
    api_key_id        BIGINT,
    user_id           BIGINT,
    slot_id           TEXT,                                  -- 命中的上游 slot（容器团队的凭据身份）
    model             TEXT,                                  -- 实际命中模型（计价依据）
    prompt_tokens     INT         NOT NULL DEFAULT 0,
    completion_tokens INT         NOT NULL DEFAULT 0,
    total_tokens      INT         NOT NULL DEFAULT 0,
    cost_micro_usd    BIGINT      NOT NULL DEFAULT 0,
    latency_ms        INT,
    attempts          INT         NOT NULL DEFAULT 1,        -- exec_claude 故障转移尝试次数
    status            TEXT,                                  -- ok | error
    error_code        TEXT,
    request_id        TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ak_usage_user ON ak_usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_ak_usage_key ON ak_usage_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_ak_usage_created ON ak_usage_logs(created_at);

-- 预置几个常见模型的占位价（admin 可在后台改）。单位：微美元 / 1k token。
INSERT INTO ak_model_prices (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k)
VALUES
    ('claude-opus-4',     'Claude Opus 4',   15000, 75000),
    ('claude-sonnet-4',   'Claude Sonnet 4',  3000, 15000),
    ('claude-haiku-4',    'Claude Haiku 4',    800,  4000),
    ('glm-4.6',           'GLM-4.6',           600,  2200),
    ('qwen-max',          'Qwen Max',         1600,  6400)
ON CONFLICT (model) DO NOTHING;

-- 墨行供应商资金与请求级利润对账。
-- 所有内部结算统一使用 micro-USD；人民币充值保留原币金额与入账汇率快照。

ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS supplier TEXT;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS upstream_model TEXT;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS official_cost_micro_usd BIGINT NOT NULL DEFAULT 0;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS supplier_cost_micro_usd BIGINT NOT NULL DEFAULT 0;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS supplier_multiplier NUMERIC(10,4);
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS sale_multiplier NUMERIC(10,4);
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS user_multiplier NUMERIC(10,4) NOT NULL DEFAULT 1;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS charged_paid_micro_usd BIGINT NOT NULL DEFAULT 0;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS charged_trial_micro_usd BIGINT NOT NULL DEFAULT 0;
ALTER TABLE ak_usage_logs ADD COLUMN IF NOT EXISTS supplier_accounting_status TEXT;

CREATE INDEX IF NOT EXISTS idx_ak_usage_supplier_created
    ON ak_usage_logs(supplier, created_at);

CREATE TABLE IF NOT EXISTS ak_supplier_accounts (
    supplier                   TEXT PRIMARY KEY,
    balance_micro_usd          BIGINT NOT NULL DEFAULT 0,
    tracking_started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ak_supplier_model_terms (
    supplier                              TEXT NOT NULL REFERENCES ak_supplier_accounts(supplier),
    model                                 TEXT NOT NULL,
    display_name                          TEXT,
    official_input_micro_usd_per_1k       BIGINT NOT NULL,
    official_output_micro_usd_per_1k      BIGINT NOT NULL,
    official_cache_read_micro_usd_per_1k  BIGINT NOT NULL,
    official_cache_write_micro_usd_per_1k BIGINT NOT NULL,
    supplier_multiplier                   NUMERIC(10,4) NOT NULL DEFAULT 1,
    sale_multiplier                       NUMERIC(10,4) NOT NULL DEFAULT 1,
    updated_by                            BIGINT,
    updated_at                            TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (supplier, model),
    CHECK (supplier_multiplier >= 0 AND supplier_multiplier <= 100),
    CHECK (sale_multiplier >= 0 AND sale_multiplier <= 100)
);

CREATE TABLE IF NOT EXISTS ak_supplier_ledger (
    id                    BIGSERIAL PRIMARY KEY,
    supplier              TEXT NOT NULL REFERENCES ak_supplier_accounts(supplier),
    entry_type             TEXT NOT NULL CHECK (entry_type IN ('topup', 'usage', 'adjustment')),
    amount_micro_usd       BIGINT NOT NULL,
    balance_after_micro_usd BIGINT NOT NULL,
    usage_log_id           BIGINT UNIQUE REFERENCES ak_usage_logs(id) ON DELETE SET NULL,
    model                  TEXT,
    request_id             TEXT,
    original_amount        NUMERIC(18,4),
    original_currency      TEXT CHECK (original_currency IN ('USD', 'RMB')),
    fx_rate                NUMERIC(18,8),
    reference              TEXT,
    note                   TEXT,
    created_by             BIGINT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ak_supplier_ledger_created
    ON ak_supplier_ledger(supplier, created_at DESC);

CREATE TABLE IF NOT EXISTS ak_supplier_balance_snapshots (
    id                        BIGSERIAL PRIMARY KEY,
    supplier                  TEXT NOT NULL REFERENCES ak_supplier_accounts(supplier),
    reported_balance_micro_usd BIGINT NOT NULL,
    original_amount           NUMERIC(18,4) NOT NULL,
    original_currency         TEXT NOT NULL CHECK (original_currency IN ('USD', 'RMB')),
    fx_rate                   NUMERIC(18,8) NOT NULL,
    as_of                     TIMESTAMPTZ NOT NULL DEFAULT now(),
    note                      TEXT,
    created_by                BIGINT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ak_supplier_snapshots_asof
    ON ak_supplier_balance_snapshots(supplier, as_of DESC);

INSERT INTO ak_supplier_accounts (supplier) VALUES ('moxing')
ON CONFLICT (supplier) DO NOTHING;

-- 官网价单位是 micro-USD / 1k token。供应商折扣默认 1.0，避免未配置商务价时低估成本；
-- 销售折扣延续当前 GLM 八折、Kimi 原价，后台可随时调整并同步到 ak_model_prices。
INSERT INTO ak_supplier_model_terms
    (supplier, model, display_name,
     official_input_micro_usd_per_1k, official_output_micro_usd_per_1k,
     official_cache_read_micro_usd_per_1k, official_cache_write_micro_usd_per_1k,
     supplier_multiplier, sale_multiplier)
VALUES
    ('moxing', 'glm-5.2', 'GLM 5.2', 1400, 4400, 260, 1400, 1.0, 0.8),
    ('moxing', 'kimi-k3', 'Kimi K3', 3000, 15000, 300, 3000, 1.0, 1.0)
ON CONFLICT (supplier, model) DO NOTHING;

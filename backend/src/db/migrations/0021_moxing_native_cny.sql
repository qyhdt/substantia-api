-- 墨行按人民币固定单价结算。新增原生 micro-CNY 账本，避免 USD/CNY 汇率造成逐笔对账偏差。

ALTER TABLE ak_supplier_model_terms
    ADD COLUMN IF NOT EXISTS official_input_micro_cny_per_million BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS official_output_micro_cny_per_million BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS official_cache_read_micro_cny_per_million BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS official_cache_write_micro_cny_per_million BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS pricing_fx_rate NUMERIC(18,8) NOT NULL DEFAULT 6.7648;

-- 2026-07-22 墨行公开模型广场人民币价：GLM 8/28/2；Kimi 20/100/2。
-- 未单列缓存写入价时按普通输入价结算。
UPDATE ak_supplier_model_terms SET
    official_input_micro_cny_per_million = 8000000,
    official_output_micro_cny_per_million = 28000000,
    official_cache_read_micro_cny_per_million = 2000000,
    official_cache_write_micro_cny_per_million = 8000000,
    pricing_fx_rate = 6.7648
WHERE supplier = 'moxing' AND model = 'glm-5.2';

UPDATE ak_supplier_model_terms SET
    official_input_micro_cny_per_million = 20000000,
    official_output_micro_cny_per_million = 100000000,
    official_cache_read_micro_cny_per_million = 2000000,
    official_cache_write_micro_cny_per_million = 20000000,
    pricing_fx_rate = 6.7648
WHERE supplier = 'moxing' AND model = 'kimi-k3';

ALTER TABLE ak_usage_logs
    ADD COLUMN IF NOT EXISTS cache_read_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_write_tokens BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS official_cost_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS supplier_cost_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sales_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS charged_paid_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS charged_trial_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS billing_fx_rate NUMERIC(18,8);

-- 0020 只保存了总 token；墨行直连的差额就是缓存读取 token。0021 后逐笔保存真实拆分。
UPDATE ak_usage_logs
SET cache_read_tokens = greatest(total_tokens - prompt_tokens - completion_tokens, 0)
WHERE supplier = 'moxing' AND cache_read_tokens = 0 AND cache_write_tokens = 0;

ALTER TABLE ak_supplier_accounts
    ADD COLUMN IF NOT EXISTS balance_micro_cny BIGINT NOT NULL DEFAULT 0;

ALTER TABLE ak_supplier_ledger
    ADD COLUMN IF NOT EXISTS amount_micro_cny BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS balance_after_micro_cny BIGINT NOT NULL DEFAULT 0;

-- 重算 migration 0020 上线以来的墨行请求，修正此前误按美元价记录的供应商成本。
WITH priced AS (
    SELECT l.id,
           (
               ceil(l.prompt_tokens::numeric * t.official_input_micro_cny_per_million / 1000000)
             + ceil(l.completion_tokens::numeric * t.official_output_micro_cny_per_million / 1000000)
             + ceil(coalesce(l.cache_read_tokens, 0)::numeric * t.official_cache_read_micro_cny_per_million / 1000000)
             + ceil(coalesce(l.cache_write_tokens, 0)::numeric * t.official_cache_write_micro_cny_per_million / 1000000)
           )::bigint AS official_cny,
           t.supplier_multiplier
    FROM ak_usage_logs l
    JOIN ak_supplier_model_terms t
      ON t.supplier = 'moxing' AND t.model = l.upstream_model
    WHERE l.supplier = 'moxing'
)
UPDATE ak_usage_logs l SET
    official_cost_micro_cny = p.official_cny,
    supplier_cost_micro_cny = round(p.official_cny * p.supplier_multiplier)::bigint,
    sales_micro_cny = round(l.cost_micro_usd * 6.7648)::bigint,
    charged_paid_micro_cny = round(l.charged_paid_micro_usd * 6.7648)::bigint,
    charged_trial_micro_cny = round(l.charged_trial_micro_usd * 6.7648)::bigint,
    billing_fx_rate = 6.7648
FROM priced p WHERE p.id = l.id;

-- 旧资金流水转换为人民币；usage 流水直接取重算后的供应商成本。
UPDATE ak_supplier_ledger ledger
SET amount_micro_cny = -coalesce(usage.supplier_cost_micro_cny, 0)
FROM ak_usage_logs usage
WHERE ledger.entry_type = 'usage' AND ledger.usage_log_id = usage.id;

-- 上一条 UPDATE 的 FROM 会漏掉无 usage_log_id 的资金项，单独补齐。
UPDATE ak_supplier_ledger SET amount_micro_cny = CASE
    WHEN original_currency = 'RMB' THEN round(original_amount * 1000000)::bigint
    WHEN original_currency = 'USD' THEN round(original_amount * 6.7648 * 1000000)::bigint
    ELSE round(amount_micro_usd * 6.7648)::bigint
END
WHERE entry_type <> 'usage';

WITH running AS (
    SELECT id, sum(amount_micro_cny) OVER (PARTITION BY supplier ORDER BY created_at, id) AS balance
    FROM ak_supplier_ledger
)
UPDATE ak_supplier_ledger ledger
SET balance_after_micro_cny = running.balance
FROM running WHERE running.id = ledger.id;

UPDATE ak_supplier_accounts account
SET balance_micro_cny = coalesce((
    SELECT sum(amount_micro_cny) FROM ak_supplier_ledger ledger WHERE ledger.supplier = account.supplier
), 0), updated_at = now()
WHERE supplier = 'moxing';

-- 兼容旧的 micro-USD 客户价表；真实请求会按人民币销售价和请求时汇率动态换算。
UPDATE ak_model_prices p SET
    input_micro_usd_per_1k = round(t.official_input_micro_cny_per_million * t.sale_multiplier / t.pricing_fx_rate / 1000)::bigint,
    output_micro_usd_per_1k = round(t.official_output_micro_cny_per_million * t.sale_multiplier / t.pricing_fx_rate / 1000)::bigint,
    cache_read_micro_usd_per_1k = round(t.official_cache_read_micro_cny_per_million * t.sale_multiplier / t.pricing_fx_rate / 1000)::bigint,
    cache_write_micro_usd_per_1k = round(t.official_cache_write_micro_cny_per_million * t.sale_multiplier / t.pricing_fx_rate / 1000)::bigint,
    updated_at = now()
FROM ak_supplier_model_terms t
WHERE t.supplier = 'moxing' AND t.model = p.model;

-- 缓存 token 分档计价（对齐 Anthropic 官方 prompt caching 价）。
-- 此前 runner 把 cache_read_input_tokens 当普通 input 全价计，导致多轮 Agent 场景被多收
-- （cache_read 实际官方仅收 10%，cache_creation 收 125%）。本迁移新增两列单独定价：
--   cache_read_micro_usd_per_1k  = 缓存命中读取价（官方 ≈ 输入价 × 0.10）
--   cache_write_micro_usd_per_1k = 缓存写入/创建价（官方 ≈ 输入价 × 1.25）
-- 单位与 input/output 一致：微美元 / 1k token。admin 可在后台改。
ALTER TABLE ak_model_prices
    ADD COLUMN IF NOT EXISTS cache_read_micro_usd_per_1k  BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS cache_write_micro_usd_per_1k BIGINT NOT NULL DEFAULT 0;

-- 回填：按当前输入价派生官方比例（read 10% / write 125%），四舍五入到整数微美元。
-- 只回填仍为 0 的行，避免覆盖 admin 手工设过的值。
UPDATE ak_model_prices
   SET cache_read_micro_usd_per_1k  = round(input_micro_usd_per_1k * 0.10),
       cache_write_micro_usd_per_1k = round(input_micro_usd_per_1k * 1.25),
       updated_at = now()
 WHERE cache_read_micro_usd_per_1k = 0
   AND cache_write_micro_usd_per_1k = 0;

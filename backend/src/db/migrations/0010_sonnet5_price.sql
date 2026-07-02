-- 新增 Claude Sonnet 5 定价（claude-fable-5 已在 0006 落库）。
-- 官方标准价 $3 / $15 每百万 token（2026-08-31 前有 $2/$10 优惠价，此处按标准价的 50% 收，
-- 想跟进优惠价可在 admin 后台改）。缓存价按官方比例：read 10% / write 125% 输入价。
-- 表单位：微美元 / 1k token。下面已是 50% 实付价。
INSERT INTO ak_model_prices
    (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k,
     cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k)
VALUES
    ('claude-sonnet-5', 'Claude Sonnet 5', 1500, 7500, 150, 1875)
ON CONFLICT (model) DO UPDATE SET
    display_name                 = EXCLUDED.display_name,
    input_micro_usd_per_1k       = EXCLUDED.input_micro_usd_per_1k,
    output_micro_usd_per_1k      = EXCLUDED.output_micro_usd_per_1k,
    cache_read_micro_usd_per_1k  = EXCLUDED.cache_read_micro_usd_per_1k,
    cache_write_micro_usd_per_1k = EXCLUDED.cache_write_micro_usd_per_1k,
    updated_at                   = now();

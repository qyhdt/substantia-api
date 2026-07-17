-- ChatGPT（gpt-* / o3）定价：官方 API 价的 50% 实付，与 Claude 同口径。
-- 表单位：微美元 / 1k token。换算：官方 $X/百万 token × 500 = 50% 实付的 微美元/1k。
-- 缓存价按官方比例：read≈10% 输入价、write≈125% 输入价。admin 后台可随时改/停。
-- 计价必须有价格行，否则按 0 收（见 services/apikey/pricing.py 的告警）。
INSERT INTO ak_model_prices
    (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k,
     cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k)
VALUES
    ('gpt-5',        'GPT-5',        625,  5000, 63,  781),
    ('gpt-5-mini',   'GPT-5 mini',   125,  1000, 13,  156),
    ('gpt-5-nano',   'GPT-5 nano',   25,   200,  3,   31),
    ('o3',           'OpenAI o3',    1000, 4000, 100, 1250),
    ('gpt-4o',       'GPT-4o',       1250, 5000, 125, 1563),
    ('gpt-4o-mini',  'GPT-4o mini',  75,   300,  8,   94),
    ('gpt-4.1',      'GPT-4.1',      1000, 4000, 100, 1250),
    ('gpt-4.1-mini', 'GPT-4.1 mini', 200,  800,  20,  250)
ON CONFLICT (model) DO UPDATE SET
    display_name                 = EXCLUDED.display_name,
    input_micro_usd_per_1k       = EXCLUDED.input_micro_usd_per_1k,
    output_micro_usd_per_1k      = EXCLUDED.output_micro_usd_per_1k,
    cache_read_micro_usd_per_1k  = EXCLUDED.cache_read_micro_usd_per_1k,
    cache_write_micro_usd_per_1k = EXCLUDED.cache_write_micro_usd_per_1k,
    updated_at                   = now();

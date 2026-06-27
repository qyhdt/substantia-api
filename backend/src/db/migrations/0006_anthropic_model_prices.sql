-- 对齐 Anthropic 当前官方 model ID（不带日期后缀）+ 官方价的 50%。
-- 官方价（每百万 token，输入/输出）：
--   claude-opus-4-8   $5 / $25
--   claude-sonnet-4-6 $3 / $15
--   claude-haiku-4-5  $1 / $5
--   claude-fable-5    $10 / $50
-- 表单位：微美元 / 1k token（$1=1_000_000，所以 $5/1M = 5000/1k）。下面已是 50% 实付价。
INSERT INTO ak_model_prices (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k)
VALUES
    ('claude-opus-4-8',   'Claude Opus 4.8',   2500, 12500),
    ('claude-sonnet-4-6', 'Claude Sonnet 4.6', 1500,  7500),
    ('claude-haiku-4-5',  'Claude Haiku 4.5',   500,  2500),
    ('claude-fable-5',    'Claude Fable 5',    5000, 25000)
ON CONFLICT (model) DO UPDATE SET
    display_name            = EXCLUDED.display_name,
    input_micro_usd_per_1k  = EXCLUDED.input_micro_usd_per_1k,
    output_micro_usd_per_1k = EXCLUDED.output_micro_usd_per_1k,
    updated_at              = now();

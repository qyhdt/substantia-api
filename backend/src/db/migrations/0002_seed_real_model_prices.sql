-- 用真实命中模型名补充定价（claude --output-format json 的 modelUsage key 带版本号）。
-- 计费按实际命中模型（见 services/apikey/runner.py 的 modelUsage 解析）。
-- 单位：微美元 / 1k token（$1/M token = 1000 micro/1k）。admin 可在后台改。
INSERT INTO ak_model_prices (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k)
VALUES
    ('claude-opus-4-8',            'Claude Opus 4.8',   15000, 75000),
    ('claude-sonnet-4-5',          'Claude Sonnet 4.5',  3000, 15000),
    ('claude-haiku-4-5-20251001',  'Claude Haiku 4.5',   1000,  5000)
ON CONFLICT (model) DO NOTHING;

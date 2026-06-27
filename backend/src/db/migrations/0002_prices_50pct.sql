-- 全站「官网价 5 折」：把 ak_model_prices 从官网全价改为 50% 实付价。
-- 单位：微美元 / 1k token（$1 = 1_000_000）。官网价见注释。
-- 落地页只做展示（官网价划线 + 实付加粗），真实计费按本表。

-- claude-opus-4   官网 $15 / $75  → 实付 $7.5 / $37.5
UPDATE ak_model_prices SET input_micro_usd_per_1k = 7500,  output_micro_usd_per_1k = 37500 WHERE model = 'claude-opus-4';
-- claude-sonnet-4 官网 $3 / $15    → 实付 $1.5 / $7.5
UPDATE ak_model_prices SET input_micro_usd_per_1k = 1500,  output_micro_usd_per_1k = 7500  WHERE model = 'claude-sonnet-4';
-- claude-haiku-4  官网 $0.8 / $4   → 实付 $0.4 / $2
UPDATE ak_model_prices SET input_micro_usd_per_1k = 400,   output_micro_usd_per_1k = 2000  WHERE model = 'claude-haiku-4';

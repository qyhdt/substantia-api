-- 全站「官网价 5 折」：把所有 Claude 模型的计价改为官网价的 50%（实付价）。
-- 单位：微美元 / 1k token。落地页展示官网价划线 + 实付加粗；真实计费按本表。
-- 通用半价（覆盖现有及 seed 进来的全部 claude-* 模型，如 claude-opus-4-8 / sonnet-4-5 / haiku-4-5）。
-- 注：非 Anthropic 模型（glm / qwen 等）不在此折扣内，按各自设定计价。
UPDATE ak_model_prices
   SET input_micro_usd_per_1k  = input_micro_usd_per_1k  / 2,
       output_micro_usd_per_1k = output_micro_usd_per_1k / 2,
       updated_at = now()
 WHERE model LIKE 'claude-%';

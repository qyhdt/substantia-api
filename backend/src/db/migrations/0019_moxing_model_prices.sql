-- moxing 公开模型定价（美元 / 百万 token）：
--   GLM 5.2 官网 $1.40 / $4.40，本站 8 折 = $1.12 / $3.52；缓存官网 $0.26，8 折 = $0.208。
--   Kimi K3 因全网资源短缺按官网原价 $3.00 / $15.00；缓存原价 $0.30。
-- 表单位：微美元 / 1k token。首次缓存创建按普通输入价，后续命中按 cached input 价。
INSERT INTO ak_model_prices
    (model, display_name, input_micro_usd_per_1k, output_micro_usd_per_1k,
     cache_read_micro_usd_per_1k, cache_write_micro_usd_per_1k)
VALUES
    ('glm-5.2', 'GLM 5.2', 1120, 3520, 208, 1120),
    ('kimi-k3', 'Kimi K3', 3000, 15000, 300, 3000)
ON CONFLICT (model) DO UPDATE SET
    display_name                 = EXCLUDED.display_name,
    input_micro_usd_per_1k       = EXCLUDED.input_micro_usd_per_1k,
    output_micro_usd_per_1k      = EXCLUDED.output_micro_usd_per_1k,
    cache_read_micro_usd_per_1k  = EXCLUDED.cache_read_micro_usd_per_1k,
    cache_write_micro_usd_per_1k = EXCLUDED.cache_write_micro_usd_per_1k,
    updated_at                   = now();

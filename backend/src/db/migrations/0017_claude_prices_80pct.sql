-- 全站 Claude 定价由官网价 5 折调整为 8 折。
-- 现有 claude-* 价格均由 0003/0006/0010 按官网价 50% 写入，因此乘以 8/5
-- 即得到官网价 80%；缓存读写价格保持相同倍率关系。
-- 用户级 price_multiplier 仍会在此基础价之上叠加。
UPDATE ak_model_prices
   SET input_micro_usd_per_1k       = input_micro_usd_per_1k       * 8 / 5,
       output_micro_usd_per_1k      = output_micro_usd_per_1k      * 8 / 5,
       cache_read_micro_usd_per_1k  = cache_read_micro_usd_per_1k  * 8 / 5,
       cache_write_micro_usd_per_1k = cache_write_micro_usd_per_1k * 8 / 5,
       updated_at                   = now()
 WHERE model LIKE 'claude-%';

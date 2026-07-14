-- 按用户折扣系数：计费时 实扣 = 模型价 × price_multiplier。
--   1.0 = 原价（默认）；0.5 = 5 折；1.3 = 上浮 1.3 倍；0 = 免费。
ALTER TABLE ak_users
    ADD COLUMN IF NOT EXISTS price_multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0;

-- 试用额度分桶：注册送的 $20 进试用桶（有效期 3 个月），与实付桶（balance）分开。
-- 消费先扣试用桶；试用到期且未转永久则失效；充值≥$1（在有效期内）把试用桶转永久。
-- 既有用户：trial_micro_usd 默认 0 → 不受影响，他们的 balance 视为实付（永久）。
ALTER TABLE ak_users ADD COLUMN IF NOT EXISTS trial_micro_usd  BIGINT      NOT NULL DEFAULT 0;
ALTER TABLE ak_users ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMPTZ;
ALTER TABLE ak_users ADD COLUMN IF NOT EXISTS trial_permanent  BOOLEAN     NOT NULL DEFAULT false;

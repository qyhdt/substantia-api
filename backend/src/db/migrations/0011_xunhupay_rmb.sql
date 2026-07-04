-- 虎皮椒充值：记录实际向虎皮椒收取的人民币金额（对账用；余额仍以 amount_micro_usd 美元口径到账）。
ALTER TABLE ak_payments ADD COLUMN IF NOT EXISTS amount_rmb NUMERIC(12,2);

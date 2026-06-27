-- 自助充值订单（Polar）。webhook 回调按 out_trade_no 幂等加余额。
CREATE TABLE IF NOT EXISTS ak_payments (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT      NOT NULL REFERENCES ak_users(id) ON DELETE CASCADE,
    provider         TEXT        NOT NULL DEFAULT 'polar',
    out_trade_no     TEXT        NOT NULL UNIQUE,            -- 我方订单号，放进 Polar metadata 原样回传
    amount_micro_usd BIGINT      NOT NULL,                   -- 充值金额（微美元，下单时确定）
    status           TEXT        NOT NULL DEFAULT 'pending', -- pending | paid
    provider_ref     TEXT,                                   -- Polar order/checkout id
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ak_payments_user ON ak_payments(user_id, created_at DESC);

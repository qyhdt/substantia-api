-- 注册赠送去重：记录每次发放试用额度（$20）时的设备指纹 / IP。
-- 同一设备(device_id)或同一 IP 已领过 → 之后注册不再送，防止清缓存反复注册薅额度。
CREATE TABLE IF NOT EXISTS ak_signup_grants (
    id                bigserial   PRIMARY KEY,
    user_id           bigint      NOT NULL,
    device_id         text,
    ip                text,
    granted_micro_usd bigint      NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- 只对「实际发过钱」的行建去重索引（granted>0），加速命中判断
CREATE INDEX IF NOT EXISTS idx_ak_signup_grants_device
    ON ak_signup_grants (device_id) WHERE device_id IS NOT NULL AND granted_micro_usd > 0;
CREATE INDEX IF NOT EXISTS idx_ak_signup_grants_ip
    ON ak_signup_grants (ip) WHERE ip IS NOT NULL AND granted_micro_usd > 0;

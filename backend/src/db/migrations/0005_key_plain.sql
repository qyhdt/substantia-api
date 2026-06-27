-- 存 key 明文，供本人在 portal 里取回、自动填入测试 curl。
-- 鉴权仍走 key_hash（sha256）；key_plain 仅本人可见。老 key 无明文（NULL），需重新生成才能自动填入。
ALTER TABLE ak_api_keys ADD COLUMN IF NOT EXISTS key_plain TEXT;

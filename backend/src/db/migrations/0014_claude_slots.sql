-- claude_slots：把 slot（上游账号）配置从"扫本机目录/slots.json"搬进 DB，按 server_ip 分片。
-- 一行 = 某 IP 服务器上的一个账号；creds_json 存 subscription 的 .credentials.json 完整内容。
-- 目的：统一账号出口 IP（账号绑定服务器）+ 多节点分片的地基。
CREATE TABLE IF NOT EXISTS claude_slots (
  id               BIGSERIAL PRIMARY KEY,
  server_ip        TEXT        NOT NULL,
  slot_id          TEXT        NOT NULL,
  type             TEXT        NOT NULL DEFAULT 'subscription',
  enabled          BOOLEAN     NOT NULL DEFAULT true,
  weight           DOUBLE PRECISION NOT NULL DEFAULT 1.0,
  image            TEXT        NOT NULL DEFAULT '',
  creds_json       TEXT        NOT NULL DEFAULT '',
  env              JSONB       NOT NULL DEFAULT '{}',
  account_email    TEXT        NOT NULL DEFAULT '',
  note             TEXT        NOT NULL DEFAULT '',
  creds_synced_at  TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (server_ip, slot_id)
);
CREATE INDEX IF NOT EXISTS idx_claude_slots_server_enabled ON claude_slots (server_ip) WHERE enabled;

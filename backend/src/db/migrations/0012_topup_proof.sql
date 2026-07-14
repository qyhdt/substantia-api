-- 人工充值申请增加「转账凭证」字段（截图 URL）。用户提交申请时可上传转账凭证，
-- admin 审核时可查看。存的是后端静态服务的相对 URL（/api/uploads/<file>）。
ALTER TABLE ak_topup_requests ADD COLUMN IF NOT EXISTS proof_url TEXT;

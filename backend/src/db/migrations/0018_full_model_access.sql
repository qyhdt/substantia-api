-- 全模型权限标签：真实人工充值或自助支付用户为 true；免费试用/后台赠送默认为 false。
ALTER TABLE ak_users
    ADD COLUMN IF NOT EXISTS full_model_access BOOLEAN NOT NULL DEFAULT false;

-- 兼容既有数据：已审核通过的人工充值、已支付的 Polar/虎皮椒订单均补为全模型用户。
UPDATE ak_users u
   SET full_model_access = true
 WHERE EXISTS (
           SELECT 1 FROM ak_topup_requests t
            WHERE t.user_id = u.id AND t.status = 'approved'
       )
    OR EXISTS (
           SELECT 1 FROM ak_payments p
            WHERE p.user_id = u.id AND p.status = 'paid'
       );

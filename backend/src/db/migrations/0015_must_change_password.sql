-- 强制改密：admin 建的用户默认密码 123456，首次登录必须改密才能进。
ALTER TABLE ak_users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

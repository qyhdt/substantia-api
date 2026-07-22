# -*- coding: utf-8 -*-
"""
集中读取环境变量。业务代码统一从 `settings` 单例取值，禁止再散落 os.getenv。

依赖：pydantic-settings >= 2。
"""
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- HTTP ----------
    HOST: str = "0.0.0.0"
    PORT: int = 9999

    # 慢请求阈值（秒），中间件根据它写 latency.log
    REQUEST_LATENCY_THRESHOLD: float = 2.0

    # 中间件是否把请求参数打到日志（默认关闭，避免泄露）
    LOG_REQUEST_PARAMS: bool = False

    # 是否把封装后发往 api.anthropic.com 的完整请求体落审计日志（全量不截断，gzip 按天轮转保留 30 天）
    LOG_UPSTREAM_BODY: bool = True

    # 业务侧若希望把额外请求头并入 request_context，列在这里（CSV 字符串，取列表用 extra_context_headers_list）
    EXTRA_CONTEXT_HEADERS: str = ""

    # ---------- 注册/登录：图形验证码 + 邮箱验证码 ----------
    # 注册/登录是否要求图形验证码（自建 SVG，无外部依赖）。默认开。
    CAPTCHA_REQUIRED: bool = True
    # 邮箱验证码 SMTP（Gmail 应用专用密码）。HOST+USER+PASS 齐全即视为「已配置」，注册自动要求邮箱验证。
    SMTP_HOST: str = ""              # 如 smtp.gmail.com
    SMTP_PORT: int = 587            # 587 = STARTTLS；465 = SSL
    SMTP_USER: str = ""             # 如 qqyhdt@gmail.com
    SMTP_PASS: str = ""             # 16 位应用专用密码（非账号密码）
    SMTP_FROM: str = ""             # 发件人，留空回落 SMTP_USER
    SMTP_FROM_NAME: str = "Substantia"   # 发件人显示名
    EMAIL_CODE_TTL: int = 600        # 验证码有效期（秒），默认 10 分钟
    EMAIL_CODE_RESEND_SECONDS: int = 60  # 同邮箱最短重发间隔（秒）
    # 强制要求注册邮箱验证：true=必须（即使没配 SMTP 也拦）；false=未配 SMTP 时自动跳过（平滑上线）
    EMAIL_VERIFY_REQUIRED: bool = False

    # ---------- CORS ----------
    # CSV 字符串；取列表用 settings.cors_origins_list
    CORS_ORIGINS: str = ""
    CORS_ORIGIN_REGEX: str = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    # ---------- 鉴权 ----------
    JWT_SECRET: str = "CHANGE_ME_TO_ENV"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_HOURS: int = 24 * 60   # 60 天
    AUTH_TOKEN_EXPIRE_HOURS: int = 24 * 60      # 60 天
    # 关闭鉴权（仅本地/测试用），设为 true 后 require_access_token 返回 dummy user
    AUTH_DISABLED: bool = False
    # 前端 cookie 模式下携带 access token 的 cookie 名（浏览器自动带）
    AUTH_COOKIE: str = "substantia_access_token"

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_POOL_MAX_CONNECTIONS: int = 200
    REDIS_BG_WRITE_CONCURRENCY: int = 50

    # ---------- PostgreSQL ----------
    DATABASE_URL: str = ""
    DB_POOL_MIN_SIZE: int = 1
    DB_POOL_MAX_SIZE: int = 20
    DB_COMMAND_TIMEOUT: int = 60

    # ---------- Claude 容器（slot 编排）----------
    # base 镜像（api_key slot 用；订阅 slot 用各自的预登录镜像 slot.image）
    CLAUDE_BASE_IMAGE: str = "claude-runner"
    # workspace 根目录：host 上 <root>/<slot_id>/ 挂成容器 /workspace；用户目录在其下 users/<uid>/
    CLAUDE_WORKSPACE_ROOT: str = "/var/lib/substantia/claude"
    # 每个 slot 容器的资源上限
    CLAUDE_CONTAINER_MEMORY: str = "3g"
    CLAUDE_CONTAINER_CPUS: float = 2.0
    # 单次 claude exec 超时（秒）
    CLAUDE_EXEC_TIMEOUT: int = 600
    # slot 池配置（JSON 数组）；留空 = 空池，需先配 slot 才能路由。仅作初始 seed，admin 改动落到 slots 文件。
    CLAUDE_SLOTS_JSON: str = ""
    # slot 持久化文件（admin CRUD 写这里）；留空 = <CLAUDE_WORKSPACE_ROOT>/slots.json
    CLAUDE_SLOTS_FILE: str = ""
    # 共享账号池目录（与境核AI/小智账号池同源）：配了则每个 <dir>/<acc>/.credentials.json = 一个订阅 slot，
    # 自动轮询。小智后台 add-account.sh 新增账号，这里免重启即纳入（probe_loop 周期重扫）。留空 = 用 slots.json。
    CLAUDE_SHARED_ACCOUNTS_DIR: str = ""
    # 共享账号 slot 用的基础镜像（凭据由 creds_dir 挂载覆盖，镜像只需带 claude CLI）
    CLAUDE_SHARED_ACCOUNTS_IMAGE: str = ""
    # slot 容器名前缀：容器名 = <prefix><slot_id>。多套栈同机时改前缀避免撞名。
    CLAUDE_CONTAINER_PREFIX: str = "claude-slot-"
    # 独立账号池目录（本栈自有，非共享）：每个 <dir>/<acc>/.credentials.json = 一个订阅 slot。
    # 与 CLAUDE_SHARED_ACCOUNTS_DIR 类似但归本栈独占；后台交互式登录新增账号写这里。留空 = 不用。
    CLAUDE_ACCOUNTS_DIR: str = ""
    # slot 来源：dir=扫账号目录/slots.json；db=从 claude_slots 表按 CLAUDE_NODE_IP 分片加载。
    CLAUDE_SLOTS_SOURCE: str = "dir"
    # 路由策略：round_robin | hrw（会话粘性）。
    CLAUDE_ROUTE_POLICY: str = "round_robin"
    # subscription 池全部不可用后的 api_key 兜底链。档表见 services/claude/registry.py
    # 的 FALLBACK_TIERS（priority 固定：moxing=100、gemini=200），新增档只需在档表登记。
    # 每档三件套（BASE_URL/AUTH_TOKEN/MODEL）都非空才合成对应 slot；未配齐的档自动跳过。
    CLAUDE_FALLBACK_MOXING_BASE_URL: str = ""
    CLAUDE_FALLBACK_MOXING_AUTH_TOKEN: str = ""
    CLAUDE_FALLBACK_MOXING_MODEL: str = ""
    CLAUDE_FALLBACK_GEMINI_BASE_URL: str = ""
    CLAUDE_FALLBACK_GEMINI_AUTH_TOKEN: str = ""
    CLAUDE_FALLBACK_GEMINI_MODEL: str = ""
    # moxing 公开模型（GLM 5.2 / Kimi K3）的 OpenAI/Anthropic 兼容直连。
    # 留空时复用 CLAUDE_FALLBACK_MOXING_BASE_URL/AUTH_TOKEN，避免同一密钥重复配置。
    MOXING_API_BASE: str = ""
    MOXING_API_KEY: str = ""
    # 本节点出口 IP：db 模式下按此 IP 从 claude_slots 取本机负责的账号（账号绑定服务器出口 IP）。
    CLAUDE_NODE_IP: str = ""

    # ---- 健康探针 / 保活 / 故障转移 ----
    # 启动是否拉起所有 enabled slot 容器 + 起健康探针
    CLAUDE_PROBE_ENABLED: bool = True
    # 探针周期（秒）：每隔这么久对每个 slot 真跑一次 claude（订阅档顺带触发 OAuth 续期保活）
    CLAUDE_PROBE_INTERVAL_SECONDS: int = 600
    # 探针/exec 判定为不健康后的故障抑制窗口记录；恢复只由后续探针 mark_healthy 完成。
    CLAUDE_UNHEALTHY_COOLDOWN_SECONDS: int = 600
    # exec 撞 401/鉴权失败时，自动改路由到其它健康 slot 的最大尝试次数
    CLAUDE_EXEC_MAX_ATTEMPTS: int = 3

    # ---------- ChatGPT 上游（两条，均配置门控；对齐 digital-platform--generator）----------
    # 上游 A：ChatGPT 订阅（codex 容器）。每次请求 `docker run --rm codex-runner codex exec --json`，
    #   挂载账号池里某个 <CODEX_ACCOUNTS_DIR>/<acc>/auth.json 的登录态。账号由 admin 网页 device-auth 登录写入。
    #   门控：账号池里有 ≥1 个可用 auth.json 才启用。
    CODEX_IMAGE: str = "codex-runner"                       # 本地镜像（Dockerfile.codex 构建，只带 codex CLI）
    # 与境核AI/小智共用同一份 codex 账号池（同 host 同路径 bind）。留空=用 workspace 下默认。
    CODEX_ACCOUNTS_DIR: str = "/home/work/workspaces/.codex-accounts"
    CODEX_HOME_IN_CONTAINER: str = "/workspace/.codex"      # 账号目录挂进容器的 CODEX_HOME
    CODEX_YOLO_FLAG: str = "--dangerously-bypass-approvals-and-sandbox"
    CODEX_CONTAINER_MEMORY: str = "3g"
    CODEX_CONTAINER_CPUS: float = 2.0
    CODEX_EXEC_TIMEOUT: int = 600                           # 单次 codex exec 超时（秒）
    CODEX_DEFAULT_MODEL: str = "gpt-5"                       # 请求未指明可识别 gpt 型号时的兜底
    # 上游 B：OpenAI 官方 API key（passthrough 到 chat/completions）。留空=不启用该兜底。
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://api.openai.com/v1"

    # ---------- 权限 ----------
    # 拥有 /api/admin/* 权限的用户邮箱白名单（逗号分隔的 CSV 字符串；空 = 没人能用）
    # 取列表请用 settings.admin_emails_list（List[str]）
    ADMIN_EMAILS: str = ""
    # owner（超管）邮箱白名单：admin 之上一层，可做 admin 不能做的高危操作（如设倍率/删账号）。
    # 取列表用 settings.owner_emails_list。owner 自动拥有 admin 权限。
    OWNER_EMAILS: str = ""

    # ---------- 文件上传（转账凭证等）----------
    # 上传文件落盘目录；通过 /api/uploads/<file> 静态回读。
    UPLOAD_DIR: str = "/var/lib/substantia/uploads"

    # ---------- 充值（Polar.sh，海外 MoR，收美元）----------
    # 复用 digital-platform 的 Polar 账号；值放 .env。留空 = 未接入，充值接口返回 503。
    POLAR_ACCESS_TOKEN: str = ""       # Organization Access Token
    POLAR_PRODUCT_ID: str = ""         # 一个 pay-what-you-want 产品 id
    POLAR_WEBHOOK_SECRET: str = ""     # Webhook signing secret（whsec_…）
    POLAR_SANDBOX: bool = False        # true=sandbox-api.polar.sh
    # 支付成功后跳回的站内地址
    PAYMENT_RETURN_URL: str = "https://dev.substantia.ai/"
    # 充值页直达地址（?tab=topups 让前端直接打开「充值」标签页）；用于余额不足时的引导文案
    RECHARGE_URL: str = "https://dev.substantia.ai/?tab=topups"

    # ---------- 充值（虎皮椒 xunhupay，国内个人收款，微信/支付宝，收人民币）----------
    # 在虎皮椒后台「我的支付渠道」拿 AppID / AppSecret，签约成功后填入 .env。留空 = 不显示该渠道。
    XUNHUPAY_APPID: str = ""            # 渠道 AppID
    XUNHUPAY_APPSECRET: str = ""        # 渠道 AppSecret（MD5 签名用）
    XUNHUPAY_API_BASE: str = "https://api.xunhupay.com"   # 接口域名（一般不用改）
    # 用户下单金额仍以美元计（余额是 micro-USD），按此汇率换成人民币向虎皮椒收款。
    XUNHUPAY_RMB_PER_USD: float = 7.2
    # 异步通知 / 同步跳回地址；留空则从 PAYMENT_RETURN_URL 的域名自动推导。
    XUNHUPAY_NOTIFY_URL: str = ""       # 服务器回调 → /api/webhooks/xunhupay
    XUNHUPAY_RETURN_URL: str = ""       # 付款后浏览器跳回（默认用 RECHARGE_URL）

    # ---------- APIKey 分发（下游令牌 / 计费 / 网关）----------
    # 新用户注册自动赠送的余额（微美元，$1 = 1_000_000）。默认 $20。进「试用桶」。
    AK_TRIAL_GRANT_MICRO_USD: int = 20_000_000
    # 试用额度有效期（天）。默认 90（3 个月）。
    AK_TRIAL_EXPIRE_DAYS: int = 90
    # 充值达到该金额（微美元）即把试用额度转为永久有效。默认 $1。
    AK_TRIAL_ACTIVATE_MIN_MICRO_USD: int = 1_000_000
    # 网关请求未显式带 model 时的默认模型（用于计价与 claude --model）
    AK_DEFAULT_MODEL: str = "claude-sonnet-4-6"
    # 余额不足（≤0）时是否拒绝网关请求
    AK_ENFORCE_BALANCE: bool = True

    # ---------- 派生属性 ----------
    @property
    def admin_emails_list(self) -> List[str]:
        return self._csv_to_list(self.ADMIN_EMAILS)

    @property
    def owner_emails_list(self) -> List[str]:
        return self._csv_to_list(self.OWNER_EMAILS)

    @property
    def cors_origins_list(self) -> List[str]:
        return self._csv_to_list(self.CORS_ORIGINS)

    @property
    def extra_context_headers_list(self) -> List[str]:
        return self._csv_to_list(self.EXTRA_CONTEXT_HEADERS)

    @staticmethod
    def _csv_to_list(raw: str) -> List[str]:
        raw = (raw or "").strip()
        if not raw:
            return []
        return [s.strip() for s in raw.split(",") if s.strip()]


settings = Settings()

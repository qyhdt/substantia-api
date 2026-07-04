import { createContext, useContext, useState, type ReactNode } from 'react'

export type Lang = 'zh' | 'en'

const DICT = {
  zh: {
    // 顶栏 / 通用
    brand_tag: 'API',
    nav_features: '功能',
    nav_pricing: '价格',
    nav_docs: '文档',
    login: '登录',
    register: '注册',
    logout: '退出',
    console: '控制台',
    get_started: '免费开始',
    free_trial_note: '注册即送 $20 试用额度',

    // Hero
    hero_title: '面向开发者的 Claude 智能 API',
    hero_sub: '一行替换 base_url，即可用上 Claude Opus / Sonnet。按 token 计费，注册送 $20，随用随付，无月费。',
    hero_cta1: '免费开始',
    hero_cta2: '查看价格',

    // 功能
    features_title: '为什么选 Substantia',
    feat1_t: 'Anthropic 兼容',
    feat1_d: '完全兼容 Anthropic Messages API（/v1/messages）。现有 SDK 改一下 base_url 和 key 即可接入，零改造。',
    feat2_t: '真 Claude 算力',
    feat2_d: '后端跑官方 Claude Code（Opus 4.8 / Sonnet），多订阅自动分流，稳定不掉链。',
    feat3_t: '按量计费',
    feat3_d: '按 token 逐模型定价，用多少算多少。注册自动送 $20，余额用完再充，无月费无锁定。',
    feat4_t: '密钥与用量自管',
    feat4_d: '控制台自助创建 / 禁用 API key，实时查看每把 key 的调用量与花费。',

    // 价格
    pricing_title: '官网价 5 折',
    pricing_sub: '价格对标 Anthropic 官网，全站统一 5 折：官网价 × 50% = 你的实付。注册即送 $20，按 token 精确结算。',
    pricing_badge: '🔥 官网价 5 折',
    pricing_col_model: '模型',
    pricing_col_in: '输入 / 百万 token',
    pricing_col_out: '输出 / 百万 token',
    pricing_official: '官网',
    pricing_note: '* 价格对标 Anthropic 官网，划线为官网价、加粗为你的实付（5 折）。余额按微美元精确结算。',
    pricing_cta: '注册领 $20 →',

    // 落地页底部 / 登录
    footer_tag: 'Substantia API · 面向开发者的 Claude 网关',
    back_home: '← 返回首页',
    email: '邮箱',
    password: '密码（≥6 位）',
    no_account: '没有账号？',
    has_account: '已有账号？',
    to_register: '注册',
    to_login: '去登录',
    submitting: '提交中…',
    failed: '失败',
    captcha: '图形验证码',
    captcha_refresh: '点击刷新',
    email_code: '邮箱验证码',
    send_code: '发送验证码',
    sending: '发送中…',
    code_sent: '验证码已发送，请查收邮箱（含垃圾箱）',
    enter_email_first: '请先填写邮箱',
    enter_captcha_first: '请先填图形验证码',

    // 顶栏试用 banner
    trial_banner_1: '你有试用额度',
    trial_banner_2: '，有效期至',
    trial_banner_3: '。期间充值任意金额（≥ $1），试用额度即',
    trial_permanent: '永久有效',

    // 控制台标签页
    tab_keys: '我的 Key',
    tab_usage: '用量明细',
    tab_topups: '充值',

    // 通用按钮 / 文案
    copy: '复制',
    copied: '已复制 ✓',
    cancel: '取消',
    generate: '生成',
    disable: '禁用',
    delete: '删除',

    // 新 key banner
    newkey_title: '新 Key（仅显示一次，请妥善保存）：',
    copy_key: '复制 Key',

    // 新建 Key 卡片
    card_newkey: '新建 Key',
    key_name_ph: '名称',
    newkey_desc_1: '把 key 当作下游网关的密钥用，同时支持 ',
    newkey_desc_2: ' 和 ',
    newkey_desc_3: ' 两种协议，点开看示例：',
    accordion_collapse: '收起 ▲',
    accordion_expand: '点开查看 ▼',
    copy_sample: '复制示例',
    copy_real_key: '一键复制（填入真实 key）',
    anthropic_compat: 'Anthropic 兼容',
    openai_compat: 'OpenAI 兼容',
    anthropic_note: '官方 anthropic SDK：base_url = https://api.substantia.ai，key 当作 x-api-key',
    openai_note: '官方 openai SDK：base_url = https://api.substantia.ai/v1，key 当作 api_key（Bearer）',
    copy_curl_done: '已复制 {title} curl（含 {name}）✓',
    copy_curl_nokey: '没有可自动填入的 key —— 旧 key 不保存明文，请先「生成」一个新 key',
    model_pick: '示例模型：',

    // Cursor 卡片
    card_cursor: '在 Cursor 中使用',
    cursor_desc_1: 'Cursor 走 ',
    cursor_desc_2: ' 接入，支持 ',
    cursor_desc_3: ' 与 ',
    cursor_desc_4: '（工具调用 / 读本地项目）两种模式。按以下步骤配置：',
    cursor_step1_1: '打开 ',
    cursor_step1_2: '（macOS 快捷键 ',
    cursor_step2_1: '展开底部 ',
    cursor_step2_2: '，在 ',
    cursor_step2_3: ' 填入你的 ',
    cursor_step2_4: ' key',
    cursor_step3_1: '打开 ',
    cursor_step3_2: '，地址填（',
    cursor_step3_3: '必须带 ',
    cursor_step3_4: '）：',
    cursor_step4_1: '把 ',
    cursor_step4_2: 'OpenAI API Key 右侧开关打开（变绿）',
    cursor_step4_3: ' —— 这一步最容易漏',
    cursor_step5_1: '在 ',
    cursor_step5_2: ' 里点 ',
    cursor_step5_3: '，输入（全小写）：',
    cursor_step6_1: '在 Chat / Agent 下拉里选中 ',
    cursor_step6_2: '（',
    cursor_step6_3: '不要',
    cursor_step6_4: ' 选 Cursor 自带的 “Opus 4.8”，那走的是 Cursor 订阅）',
    copy_my_key: '复制我的 Key',
    cursor_foot_1: '说明：自定义名 ',
    cursor_foot_2: ' 后台会自动识别为 ',
    cursor_foot_3: '；Anthropic API Key 保持关闭，避免冲突。Agent 模式下可用 ',
    cursor_foot_4: ' 让模型读取本地项目。',

    // Claude Code (CLI) 卡片
    card_claudecli: '在 Claude Code (CLI) 中使用',
    claudecli_desc_1: 'Claude Code 官方命令行直接接入：把 ',
    claudecli_desc_2: ' 指向本网关、',
    claudecli_desc_3: ' 填你的 sk-key 即可。设置以下环境变量后运行 ',
    claudecli_desc_4: '：',
    claudecli_note: '说明：网关会把 Claude Code 的请求原生透传到 Claude；换模型改 ',
    claudecli_note_2: ' 即可（可选，不设则用账号默认）。也可写进 ~/.zshrc 长期生效。',
    claudecli_warn_1: '不生效？如果 export 之后仍连到旧地址，多半是 ',
    claudecli_warn_2: ' 里的 ',
    claudecli_warn_3: ' 字段在覆盖 shell 环境变量（它的优先级更高，用过其他中转站的机器上很常见）。无需改文件，直接用下面的命令启动即可——命令行参数会覆盖 settings.json：',

    // 选择 key 弹窗
    pick_title: '选择一个 Key 生成 {title} curl',

    // 我的 key 列表
    card_keylist: '我的 Key 列表',
    col_name: '名称',
    col_prefix: '前缀',
    col_status: '状态',
    col_spent: '已花费',
    col_cap: '封顶',
    col_created: '创建',
    copy_disabled_title: '旧 key 未存明文，无法复制完整 key',
    confirm_del_key: '确认删除这把 key？删除后用它的请求会立即失效，且不可恢复。',
    empty_keys: '暂无 key',

    // 用量明细
    card_usage: '用量明细',
    col_time: '时间',
    col_model: '模型',
    col_slot: 'slot',
    col_tokens: 'tokens',
    col_cost: '花费',
    empty_usage: '还没有调用记录',

    // 充值
    bonus_tiers_title: '充值赠送（充得越多送越多）',
    recharge_credited: '到账',
    bonus_word: '赠送',
    card_recharge: '充值（信用卡 / 支付宝 / 微信）',
    recharge_go: '去支付',
    recharge_going: '跳转中…',
    recharge_off: '充值暂未开通',
    recharge_note: '支付由 Polar 处理，成功后余额自动到账（可能有几秒延迟）。',
    card_recharge_log: '充值记录',
    col_amount: '金额',
    topup_paid: '已到账',
    topup_pending: '待支付',
    empty_topups: '暂无记录',
  },
  en: {
    brand_tag: 'API',
    nav_features: 'Features',
    nav_pricing: 'Pricing',
    nav_docs: 'Docs',
    login: 'Log in',
    register: 'Sign up',
    logout: 'Log out',
    console: 'Console',
    get_started: 'Get started free',
    free_trial_note: 'Sign up and get $20 free credit',

    hero_title: 'The Claude API gateway for developers',
    hero_sub: 'Swap one base_url and use Claude Opus / Sonnet. Pay per token, $20 free on signup, no monthly fee.',
    hero_cta1: 'Get started free',
    hero_cta2: 'See pricing',

    features_title: 'Why Substantia',
    feat1_t: 'Anthropic-compatible',
    feat1_d: 'Fully compatible with the Anthropic Messages API (/v1/messages). Point your existing SDK at our base_url and key — zero rewrites.',
    feat2_t: 'Real Claude power',
    feat2_d: 'Backed by official Claude Code (Opus 4.8 / Sonnet) with multi-subscription routing for rock-solid uptime.',
    feat3_t: 'Pay as you go',
    feat3_d: 'Per-model, per-token pricing — pay only for what you use. $20 free on signup, top up anytime, no lock-in.',
    feat4_t: 'Self-serve keys & usage',
    feat4_d: 'Create or revoke API keys in the console and watch per-key calls and spend in real time.',

    pricing_title: '50% off official pricing',
    pricing_sub: 'Benchmarked to Anthropic official rates — a flat 50% off: official price × 50% = what you pay. $20 free on signup, settled precisely per token.',
    pricing_badge: '🔥 50% off official',
    pricing_col_model: 'Model',
    pricing_col_in: 'Input / 1M tokens',
    pricing_col_out: 'Output / 1M tokens',
    pricing_official: 'Official',
    pricing_note: '* Benchmarked to Anthropic official rates: struck-through is the official price, bold is your price (50% off). Balance settled in micro-USD.',
    pricing_cta: 'Claim $20 free →',

    footer_tag: 'Substantia API · the Claude gateway for developers',
    back_home: '← Back home',
    email: 'Email',
    password: 'Password (≥6 chars)',
    no_account: 'No account?',
    has_account: 'Have an account?',
    to_register: 'Sign up',
    to_login: 'Log in',
    submitting: 'Submitting…',
    failed: 'Failed',
    captcha: 'Captcha',
    captcha_refresh: 'Click to refresh',
    email_code: 'Email code',
    send_code: 'Send code',
    sending: 'Sending…',
    code_sent: 'Code sent — check your inbox/spam',
    enter_email_first: 'Enter email first',
    enter_captcha_first: 'Enter the captcha first',

    // top-bar trial banner
    trial_banner_1: 'You have trial credit',
    trial_banner_2: ', valid until',
    trial_banner_3: '. Top up any amount (≥ $1) during this period to make the trial credit',
    trial_permanent: 'permanent',

    // console tabs
    tab_keys: 'My Keys',
    tab_usage: 'Usage',
    tab_topups: 'Top up',

    // common buttons / text
    copy: 'Copy',
    copied: 'Copied ✓',
    cancel: 'Cancel',
    generate: 'Generate',
    disable: 'Disable',
    delete: 'Delete',

    // new key banner
    newkey_title: 'New key (shown only once — store it safely):',
    copy_key: 'Copy key',

    // new key card
    card_newkey: 'Create key',
    key_name_ph: 'Name',
    newkey_desc_1: 'Use the key as the gateway secret. Supports both ',
    newkey_desc_2: ' and ',
    newkey_desc_3: ' protocols — click to see examples:',
    accordion_collapse: 'Collapse ▲',
    accordion_expand: 'Show ▼',
    copy_sample: 'Copy sample',
    model_pick: 'Example model: ',
    copy_real_key: 'Copy with real key',
    anthropic_compat: 'Anthropic-compatible',
    openai_compat: 'OpenAI-compatible',
    anthropic_note: 'Official Anthropic SDK: base_url = https://api.substantia.ai, key as x-api-key',
    openai_note: 'Official OpenAI SDK: base_url = https://api.substantia.ai/v1, key as api_key (Bearer)',
    copy_curl_done: 'Copied {title} curl (with {name}) ✓',
    copy_curl_nokey: 'No key available to auto-fill — old keys keep no plaintext, please "Generate" a new key first',

    // Cursor card
    card_cursor: 'Use in Cursor',
    cursor_desc_1: 'Cursor connects via ',
    cursor_desc_2: ', supporting both ',
    cursor_desc_3: ' and ',
    cursor_desc_4: ' (tool calls / read local project) modes. Configure as follows:',
    cursor_step1_1: 'Open ',
    cursor_step1_2: ' (macOS shortcut ',
    cursor_step2_1: 'Expand ',
    cursor_step2_2: ' at the bottom, and in ',
    cursor_step2_3: ' enter your ',
    cursor_step2_4: ' key',
    cursor_step3_1: 'Turn on ',
    cursor_step3_2: ', and set the URL (',
    cursor_step3_3: 'must include ',
    cursor_step3_4: '):',
    cursor_step4_1: 'Turn on ',
    cursor_step4_2: 'the toggle to the right of OpenAI API Key (turns green)',
    cursor_step4_3: ' — this step is the most commonly missed',
    cursor_step5_1: 'In ',
    cursor_step5_2: ', click ',
    cursor_step5_3: ' and enter (all lowercase):',
    cursor_step6_1: 'In the Chat / Agent dropdown, select ',
    cursor_step6_2: ' (',
    cursor_step6_3: 'do NOT',
    cursor_step6_4: ' pick Cursor’s built-in “Opus 4.8”, which uses your Cursor subscription)',
    copy_my_key: 'Copy my key',
    cursor_foot_1: 'Note: the custom name ',
    cursor_foot_2: ' is auto-recognized by the backend as ',
    cursor_foot_3: '; keep Anthropic API Key off to avoid conflicts. In Agent mode use ',
    cursor_foot_4: ' to let the model read your local project.',

    // Claude Code (CLI) card
    card_claudecli: 'Use with Claude Code (CLI)',
    claudecli_desc_1: 'Point the official Claude Code CLI at this gateway: set ',
    claudecli_desc_2: ' to this gateway and ',
    claudecli_desc_3: ' to your sk-key. Set the env vars below, then run ',
    claudecli_desc_4: ':',
    claudecli_note: 'The gateway passes Claude Code requests straight through to Claude. To switch models, change ',
    claudecli_note_2: ' (optional — falls back to the account default). Add it to ~/.zshrc to persist.',
    claudecli_warn_1: 'Not working? If the CLI still hits an old endpoint after export, the ',
    claudecli_warn_2: ' file’s ',
    claudecli_warn_3: ' field is probably overriding your shell env vars (it takes precedence — common on machines that used another relay before). No need to edit the file — launch with the command below; CLI flags override settings.json:',

    // pick key modal
    pick_title: 'Pick a key to generate {title} curl',

    // my keys list
    card_keylist: 'My keys',
    col_name: 'Name',
    col_prefix: 'Prefix',
    col_status: 'Status',
    col_spent: 'Spent',
    col_cap: 'Cap',
    col_created: 'Created',
    copy_disabled_title: 'Old key kept no plaintext; full key cannot be copied',
    confirm_del_key: 'Delete this key? Requests using it will fail immediately and this cannot be undone.',
    empty_keys: 'No keys yet',

    // usage
    card_usage: 'Usage',
    col_time: 'Time',
    col_model: 'Model',
    col_slot: 'slot',
    col_tokens: 'tokens',
    col_cost: 'Cost',
    empty_usage: 'No calls yet',

    // top up
    bonus_tiers_title: 'Top-up bonus (spend more, get more)',
    recharge_credited: 'You get',
    bonus_word: 'bonus',
    card_recharge: 'Top up (card / Alipay / WeChat)',
    recharge_go: 'Pay',
    recharge_going: 'Redirecting…',
    recharge_off: 'Top-up not available yet',
    recharge_note: 'Payments are handled by Polar; balance is credited automatically on success (may take a few seconds).',
    card_recharge_log: 'Top-up history',
    col_amount: 'Amount',
    topup_paid: 'Credited',
    topup_pending: 'Pending',
    empty_topups: 'No records yet',
  },
} as const

export type TKey = keyof typeof DICT['zh']

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: (k: TKey) => string }>({
  lang: 'zh',
  setLang: () => {},
  t: (k) => k,
})

export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const s = (typeof localStorage !== 'undefined' && localStorage.getItem('lang')) as Lang | null
    // 默认英文；用户切过中文会写入 localStorage，不清缓存就一直保留
    return s === 'en' || s === 'zh' ? s : 'en'
  })
  const setLang = (l: Lang) => {
    try { localStorage.setItem('lang', l) } catch { /* ignore */ }
    setLangState(l)
  }
  const t = (k: TKey) => (DICT[lang] as Record<string, string>)[k] ?? (DICT.zh as Record<string, string>)[k] ?? k
  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>
}

export const useI18n = () => useContext(Ctx)

export function LangToggle() {
  const { lang, setLang } = useI18n()
  return (
    <button className="ak-lang" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}
      title={lang === 'zh' ? 'Switch to English' : '切换为中文'}>
      {lang === 'zh' ? 'EN' : '中'}
    </button>
  )
}

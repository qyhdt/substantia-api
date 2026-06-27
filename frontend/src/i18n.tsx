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
    return s === 'en' || s === 'zh' ? s : 'zh'
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

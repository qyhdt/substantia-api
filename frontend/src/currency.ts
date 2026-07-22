import { useCallback, useEffect, useState } from 'react'
import { publicApi, RMB_PER_USD_FALLBACK } from './api'

export type DisplayCurrency = 'usd' | 'rmb'

// v2 将产品默认币种从 USD 切换为 RMB；不继承旧版隐式/显式 USD 值。
const STORAGE_KEY = 'substantia_display_currency_v2'
const CHANGE_EVENT = 'substantia-display-currency-change'

function initialCurrency(): DisplayCurrency {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'usd' ? 'usd' : 'rmb'
  } catch {
    return 'rmb'
  }
}

export function useDisplayCurrency() {
  const [currency, setValue] = useState<DisplayCurrency>(initialCurrency)
  useEffect(() => {
    const sync = (event: Event) => setValue((event as CustomEvent<DisplayCurrency>).detail || initialCurrency())
    window.addEventListener(CHANGE_EVENT, sync)
    return () => window.removeEventListener(CHANGE_EVENT, sync)
  }, [])
  const setCurrency = useCallback((value: DisplayCurrency) => {
    setValue(value)
    try { localStorage.setItem(STORAGE_KEY, value) } catch { /* storage may be unavailable */ }
    window.dispatchEvent(new CustomEvent<DisplayCurrency>(CHANGE_EVENT, { detail: value }))
  }, [])
  return [currency, setCurrency] as const
}

let cachedRate = RMB_PER_USD_FALLBACK
let rateRequest: Promise<number> | null = null

export function useRmbPerUsd() {
  const [rate, setRate] = useState(cachedRate)
  useEffect(() => {
    if (!rateRequest) {
      rateRequest = publicApi.fx()
        .then((data) => {
          const next = Number(data.rate)
          if (Number.isFinite(next) && next > 0) cachedRate = next
          return cachedRate
        })
        .catch(() => cachedRate)
    }
    rateRequest.then(setRate)
  }, [])
  return rate
}

export function fmtDisplayCurrency(
  microUsd: number | null | undefined,
  currency: DisplayCurrency,
  rmbPerUsd: number,
  digits = 4,
) {
  const usd = Number(microUsd || 0) / 1e6
  return currency === 'rmb'
    ? `¥${(usd * rmbPerUsd).toFixed(digits)}`
    : `$${usd.toFixed(digits)}`
}

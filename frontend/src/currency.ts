import { useCallback, useState } from 'react'

export type DisplayCurrency = 'usd' | 'rmb'

const STORAGE_KEY = 'substantia_display_currency'

function initialCurrency(): DisplayCurrency {
  try {
    return localStorage.getItem(STORAGE_KEY) === 'rmb' ? 'rmb' : 'usd'
  } catch {
    return 'usd'
  }
}

export function useDisplayCurrency() {
  const [currency, setValue] = useState<DisplayCurrency>(initialCurrency)
  const setCurrency = useCallback((value: DisplayCurrency) => {
    setValue(value)
    try { localStorage.setItem(STORAGE_KEY, value) } catch { /* storage may be unavailable */ }
  }, [])
  return [currency, setCurrency] as const
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

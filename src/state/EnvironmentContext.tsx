import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type EnvironmentFilter = 'production' | 'staging' | 'development' | 'all'
const STORAGE_KEY = 'nexops-environment'

interface EnvironmentValue {
  environment: EnvironmentFilter
  setEnvironment: (value: EnvironmentFilter) => void
  /** Query value for API calls: undefined means every environment. */
  apiEnvironment: string | undefined
}

const EnvironmentContext = createContext<EnvironmentValue | null>(null)

function initial(): EnvironmentFilter {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'production' || saved === 'staging' || saved === 'development' || saved === 'all') return saved
  } catch { /* optional */ }
  return 'production'
}

export function EnvironmentProvider({ children }: { children: ReactNode }) {
  const [environment, setEnvironment] = useState<EnvironmentFilter>(initial)
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, environment) } catch { /* optional */ }
  }, [environment])
  const value = useMemo(() => ({ environment, setEnvironment, apiEnvironment: environment === 'all' ? undefined : environment }), [environment])
  return <EnvironmentContext.Provider value={value}>{children}</EnvironmentContext.Provider>
}

export function useEnvironment() {
  const value = useContext(EnvironmentContext)
  if (!value) throw new Error('useEnvironment must be used inside EnvironmentProvider')
  return value
}

import { createContext, useContext, useMemo } from 'react'
import type { ReactNode } from 'react'
import { api, query } from '../api/client'
import type { Incident, RuntimeSettings } from '../api/types'
import { useResource } from '../hooks/useResource'
import { useEnvironment } from './EnvironmentContext'

interface LiveValue {
  activeIncidents: Incident[]
  settings: RuntimeSettings | null
  refreshIncidents: () => Promise<void>
}

const LiveContext = createContext<LiveValue | null>(null)

/** Workspace-wide live state shared by the sidebar badge, notifications and AI card. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const { apiEnvironment } = useEnvironment()
  const incidents = useResource(() => api<Incident[]>(`/api/incidents${query({ status: 'active', environment: apiEnvironment })}`), [apiEnvironment], 15000)
  const settings = useResource(() => api<RuntimeSettings>('/api/settings'), [])
  const value = useMemo(() => ({
    activeIncidents: incidents.data ?? [],
    settings: settings.data,
    refreshIncidents: incidents.refresh,
  }), [incidents.data, incidents.refresh, settings.data])
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

export function useLive() {
  const value = useContext(LiveContext)
  if (!value) throw new Error('useLive must be used inside LiveProvider')
  return value
}

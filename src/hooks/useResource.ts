import { useCallback, useEffect, useRef, useState } from 'react'

interface ResourceState<T> {
  data: T | null
  error: string | null
  loading: boolean
  refresh: () => Promise<void>
  updatedAt: number | null
}

/** Load data from the API, optionally re-polling while the tab is visible. */
export function useResource<T>(loader: () => Promise<T>, deps: unknown[], pollMs?: number): ResourceState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const loaderRef = useRef(loader)
  loaderRef.current = loader
  const generation = useRef(0)

  const refresh = useCallback(async () => {
    const current = ++generation.current
    try {
      const result = await loaderRef.current()
      if (current !== generation.current) return
      setData(result)
      setError(null)
      setUpdatedAt(Date.now())
    } catch (caught) {
      if (current !== generation.current) return
      setError(caught instanceof Error ? caught.message : 'Request failed')
    } finally {
      if (current === generation.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    setData(null)
    void refresh()
    if (!pollMs) return undefined
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh() }, pollMs)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, pollMs])

  return { data, error, loading, refresh, updatedAt }
}

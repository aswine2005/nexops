import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { api, getToken, onUnauthorized, setToken } from '../api/client'
import type { Role, User } from '../api/types'

interface AuthValue {
  user: User | null
  ready: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  can: (role: Role) => boolean
}

const levels: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 }
const AuthContext = createContext<AuthValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    onUnauthorized(() => setUser(null))
    if (!getToken()) { setReady(true); return }
    api<User>('/api/auth/me').then(setUser).catch(() => setToken(null)).finally(() => setReady(true))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const result = await api<{ token: string; user: User }>('/api/auth/login', { method: 'POST', json: { email, password } })
    setToken(result.token)
    setUser(result.user)
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  const value = useMemo<AuthValue>(() => ({
    user, ready, login, logout,
    can: (role: Role) => Boolean(user && levels[user.role] >= levels[role]),
  }), [user, ready, login, logout])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

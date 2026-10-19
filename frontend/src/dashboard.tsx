import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from './api'
import { usePolling } from './hooks'

export interface CaseSummary {
  id: string
  title: string
  incident_type: string
  created_at: string
  status: string
  is_demo: boolean
  sensitive: boolean
  media_count: number
  location_count: number
  modality: 'image' | 'video' | 'audio' | null
  thumb: string | null
  verdict: { label: string; p_fake: number; headline?: string } | null
  investigating: boolean
}
export interface AlertRow { id: string; case_id: string; case_title: string; kind: string; message: string; created_at: string }
export interface ModelRow { key: string; title: string; state: 'loaded' | 'idle' | 'missing'; detail: string; mb: number | null; device: string | null }
export interface DashboardData {
  recent: CaseSummary[]
  counts: { cases: number; alerts: number; active_jobs: number }
  alerts: AlertRow[]
  system: {
    ok: boolean
    backend: { running: boolean; version: string; uptime_s: number }
    gpu: { available: boolean; name: string | null; vram_total_mb: number | null; vram_used_mb: number | null; utilization_pct: string | null }
    models: ModelRow[]
  }
}

interface Ctx { data: DashboardData | null; error: string | null; reload: () => void }
const C = createContext<Ctx>({ data: null, error: null, reload: () => {} })
export const useDashboard = () => useContext(C)

/** One shared, polled copy of the dashboard summary: the sidebar badge, the bell and the Home page all read it. */
export function DashboardProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const reload = useCallback(() => {
    api.get<DashboardData>('/api/dashboard?limit=6').then((d) => { setData(d); setError(null) }).catch((e) => setError(String(e.message)))
  }, [])
  useEffect(() => { reload() }, [reload])
  usePolling(reload, 6000)
  const value = useMemo(() => ({ data, error, reload }), [data, error, reload])
  return <C.Provider value={value}>{children}</C.Provider>
}

/** Verdict -> badge presentation. Colour is always paired with an icon-bearing label. */
export type Tone = 'crit' | 'warn' | 'good' | 'info' | 'neutral'
export function caseBadge(c: Pick<CaseSummary, 'verdict' | 'investigating'>): { label: string; tone: Tone; busy?: boolean } {
  if (c.investigating) return { label: 'Investigating', tone: 'info', busy: true }
  const l = c.verdict?.label
  if (l === 'fake') return { label: 'Fake', tone: 'crit' }
  if (l === 'likely_fake') return { label: 'Likely fake', tone: 'crit' }
  if (l === 'uncertain') return { label: 'Uncertain', tone: 'warn' }
  if (l === 'likely_real') return { label: 'Likely real', tone: 'good' }
  if (l === 'real') return { label: 'Real', tone: 'good' }
  return { label: 'Unknown', tone: 'warn' }
}
export const TONE_VAR: Record<Tone, string> = {
  crit: 'var(--color-v-fake)', warn: 'var(--color-v-uncertain)', good: 'var(--color-v-real)', info: 'var(--color-accent-text)', neutral: 'var(--color-ink-2)',
}

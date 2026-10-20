/* eslint-disable react-refresh/only-export-components */
import { ShieldAlert } from 'lucide-react'
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { api, ConsentNeeded, type Case, type Media } from './api'
import { DEMO_BLOCKED_MESSAGE } from './lib/demo'

export interface CaseData {
  case: Case
  media: Media[]
  safety_notices: { level: string; title: string; text: string }[]
  blur: boolean
}

interface Ctx {
  data: CaseData
  caseId: string
  tick: number // bumps whenever server data may have changed
  refresh: () => void
  withConsent: <T>(fn: () => Promise<T>) => Promise<T | undefined>
  toast: (msg: string, tone?: 'ok' | 'err') => void
}

export const CaseCtx = createContext<Ctx | null>(null)
export const useCase = () => {
  const c = useContext(CaseCtx)
  if (!c) throw new Error('useCase outside provider')
  return c
}

export function useConsentGate(caseId: string | null) {
  const [pending, setPending] = useState<{ action: string; description: string } | null>(null)
  const resolver = useRef<((ok: boolean) => void) | null>(null)
  const withConsent = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    try {
      return await fn()
    } catch (e) {
      if (!(e instanceof ConsentNeeded)) throw e
      setPending({ action: e.action, description: e.description })
      const ok = await new Promise<boolean>((res) => { resolver.current = res })
      setPending(null)
      if (!ok) return undefined
      await api.post('/api/consent', { action: e.action, case_id: caseId, scope: 'once' })
      return await fn()
    }
  }, [caseId])
  const dialog = pending ? (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div className="card w-full max-w-lg p-6">
        <div className="flex items-center gap-2 text-warn"><ShieldAlert size={18} /><span className="font-semibold text-ink">Outward action — your consent is needed</span></div>
        <p className="mt-3 text-ink-2">{pending.description}.</p>
        <p className="mt-2 text-sm text-ink-3">This sends data outside this computer. A consent record and a custody-log entry are kept. Nothing is sent unless you approve.</p>
        <div className="mono mt-3 text-xs text-ink-3">action: {pending.action}</div>
        <div className="mt-5 flex justify-end gap-2">
          <button className="btn" onClick={() => resolver.current?.(false)}>Don't send</button>
          <button className="btn btn-primary" onClick={() => resolver.current?.(true)}>I consent — send once</button>
        </div>
      </div>
    </div>
  ) : null
  return { withConsent, dialog }
}

export function Toasts({ items }: { items: { id: number; msg: string; tone: string }[] }) {
  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2">
      {items.map((t) => (
        <div key={t.id} className={`card px-4 py-2.5 text-sm shadow-xl ${t.tone === 'err' ? 'border-crit' : ''}`}>{t.msg}</div>
      ))}
    </div>
  )
}

export function useToasts() {
  const [items, setItems] = useState<{ id: number; msg: string; tone: string }[]>([])
  const toast = useCallback((msg: string, tone: 'ok' | 'err' = 'ok') => {
    if (msg === DEMO_BLOCKED_MESSAGE) return  // the demo dialog already explains this
    const id = Date.now() + Math.random()
    setItems((x) => [...x, { id, msg, tone }])
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4500)
  }, [])
  return { items, toast }
}

export function Provider({ value, children }: { value: Ctx; children: ReactNode }) {
  return <CaseCtx.Provider value={value}>{children}</CaseCtx.Provider>
}

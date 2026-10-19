import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api } from './api'
import { applyPrefs, DEFAULT_PREFS, loadLocal, sanitize, type AccentSet, type Prefs } from './lib/theme'

type SaveState = 'saved' | 'saving' | 'local-only'
interface PrefsCtx {
  prefs: Prefs
  mode: 'light' | 'dark'
  accent: AccentSet
  version: number // bumps on every theme change so charts and canvases can redraw
  saveState: SaveState
  set: (patch: Partial<Prefs>) => void
  reset: () => void
}

const Ctx = createContext<PrefsCtx | null>(null)
export const usePrefs = () => {
  const c = useContext(Ctx)
  if (!c) throw new Error('usePrefs outside PrefsProvider')
  return c
}
/** Re-renders the caller whenever the theme changes (for cytoscape/SVG code that reads tokens as concrete colours). */
export const useThemeVersion = () => usePrefs().version

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<Prefs>(() => loadLocal())
  const [applied, setApplied] = useState(() => applyPrefs(prefs))
  const [version, setVersion] = useState(0)
  const [saveState, setSaveState] = useState<SaveState>('saved')
  const timer = useRef<number | undefined>(undefined)
  const first = useRef(true)

  const commit = useCallback((p: Prefs) => {
    setApplied(applyPrefs(p))
    setVersion((v) => v + 1)
  }, [])

  // reconcile with the copy stored beside the cases (survives a cleared browser or a different browser)
  useEffect(() => {
    api.get<Prefs>('/api/preferences').then((srv) => {
      const local = loadLocal()
      const s = sanitize(srv)
      const newer = (s.updated_at ?? '') > (local.updated_at ?? '')
      if (newer) { setPrefs(s); commit(s) }
      else if (!local.updated_at && !srv.updated_at) setSaveState('saved')
      else if ((local.updated_at ?? '') > (s.updated_at ?? '')) api.put('/api/preferences', local).catch(() => setSaveState('local-only'))
    }).catch(() => setSaveState('local-only'))
  }, [commit])

  // follow the operating system while the mode is "system"
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => { if (prefs.mode === 'system') commit(prefs) }
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [prefs, commit])

  const set = useCallback((patch: Partial<Prefs>) => {
    setPrefs((cur) => {
      const next = sanitize({ ...cur, ...patch, updated_at: new Date().toISOString() })
      commit(next)
      setSaveState('saving')
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => {
        const { theme, mode, accent, motion, profile } = next
        api.put<Prefs>('/api/preferences', { theme, mode, accent, motion, profile }).then((r) => {
          // store the server's timestamp so both copies agree
          localStorage.setItem('dfir.prefs', JSON.stringify({ ...next, updated_at: r.updated_at }))
          setSaveState('saved')
        }).catch(() => setSaveState('local-only'))
      }, 350)
      return next
    })
  }, [commit])

  useEffect(() => { first.current = false }, [])
  const reset = useCallback(() => set({ ...DEFAULT_PREFS }), [set])
  const value = useMemo<PrefsCtx>(() => ({ prefs, mode: applied.mode, accent: applied.accent, version, saveState, set, reset }), [prefs, applied, version, saveState, set, reset])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

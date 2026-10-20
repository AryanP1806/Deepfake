import { useSyncExternalStore } from 'react'

/** Demo mode: when no analysis backend is reachable (for example on Vercel, which cannot run it), the app shows a few saved,
 *  complete example cases from static files in /demo instead. Reading works; anything that needs the backend (a new
 *  investigation, an upload, a re-run, a delete) is refused with a friendly explanation.
 *
 *  Static files are written by scripts/export_demo.py. Their names come from demoKey(), which must match that script. */

export type Mode = 'booting' | 'live' | 'demo' | 'down'

export interface DemoCase { id: string; title: string; blurb: string; source_url: string }
export interface DemoManifest { generated_at: string; cases: DemoCase[]; keys: string[]; vault: Record<string, string> }

export const DEMO_BLOCKED_MESSAGE = 'Demo mode: connect the backend to do this.'
export class DemoBlocked extends Error {
  method: string
  url: string
  constructor(method: string, url: string) { super(DEMO_BLOCKED_MESSAGE); this.method = method; this.url = url }
}
export class DemoUnavailable extends Error {
  url: string
  constructor(url: string) { super('Demo mode: this screen needs the live backend and is not part of the saved demo.'); this.url = url }
}

const FORCE_KEY = 'dfir.demo'
let mode: Mode = 'booting'
let manifest: DemoManifest | null = null
let keys = new Set<string>()
let features: string[] = []
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((l) => l())
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

export const getMode = () => mode
export const isDemo = () => mode === 'demo'
export const getFeatures = () => features
export const getManifest = () => manifest
export const useMode = () => useSyncExternalStore(subscribe, () => mode)

/** Same rule as demo_key() in scripts/export_demo.py. */
export function demoKey(url: string): string {
  const [path, query] = url.replace(/^\/api\//, '').split('?')
  return query ? `${path}__q__${query.replace(/[^A-Za-z0-9_=.-]/g, '-')}` : path
}

async function probeBackend(): Promise<boolean> {
  const ctl = new AbortController()
  const t = setTimeout(() => ctl.abort(), 2500)
  try {
    const r = await fetch('/api/health', { signal: ctl.signal })
    if (!r.ok || !(r.headers.get('content-type') ?? '').includes('json')) return false  // a static host answers with HTML or 404
    const j = await r.json()
    features = j.features ?? []
    return j.ok === true
  } catch { return false } finally { clearTimeout(t) }
}

async function loadManifest(): Promise<boolean> {
  try {
    const r = await fetch('/demo/manifest.json')
    if (!r.ok || !(r.headers.get('content-type') ?? '').includes('json')) return false
    manifest = await r.json()
    keys = new Set(manifest!.keys)
    return true
  } catch { return false }
}

/** Decide once at start-up. ?demo=1 forces the saved demo (and remembers it); ?demo=0 goes back to the live backend. */
export async function bootstrap(): Promise<void> {
  const q = new URLSearchParams(window.location.search).get('demo')
  if (q === '1') localStorage.setItem(FORCE_KEY, '1')
  if (q === '0') localStorage.removeItem(FORCE_KEY)
  const forced = localStorage.getItem(FORCE_KEY) === '1'
  if (!forced && (await probeBackend())) mode = 'live'
  else mode = (await loadManifest()) ? 'demo' : 'down'
  emit()
  if (mode !== 'live' && !forced) {  // if a backend appears later (you started it), switch to it
    setInterval(async () => { if (await probeBackend()) window.location.reload() }, 30000)
  }
}

export const isForcedDemo = () => localStorage.getItem(FORCE_KEY) === '1'

export function notifyBlocked(method: string, url: string) {
  window.dispatchEvent(new CustomEvent('dfir-demo-blocked', { detail: { method, url } }))
}
/** For plain links to downloads that exist only on a live backend. */
export function guardDemo(e: { preventDefault: () => void }) {
  if (isDemo()) { e.preventDefault(); notifyBlocked('GET', 'download') }
}

let searchIndex: { type: string; title: string; subtitle: string; case_id: string; route: string; hay: string }[] | null = null
async function searchDemo(url: string) {
  const q = (new URLSearchParams(url.split('?')[1] ?? '').get('q') ?? '').trim().toLowerCase()
  if (q.length < 2) return { query: q, results: [] }
  if (!searchIndex) searchIndex = await (await fetch('/demo/search.json')).json()
  const hits = searchIndex!.filter((h) => h.hay.includes(q))
  return { query: q, results: hits.slice(0, 14).map(({ hay: _h, ...rest }) => rest), total: hits.length }
}

export async function demoRequest<T>(method: string, url: string, body?: unknown): Promise<T> {
  const path = url.split('?')[0]
  // appearance choices are kept in this browser, so they work without a backend
  if (method === 'PUT' && path === '/api/preferences') return { ...(body as object), updated_at: new Date().toISOString() } as T
  if (method !== 'GET') { notifyBlocked(method, url); throw new DemoBlocked(method, url) }
  if (path === '/api/search') return (await searchDemo(url)) as T
  const key = demoKey(url)
  if (!keys.has(key)) throw new DemoUnavailable(url)
  const r = await fetch(`/demo/api/${key}.json`)
  if (!r.ok) throw new DemoUnavailable(url)
  return (await r.json()) as T
}

/** Where a saved evidence image lives in demo mode ('' if it was not kept, e.g. a very large video). */
export function demoVaultUrl(caseId: string, sha: string): string {
  const ext = manifest?.vault[`${caseId}/${sha}`]
  return ext ? `/demo/vault/${caseId}/${sha}.${ext}` : ''
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Status = 'Verified' | 'Corroborated' | 'Inferred' | 'Lead' | 'Unknown'

export interface Claim {
  id: string
  media_id?: string
  stage: string
  category: string
  statement: string
  status: Status
  strength: string
  polarity: string
  evidence_refs: string[]
  produced_by: string
  limitations: string
  what_would_confirm: string
  why: string
  data: Record<string, any>
}

export interface Media {
  id: string
  case_id: string
  sha256: string
  md5: string
  modality: 'image' | 'video' | 'audio'
  role: string
  filename: string
  mime: string
  size: number
  width?: number
  height?: number
  duration?: number
  label: string
  thumb?: string
  level?: string | null
  verdict?: import('./components/VerdictCard').Verdict | null
  location_id?: string | null
}

export interface Case {
  id: string
  title: string
  incident_type: string
  description: string
  safety: { minors_possible?: boolean; intimate?: boolean }
  complainant: Record<string, string>
  created_at: string
  status: string
  is_demo: boolean
  media_count?: number
  location_count?: number
}

export class ConsentNeeded extends Error {
  action: string
  description: string
  constructor(action: string, description: string) {
    super(description)
    this.action = action
    this.description = description
  }
}

async function req<T>(method: string, url: string, body?: any): Promise<T> {
  const init: RequestInit = { method, headers: {} }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as any)['Content-Type'] = 'application/json'
  }
  const r = await fetch(url, init)
  if (r.status === 428) {
    const j = await r.json()
    throw new ConsentNeeded(j.consent_action, j.description)
  }
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const j = await r.json()
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

export const api = {
  get: <T = any>(u: string) => req<T>('GET', u),
  post: <T = any>(u: string, b?: any) => req<T>('POST', u, b),
  put: <T = any>(u: string, b?: any) => req<T>('PUT', u, b),
  patch: <T = any>(u: string, b?: any) => req<T>('PATCH', u, b),
}

export const vaultUrl = (caseId: string, sha?: string | null, download = false) =>
  sha ? `/api/vault/${caseId}/${sha}${download ? '?download=true' : ''}` : ''

export const fmtTime = (iso?: string | null, withTz = true) => {
  if (!iso) return '—'
  const hasTz = /[zZ]|[+-]\d\d:?\d\d$/.test(iso)
  const d = new Date(hasTz ? iso : iso.replace(' ', 'T'))
  if (isNaN(d.getTime())) return iso
  const s = d.toLocaleString('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hour12: false,
  })
  return withTz ? `${s} IST${hasTz ? '' : ' (tz unknown)'}` : s
}

export const fmtBytes = (n: number) =>
  n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(0)} KB` : `${n} B`

export const INCIDENT_TYPES: Record<string, string> = {
  ncii: 'Intimate imagery (NCII / morphed)',
  sextortion: 'Sextortion',
  impersonation_fraud: 'Impersonation / fraud',
  voice_clone_scam: 'Voice-clone scam',
  political_defamation: 'Political / defamation / misinformation',
  other: 'Other',
}

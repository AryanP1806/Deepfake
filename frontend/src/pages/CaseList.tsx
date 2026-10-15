import { FolderOpen, Plus, ShieldAlert } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtTime, INCIDENT_TYPES, type Case } from '../api'
import { TopNav } from './Lab'
import { Spinner } from '../components/ui'
import { useApi } from '../hooks'

const CHAIN = ['Detect', 'Explain', 'Trace', 'Connect', 'Preserve', 'Investigate', 'Prove', 'Report', 'Act', 'Monitor']

export default function CaseList() {
  const { data: cases, loading } = useApi<Case[]>('/api/cases')
  const nav = useNavigate()
  const [form, setForm] = useState({ title: '', incident_type: 'ncii', description: '', intimate: false, minors: false })
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const create = async () => {
    setBusy(true)
    setErr('')
    try {
      const c = await api.post<Case>('/api/cases', {
        title: form.title || 'Untitled incident', incident_type: form.incident_type, description: form.description,
        safety: { intimate: form.intimate, minors_possible: form.minors },
      })
      if (file) {
        const fd = new FormData()
        fd.append('file', file)
        fd.append('role', 'primary')
        await api.post(`/api/cases/${c.id}/media`, fd)
      }
      nav(`/case/${c.id}/overview`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <TopNav active="cases" />

      <div className="mt-10 grid gap-8 lg:grid-cols-[1.15fr_1fr]">
        <div>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight">A deepfake is an incident,<br /><span className="text-ink-3">not a file.</span></h1>
          <p className="mt-4 max-w-xl text-[15px] text-ink-2">
            Start from one suspicious file. The system works through the whole chain and labels every statement with how
            well it is established. It never gives a verdict without the evidence behind it.
          </p>
          <div className="mt-6 flex flex-wrap gap-1.5">
            {CHAIN.map((s, i) => (
              <span key={s} className="flex items-center gap-1.5 text-[13px] text-ink-2">
                <span className="rounded-md border border-line-strong bg-raised px-2 py-1 font-medium">{s}</span>
                {i < CHAIN.length - 1 && <span className="text-ink-3">→</span>}
              </span>
            ))}
          </div>
          <div className="mt-10">
            <div className="label mb-3">Cases</div>
            {loading && <Spinner label="Loading cases" />}
            <div className="space-y-2">
              {(cases ?? []).filter((c) => c.status !== 'archived').map((c) => (
                <Link key={c.id} to={`/case/${c.id}/overview`} className="card flex items-center gap-4 p-4 transition hover:border-line-strong">
                  <FolderOpen size={18} className="text-ink-3" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{c.title}</div>
                    <div className="text-xs text-ink-3">{INCIDENT_TYPES[c.incident_type]} · opened {fmtTime(c.created_at)} · {c.media_count} media · {c.location_count} locations</div>
                  </div>
                  {c.is_demo && <span className="rounded border border-line-strong px-1.5 py-0.5 text-[10px] font-bold tracking-wider text-ink-3">DEMO</span>}
                  {(c.safety?.intimate || c.safety?.minors_possible) && <ShieldAlert size={16} className="text-warn" />}
                </Link>
              ))}
              {cases && cases.length === 0 && <div className="text-sm text-ink-3">No cases yet. Open one on the right, or build the demo case: <span className="mono">uv run python ../demo/make_demo_case.py</span></div>}
            </div>
          </div>
        </div>

        <div className="card h-fit p-6">
          <div className="flex items-center gap-2 font-semibold"><Plus size={16} /> Open a new incident</div>
          <div className="mt-5 space-y-4">
            <div><div className="label mb-1.5">Title</div>
              <input className="input" placeholder="e.g. Morphed image shared on Instagram" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
            <div><div className="label mb-1.5">Incident type</div>
              <select className="input" value={form.incident_type} onChange={(e) => setForm({ ...form, incident_type: e.target.value })}>
                {Object.entries(INCIDENT_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></div>
            <div><div className="label mb-1.5">What happened (your words)</div>
              <textarea className="input min-h-24" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
            <div><div className="label mb-1.5">Suspicious media file</div>
              <input type="file" accept="image/*,video/*,audio/*" className="input" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <div className="mt-1 text-xs text-ink-3">Stored write-once, hashed (SHA-256 / MD5) and logged. It stays on this computer.</div></div>
            <div className="space-y-2 rounded-lg border border-line p-3">
              <div className="label">Safety</div>
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={form.intimate} onChange={(e) => setForm({ ...form, intimate: e.target.checked })} />
                <span>The media is intimate / sexual <span className="text-ink-3">— previews will be blurred</span></span></label>
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={form.minors} onChange={(e) => setForm({ ...form, minors: e.target.checked })} />
                <span>A minor may be depicted <span className="text-ink-3">— media from discovered URLs will NOT be downloaded; report route is prioritised</span></span></label>
            </div>
            {err && <div className="text-sm text-crit">{err}</div>}
            <button className="btn btn-primary w-full justify-center" disabled={busy} onClick={create}>{busy ? 'Opening…' : 'Open case & start analysis'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

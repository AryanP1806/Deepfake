import { Archive, ArchiveRestore, ChevronDown, FolderOpen, Link2, Search, ShieldAlert, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtTime, INCIDENT_CHOICES, INCIDENT_TYPES, type Case } from '../api'
import { DeleteCaseDialog } from '../components/DeleteCaseDialog'
import { Spinner } from '../components/ui'
import { useApi } from '../hooks'
import { TopNav } from './Lab'

export default function CaseList() {
  const { data: cases, loading, reload } = useApi<Case[]>('/api/cases')
  const nav = useNavigate()
  const [link, setLink] = useState('')
  const [moreLinks, setMoreLinks] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [incident, setIncident] = useState('other')
  const [more, setMore] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', intimate: false, minors: false, findCopies: true })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [del, setDel] = useState<Case | null>(null)
  const [showArchived, setShowArchived] = useState(false)

  const sensitive = form.intimate || form.minors
  const pick = (k: string) => {
    setIncident(k)
    if (k === 'ncii' || k === 'sextortion') setForm((f) => ({ ...f, intimate: true }))
  }

  const start = async () => {
    const urls = [link, ...moreLinks.split(/\s+/)].map((u) => u.trim()).filter(Boolean)
    if (!urls.length && !file) { setErr('Paste a link to the photo, video or post — or choose a file.'); return }
    setBusy(true)
    setErr('')
    try {
      const base = {
        title: form.title, incident_type: incident, description: form.description,
        safety: { intimate: form.intimate, minors_possible: form.minors }, find_copies: form.findCopies && !sensitive,
      }
      let c: Case & { job?: unknown }
      if (file) {  // the file is the suspect media; any links are the places it was seen
        c = await api.post<Case>('/api/cases', { ...base, title: form.title || file.name })
        const fd = new FormData()
        fd.append('file', file)
        fd.append('role', 'primary')
        await api.post(`/api/cases/${c.id}/media`, fd)
        if (urls.length) await api.post(`/api/cases/${c.id}/investigate`, { urls, find_copies: base.find_copies, depth: 1 })
      } else {
        c = await api.post<Case & { job?: unknown }>('/api/cases', { ...base, urls })
        if (!c.job) {  // an up-to-date backend always starts the investigation when links are given
          setErr('The case was created, but the backend did not start investigating the link. It is probably an old version: restart it (see the red banner), then press Investigate again.')
          return
        }
      }
      nav(`/case/${c.id}/overview`)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const visible = (cases ?? []).filter((c) => (c.status === 'archived') === showArchived)
  const archivedCount = (cases ?? []).filter((c) => c.status === 'archived').length

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <TopNav active="cases" />

      <div className="mt-10 grid gap-8 lg:grid-cols-[1.1fr_1fr]">
        <div>
          <div className="card p-6">
            <h1 className="display text-[28px] font-semibold leading-tight">Check a suspicious photo, video or post</h1>
            <p className="mt-2 text-[14px] text-ink-2">
              Link to the photo, video or post. We open it, save it as evidence, check whether it is AI-made or edited, look for other copies, and tell you how to get it taken down.
              No screenshot needed.
            </p>
            <div className="mt-4 flex gap-2">
              <div className="relative flex-1">
                <Link2 size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3" />
                <input className="input !py-3 !pl-9 !text-[14px]" placeholder="https://… (photo, video, Instagram / X / Facebook / YouTube post)" value={link}
                  onChange={(e) => setLink(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && start()} />
              </div>
              <button className="btn btn-primary px-5" disabled={busy} onClick={start}><Search size={15} /> {busy ? 'Starting…' : 'Investigate'}</button>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3 text-[12.5px] text-ink-3">
              <label className="btn cursor-pointer px-3 py-1.5 text-[12.5px]">
                <Upload size={13} /> {file ? file.name : 'or upload a file'}
                <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              {file && <button className="underline" onClick={() => setFile(null)}>remove file</button>}
              <span>Public pages only, opened as a logged-out visitor. A login-protected post can't be read — then upload a saved copy.</span>
            </div>

            <div className="mt-5 border-t border-line pt-4">
              <div className="label mb-2">What kind of problem is this?</div>
              <div role="radiogroup" aria-label="Kind of problem" className="grid gap-2 sm:grid-cols-2">
                {INCIDENT_CHOICES.map((o) => (
                  <button key={o.key} type="button" role="radio" aria-checked={incident === o.key} onClick={() => pick(o.key)}
                    className={`rounded-lg border p-3 text-left transition ${incident === o.key ? 'border-accent-text bg-accent-soft' : 'border-line hover:border-line-strong'}`}>
                    <div className="text-[13px] font-semibold leading-snug">{o.title}</div>
                    {o.hint && <div className="mt-0.5 text-[11.5px] text-ink-3">{o.hint}</div>}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-4 space-y-2 rounded-lg border border-line p-3">
              <div className="label">Safety</div>
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={form.intimate} onChange={(e) => setForm({ ...form, intimate: e.target.checked })} />
                <span>The media is intimate or sexual <span className="text-ink-3">— previews are blurred, and nothing is sent to outside services without asking you</span></span></label>
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={form.minors} onChange={(e) => setForm({ ...form, minors: e.target.checked })} />
                <span>A child may be shown <span className="text-ink-3">— nothing is downloaded or sent anywhere; you are guided to report the web address only</span></span></label>
            </div>

            <button className="mt-4 flex items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink" onClick={() => setMore(!more)}>
              <ChevronDown size={14} className={more ? '' : '-rotate-90'} /> More details (other places you saw it, your notes)
            </button>
            {more && (
              <div className="mt-3 space-y-3">
                <div><div className="label mb-1.5">Other places you saw it (one link per line)</div>
                  <textarea className="input mono min-h-20 text-[12px]" placeholder={'https://instagram.com/p/…\nhttps://t.me/channel/123'} value={moreLinks} onChange={(e) => setMoreLinks(e.target.value)} /></div>
                <div><div className="label mb-1.5">What happened, in your words (optional)</div>
                  <textarea className="input min-h-20" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} /></div>
                <div><div className="label mb-1.5">Name for this case (optional)</div>
                  <input className="input" placeholder="Filled in automatically" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
                <label className={`flex items-start gap-2 text-sm ${sensitive ? 'opacity-60' : ''}`}>
                  <input type="checkbox" className="mt-1" disabled={sensitive} checked={form.findCopies && !sensitive} onChange={(e) => setForm({ ...form, findCopies: e.target.checked })} />
                  <span>Also search the web for copies using the page's caption and image
                    <span className="block text-[12px] text-ink-3">{sensitive ? 'Turned off for sensitive cases: nothing is sent to search services unless you choose to later.' : 'The caption text goes to DuckDuckGo, and the image to any image-search service you configured. Links on the page are always followed.'}</span></span>
                </label>
              </div>
            )}
            {err && <div className="mt-3 text-sm text-crit">{err}</div>}
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between">
            <div className="label">{showArchived ? 'Archived cases' : 'Your cases'}</div>
            {archivedCount > 0 && <button className="text-[12px] text-ink-3 underline" onClick={() => setShowArchived(!showArchived)}>{showArchived ? 'Back to active cases' : `Show ${archivedCount} archived`}</button>}
          </div>
          {loading && !cases && <div className="mt-3"><Spinner label="Loading cases" /></div>}
          <div className="mt-3 space-y-2">
            {visible.map((c) => (
              <div key={c.id} className="card group flex items-center gap-3 p-4 transition hover:border-line-strong">
                <Link to={`/case/${c.id}/overview`} className="flex min-w-0 flex-1 items-center gap-3">
                  <FolderOpen size={18} className="shrink-0 text-ink-3" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-medium">{c.title}</div>
                    <div className="text-xs text-ink-3">{INCIDENT_TYPES[c.incident_type]} · {fmtTime(c.created_at, false)} · {c.media_count} media · {c.location_count} places</div>
                  </div>
                </Link>
                {c.is_demo && <span className="tag">DEMO</span>}
                {(c.safety?.intimate || c.safety?.minors_possible) && <ShieldAlert size={16} className="shrink-0 text-warn" />}
                {showArchived && <button className="btn px-2 py-1" title="Restore" onClick={async () => { await api.patch(`/api/cases/${c.id}`, { status: 'open' }); reload() }}><ArchiveRestore size={14} /></button>}
                <button className="btn px-2 py-1 opacity-60 hover:opacity-100" title="Archive or delete this case" onClick={() => setDel(c)}>{showArchived ? <Trash2 size={14} /> : <Archive size={14} />}</button>
              </div>
            ))}
            {cases && visible.length === 0 && (
              <div className="rounded-lg border border-dashed border-line-strong p-6 text-center text-sm text-ink-3">
                {showArchived ? 'Nothing archived.' : 'No cases yet. Paste a link on the left to start your first one.'}
              </div>
            )}
          </div>
        </div>
      </div>
      {del && <DeleteCaseDialog caseId={del.id} title={del.title} onClose={() => setDel(null)} onDone={() => { setDel(null); reload() }} />}
    </div>
  )
}

/* eslint-disable @typescript-eslint/no-explicit-any */
import { CheckCircle2, FlaskConical, Upload, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtTime, vaultUrl } from '../api'
import { casePath } from '../lib/paths'
import { Spinner } from './ui'
import { VerdictBadge } from './VerdictCard'

export function BenchmarkPanel({ blur }: { blur: boolean }) {
  const [list, setList] = useState<any[]>([])
  const [sel, setSel] = useState<string | null>(null)
  const [res, setRes] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [visible, setVisible] = useState(true)

  const loadList = () => api.get<any[]>('/api/lab/benchmarks').then((l) => { setList(l); setSel((s) => s ?? l[0]?.id ?? null) })
  useEffect(() => { loadList() }, [])
  useEffect(() => {
    if (!sel) return
    let stop = false
    let t: number
    const tick = async () => {
      const r = await api.get(`/api/lab/benchmark/${sel}`).catch(() => null)
      if (stop) return
      setRes(r)
      if (r?.job && ['queued', 'running'].includes(r.job.status)) t = window.setTimeout(tick, 2000)
    }
    tick()
    return () => { stop = true; clearTimeout(t) }
  }, [sel])

  const upload = async (files: FileList) => {
    setBusy(true)
    try {
      const fd = new FormData()
      Array.from(files).forEach((f) => fd.append('files', f))
      const r = await api.post(`/api/lab/benchmark?name=${encodeURIComponent(`${files.length} files`)}&visible=${visible}`, fd)
      await loadList()
      setSel(r.benchmark)
    } finally { setBusy(false) }
  }

  const m = res?.metrics
  const running = res?.job && ['queued', 'running'].includes(res.job.status)
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        <FlaskConical size={16} />
        <div className="flex-1">
          <div className="font-semibold">Benchmark — does the verdict match the truth?</div>
          <div className="text-[12.5px] text-ink-3">Drop several labelled files (names like <span className="mono">fake_12.jpg</span> / <span className="mono">real_3.png</span>). Each becomes a case, is fully analysed, and is compared with its label. These are test images only — they are never used to tune the detectors.</div>
        </div>
        <label className="flex items-center gap-2 text-[12px] text-ink-2"><input type="checkbox" checked={visible} onChange={(e) => setVisible(e.target.checked)} /> show as cases in the case list</label>
        <label className={`btn ${busy ? 'opacity-50' : ''}`}><Upload size={14} /> {busy ? 'Uploading…' : 'Add labelled files'}
          <input type="file" hidden multiple accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.length && upload(e.target.files)} /></label>
        <select className="input w-60" value={sel ?? ''} onChange={(e) => setSel(e.target.value)}>
          {list.map((b) => <option key={b.id} value={b.id}>{b.name} · {fmtTime(b.created_at, false)}</option>)}
        </select>
      </div>
      {!res ? <div className="p-5">{list.length ? <Spinner /> : <span className="text-[13px] text-ink-3">No benchmarks yet.</span>}</div> : (
        <div>
          <div className="grid gap-4 border-b border-line p-5 md:grid-cols-4">
            <Stat label="Correct" value={m.accuracy != null ? `${Math.round(m.accuracy * 100)}%` : '—'} sub={`${m.n_labelled} labelled · ${m.n_analysed}/${m.n_total} analysed`} />
            <Stat label="AUC" value={m.auc != null ? m.auc.toFixed(2) : '—'} sub="1.0 = perfect ranking, 0.5 = chance" />
            <Stat label="Fake caught" value={m.confusion ? `${m.confusion.fake_as_fake}/${m.confusion.fake_as_fake + m.confusion.fake_as_real}` : '—'} sub="fakes called fake" />
            <Stat label="Real kept" value={m.confusion ? `${m.confusion.real_as_real}/${m.confusion.real_as_real + m.confusion.real_as_fake}` : '—'} sub="real photos called real" />
          </div>
          {running && <div className="border-b border-line px-5 py-2 text-[12.5px] text-ink-2"><Spinner label={`Analysing ${res.job.message || ''} (${Math.round((res.job.progress ?? 0) * 100)}%)`} /></div>}
          {m.warning && <div className="border-b border-line bg-warn/5 px-5 py-2.5 text-[12.5px] text-ink-2">⚠ {m.warning}</div>}
          <table className="w-full text-[12.5px]">
            <thead><tr className="border-b border-line text-left text-ink-3"><th className="py-2 pl-5 font-medium">Image</th><th className="font-medium">Truth</th><th className="font-medium">Verdict</th><th className="font-medium">Detectors (0 real → 100 fake)</th><th className="pr-5 font-medium">Match</th></tr></thead>
            <tbody>{res.rows.map((r: any) => (
              <tr key={r.media_id} className="border-b border-line/60 align-middle">
                <td className="py-2 pl-5"><Link to={casePath(r.case_id, `decomposition/${r.media_id}`)} className="flex items-center gap-3 hover:underline">
                  {r.thumb ? <img src={vaultUrl(r.case_id, r.thumb)} className={`h-12 w-12 rounded object-cover ${blur ? 'blur-md' : ''}`} /> : <div className="h-12 w-12 rounded bg-raised" />}
                  <span className="mono">{r.filename}</span></Link></td>
                <td>{r.truth ? <span className={`rounded px-1.5 py-0.5 text-[11px] font-semibold ${r.truth === 'fake' ? 'bg-crit/20 text-ink' : 'bg-good/20 text-ink'}`}>{r.truth}</span> : <span className="text-ink-3">?</span>}</td>
                <td>{r.verdict ? <VerdictBadge v={r.verdict} /> : <span className="text-ink-3">{running ? 'pending…' : 'no verdict'}</span>}</td>
                <td className="py-2">{r.detectors && <div className="flex flex-wrap gap-x-3 gap-y-1">{r.detectors.map((d: any) => (
                  <span key={d.name} className="mono text-[11px] text-ink-2" title={d.checks}>{d.name} <b className={d.score >= 0.5 ? 'text-ink' : 'text-ink-3'}>{Math.round(d.score * 100)}</b></span>))}</div>}</td>
                <td className="pr-5">{r.correct == null ? '' : r.correct ? <CheckCircle2 size={16} className="text-good" /> : <XCircle size={16} className="text-crit" />}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return <div><div className="label">{label}</div><div className="mt-1 text-2xl font-semibold">{value}</div><div className="text-[11.5px] text-ink-3">{sub}</div></div>
}

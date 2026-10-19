/* eslint-disable @typescript-eslint/no-explicit-any */
import { Activity, ChevronDown, ChevronRight, Cpu, Database, Eye, EyeOff, FileJson, FlaskConical, HardDrive, Pause, Play, Server, Upload, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtBytes, fmtTime, vaultUrl } from '../api'
import { Brand } from '../components/Brand'
import { BenchmarkPanel } from '../components/BenchmarkPanel'
import { LevelBadge, Spinner, StatusBadge } from '../components/ui'

const OUTCOME: Record<string, { label: string; color: string }> = {
  ok: { label: 'ran', color: 'var(--color-good)' },
  not_run: { label: 'not checked', color: 'var(--color-ink-3)' },
  error: { label: 'error', color: 'var(--color-crit)' },
}

export function TopNav({ active }: { active: 'cases' | 'lab' | 'settings' }) {
  const item = (to: string, k: string, label: string) => (
    <Link to={to} aria-current={active === k ? 'page' : undefined} className="nav-item !px-3 !py-1.5 text-[13px] font-medium">{label}</Link>
  )
  return (
    <div className="flex items-center justify-between">
      <Brand />
      <nav aria-label="Main" className="flex gap-1">{item('/', 'cases', 'Cases')}{item('/lab', 'lab', 'Lab · backend')}{item('/settings', 'settings', 'Settings')}</nav>
    </div>
  )
}

export default function Lab() {
  const [sys, setSys] = useState<any>(null)
  const [runs, setRuns] = useState<any[]>([])
  const [sel, setSel] = useState<{ case_id: string; media_id: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [blur, setBlur] = useState(false)

  const loadSys = () => api.get('/api/system').then(setSys).catch((e) => setErr(String(e.message)))
  const loadRuns = () => api.get<any[]>('/api/lab/runs').then((r) => { setRuns(r); setSel((s) => s ?? (r[0] ? { case_id: r[0].case_id, media_id: r[0].media_id } : null)) })
  useEffect(() => {
    loadSys(); loadRuns()
    const t = setInterval(loadSys, 3000)
    return () => clearInterval(t)
  }, [])

  const upload = async (f: File) => {
    setBusy(true); setErr('')
    try {
      const fd = new FormData()
      fd.append('file', f)
      const r = await api.post('/api/lab/analyze', fd)
      setSel({ case_id: r.case_id, media_id: r.media_id })
      loadRuns()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="mx-auto max-w-[1500px] px-8 py-6">
      <TopNav active="lab" />
      <div className="mt-6 flex items-end justify-between">
        <div>
          <div className="label flex items-center gap-1.5"><FlaskConical size={12} /> Lab</div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Backend & raw analyzer output</h1>
          <p className="mt-1 text-ink-3">Drop a file to see exactly what every analyzer produced. GPU, model and job status and the live backend log are below. Nothing is interpreted here.</p>
        </div>
        <div className="flex gap-2">
          <button className={`btn ${blur ? 'btn-primary' : ''}`} onClick={() => setBlur(!blur)}>{blur ? <EyeOff size={14} /> : <Eye size={14} />} Blur previews</button>
          <button className="btn" onClick={async () => { await api.post('/api/system/warmup'); loadSys() }}><Zap size={14} /> Load models onto GPU</button>
        </div>
      </div>
      {err && <div className="mt-3 text-sm text-crit">{err}</div>}

      {!sys ? <div className="mt-6"><Spinner label="Contacting backend" /></div> : <SystemCards sys={sys} />}

      <div className="mt-5 grid gap-5 xl:grid-cols-[300px_1fr]">
        <div className="space-y-4">
          <label className={`card flex cursor-pointer flex-col items-center justify-center gap-2 border-dashed p-6 text-center hover:border-line-strong ${busy ? 'opacity-60' : ''}`}
            onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) upload(f) }}>
            <Upload size={22} className="text-ink-2" />
            <div className="font-semibold">{busy ? 'Uploading…' : 'Drop or choose a file'}</div>
            <div className="text-[12px] text-ink-3">Image, video or audio. It's stored in a scratch lab case (hashed and logged) and analysed right away.</div>
            <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </label>
          <div className="card p-3">
            <div className="label mb-2 px-1">Recent lab runs</div>
            {runs.length === 0 && <div className="px-1 text-[12px] text-ink-3">None yet.</div>}
            <div className="space-y-1">{runs.map((r) => (
              <button key={r.case_id} onClick={() => setSel({ case_id: r.case_id, media_id: r.media_id })}
                className={`w-full rounded-md px-2.5 py-2 text-left text-[12.5px] ${sel?.media_id === r.media_id ? 'bg-raised' : 'hover:bg-raised/50'}`}>
                <div className="truncate font-medium">{r.filename}</div>
                <div className="text-[11px] text-ink-3">{r.modality} · {fmtTime(r.created_at, false)}</div>
              </button>
            ))}</div>
          </div>
        </div>
        <div className={blur ? '[&_img]:blur-xl' : ''}>{sel ? <Result key={sel.media_id} {...sel} /> : <div className="card p-10 text-center text-ink-3">Upload a file to see raw analyzer output.</div>}</div>
      </div>

      <div className="mt-5"><BenchmarkPanel blur={blur} /></div>

      <div className="mt-5 grid gap-5 xl:grid-cols-[1fr_420px]">
        <LogPanel />
        <JobsPanel jobs={sys?.jobs ?? []} />
      </div>
    </div>
  )
}

function Bar({ value, max, color = 'var(--color-ink-2)' }: { value: number; max: number; color?: string }) {
  return <div className="h-1.5 w-full overflow-hidden rounded bg-raised"><div className="h-full rounded transition-all" style={{ width: `${Math.min(100, (value / Math.max(1, max)) * 100)}%`, background: color }} /></div>
}

function SystemCards({ sys }: { sys: any }) {
  const g = sys.gpu
  const up = sys.uptime_s
  return (
    <div className="mt-5 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
      <div className="card p-4">
        <div className="label flex items-center gap-1.5"><Server size={12} /> Backend</div>
        <div className="mt-2 flex items-center gap-2 text-lg font-semibold"><span className="h-2.5 w-2.5 rounded-full bg-good" /> Online</div>
        <div className="mt-1 space-y-0.5 text-[12px] text-ink-3">
          <div>v{sys.app_version} · up {Math.floor(up / 3600)}h {Math.floor((up % 3600) / 60)}m</div>
          <div>Python {sys.python} · {sys.os}</div>
          <div className="truncate" title={sys.ffmpeg}>{sys.ffmpeg.split(' Copyright')[0]}</div>
        </div>
      </div>
      <div className="card p-4">
        <div className="label flex items-center gap-1.5"><Cpu size={12} /> GPU</div>
        {g.cuda_available ? <>
          <div className="mt-2 font-semibold">{g.name} <span className="text-[12px] font-normal text-ink-3">sm_{g.capability?.replace('.', '')} · CUDA {g.cuda_runtime}</span></div>
          <div className="mt-2 flex justify-between text-[12px] text-ink-3"><span>VRAM {g.vram_used_mb} / {g.vram_total_mb} MB</span><span>models {g.torch_allocated_mb} MB</span></div>
          <Bar value={g.vram_used_mb} max={g.vram_total_mb} color={g.vram_used_mb / g.vram_total_mb > 0.85 ? 'var(--color-crit)' : 'var(--color-ink-2)'} />
          <div className="mt-2 flex justify-between text-[12px] text-ink-3"><span>Utilisation {g.utilization_pct ?? '?'}%</span><span>{g.temperature_c ?? '?'}°C · {g.sm_clock_mhz ?? '?'} MHz · {g.pstate}</span></div>
          <Bar value={+(g.utilization_pct ?? 0)} max={100} />
          <div className="mt-1 text-[11px] text-ink-3">torch {g.torch} · driver {g.driver}</div>
        </> : <div className="mt-2 text-[13px] text-ink-3">CUDA not available — running on CPU{g.torch ? ` (torch ${g.torch})` : ''}.</div>}
      </div>
      <div className="card p-4">
        <div className="label flex items-center gap-1.5"><Database size={12} /> Models</div>
        <div className="mt-2 space-y-1.5">
          {sys.models.map((m: any) => {
            const rt = m.key === 'ufd' ? sys.runtime.clip : m.key === 'whisper' ? sys.runtime.whisper : null
            return (
              <div key={m.key} className="flex items-center gap-2 text-[12.5px]">
                <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: m.installed ? 'var(--color-good)' : 'var(--color-ink-3)' }} />
                <span className="flex-1 truncate" title={m.title}>{({ ufd: 'CLIP / UFD (legacy)', yunet: 'YuNet faces', whisper: 'Whisper small', spai: 'SPAI · AI-image', trufor: 'TruFor · edits', sbi: 'SBI · face swaps' } as any)[m.key] ?? m.key}</span>
                <span className="mono shrink-0 text-[10.5px] text-ink-3">{!m.installed ? 'not installed' : (() => { const g = (sys.gpu_models ?? []).find((x: any) => x.key === (m.key === 'ufd' ? 'clip' : m.key)); return g ? `${g.device} · loaded` : rt?.loaded ? `${rt.device}` : 'on disk' })()}</span>
              </div>
            )
          })}
        </div>
        <div className="mt-2 text-[11px] text-ink-3">"on disk" models load onto the GPU on first use or with <i>Load models</i>.</div>
      </div>
      <div className="card p-4">
        <div className="label flex items-center gap-1.5"><HardDrive size={12} /> Analyzers & storage</div>
        <div className="mt-2 text-lg font-semibold">{sys.analyzers.filter((a: any) => a.available).length} / {sys.analyzers.length} <span className="text-[12px] font-normal text-ink-3">analyzers available</span></div>
        <div className="mt-1 flex flex-wrap gap-1">{sys.analyzers.map((a: any) => (
          <span key={a.name} title={a.available ? a.title : `${a.title}: ${a.reason}`} className={`rounded border px-1.5 py-0.5 text-[10px] ${a.available ? 'border-line-strong text-ink-2' : 'border-dashed border-line text-ink-3'}`}>{a.name}</span>
        ))}</div>
        <div className="mt-2 text-[11px] text-ink-3">Evidence data {sys.storage.data_mb} MB · models {sys.storage.models_mb} MB</div>
      </div>
    </div>
  )
}

function Result({ case_id, media_id }: { case_id: string; media_id: string }) {
  const [r, setR] = useState<any>(null)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  useEffect(() => {
    let stop = false
    const tick = async () => {
      const d = await api.get(`/api/lab/result/${case_id}/${media_id}`).catch(() => null)
      if (stop) return
      if (d) setR(d)
      if (!d || !d.job || ['queued', 'running'].includes(d.job.status)) setTimeout(tick, 1500)
    }
    tick()
    return () => { stop = true }
  }, [case_id, media_id])
  if (!r) return <Spinner label="Loading" />
  const running = r.job && ['queued', 'running'].includes(r.job.status)
  const maxDur = Math.max(0.01, ...r.runs.map((x: any) => x.duration_s ?? 0))
  const total = r.runs.reduce((a: number, x: any) => a + (x.duration_s ?? 0), 0)
  return (
    <div className="card overflow-hidden">
      <div className="flex gap-4 border-b border-line p-4">
        {r.thumb ? <img src={vaultUrl(case_id, r.thumb)} className="h-24 w-32 rounded bg-black object-contain" /> : <div className="flex h-24 w-32 items-center justify-center rounded bg-raised text-ink-3"><Activity /></div>}
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold">{r.media.filename}</div>
          <div className="text-[12px] text-ink-3">{r.media.modality} · {r.media.mime} · {fmtBytes(r.media.size)}{r.media.width ? ` · ${r.media.width}×${r.media.height}` : ''}{r.media.duration ? ` · ${r.media.duration.toFixed(1)} s` : ''}</div>
          <div className="mono mt-1 truncate text-[10.5px] text-ink-3">sha256 {r.media.sha256}</div>
          <div className="mt-2 flex items-center gap-3">
            {running ? <><Spinner label={`${r.job.message || 'queued'} · ${Math.round((r.job.progress ?? 0) * 100)}%`} /></> : r.explanation && <LevelBadge level={r.explanation.level} />}
            {r.job?.status === 'error' && <span className="text-[12px] text-crit">job failed — see log below</span>}
            {!running && <span className="text-[12px] text-ink-3">{r.runs.length} analyzers · {total.toFixed(1)} s total</span>}
            <Link className="ml-auto text-[12px] text-ink-3 underline" to={`/case/${case_id}/decomposition/${media_id}`}>open as full case →</Link>
          </div>
        </div>
      </div>
      <table className="w-full text-[12.5px]">
        <thead><tr className="border-b border-line text-left text-ink-3"><th className="w-8" /><th className="py-2 font-medium">Analyzer</th><th className="font-medium">Outcome</th><th className="w-56 font-medium">Time</th><th className="pr-4 text-right font-medium">Findings</th></tr></thead>
        <tbody>
          {r.runs.map((x: any) => {
            const o = OUTCOME[x.outcome] ?? OUTCOME.error
            const isOpen = open[x.component] ?? x.outcome === 'error'
            return [
              <tr key={x.id} className="cursor-pointer border-b border-line/60 hover:bg-raised/40" onClick={() => setOpen({ ...open, [x.component]: !isOpen })}>
                <td className="pl-3 text-ink-3">{isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</td>
                <td className="py-2"><span className="mono">{x.component}</span> <span className="text-[11px] text-ink-3">v{x.version}</span></td>
                <td><span className="inline-flex items-center gap-1.5 font-medium"><span className="h-2 w-2 rounded-full" style={{ background: o.color }} />{o.label}</span></td>
                <td><div className="flex items-center gap-2"><div className="w-36"><Bar value={x.duration_s ?? 0} max={maxDur} /></div><span className="mono text-[11px] text-ink-3">{x.duration_s?.toFixed(2)} s</span></div></td>
                <td className="pr-4 text-right text-ink-2">{x.claims.length}{x.artifacts.length ? ` · ${x.artifacts.length} artifact${x.artifacts.length > 1 ? 's' : ''}` : ''}</td>
              </tr>,
              isOpen && (
                <tr key={x.id + '-d'} className="border-b border-line/60 bg-bg/40">
                  <td />
                  <td colSpan={4} className="py-3 pr-4">
                    {x.reason && <div className={`mb-2 text-[12px] ${x.outcome === 'error' ? 'text-crit' : 'text-ink-3'}`}>{x.reason}{x.how_to_enable && <span className="mono ml-2 text-ink-3">↳ {x.how_to_enable}</span>}</div>}
                    <div className="space-y-2">{x.claims.map((c: any) => (
                      <div key={c.id} className="rounded border border-line p-2">
                        <div className="flex items-start gap-2"><StatusBadge status={c.status} /><span className="flex-1">{c.statement}</span><span className="text-[11px] text-ink-3">{c.strength}{c.polarity !== 'neutral' ? ` · ${c.polarity.replace('_', ' ')}` : ''}</span></div>
                        {Object.keys(c.data ?? {}).length > 0 && <pre className="mono mt-1.5 max-h-48 overflow-auto rounded bg-bg/70 p-2 text-[10.5px] text-ink-3">{JSON.stringify(c.data, null, 1)}</pre>}
                      </div>
                    ))}</div>
                    {x.artifacts.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{x.artifacts.map((a: any) => a.mime === 'image/png'
                      ? <a key={a.id} href={vaultUrl(case_id, a.sha256)} target="_blank" rel="noreferrer" title={a.label}><img src={vaultUrl(case_id, a.sha256)} className="h-24 rounded border border-line bg-black object-contain" /></a>
                      : <a key={a.id} href={vaultUrl(case_id, a.sha256)} target="_blank" rel="noreferrer" className="btn px-2 py-1 text-[11px]"><FileJson size={12} /> {a.kind}</a>)}</div>}
                  </td>
                </tr>
              ),
            ]
          })}
        </tbody>
      </table>
      {!running && Object.keys(r.fingerprints).length > 0 && (
        <details className="border-t border-line p-4 text-[12px]">
          <summary className="cursor-pointer text-ink-3">Fingerprints (used for genealogy matching)</summary>
          <pre className="mono mt-2 max-h-64 overflow-auto text-[10.5px] text-ink-3">{JSON.stringify(r.fingerprints, null, 1)}</pre>
        </details>
      )}
    </div>
  )
}

const LEVEL_COLOR: Record<string, string> = { ERROR: 'var(--color-crit)', WARNING: 'var(--color-warn)', INFO: 'var(--color-ink-3)' }

function LogPanel() {
  const [lines, setLines] = useState<any[]>([])
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState('')
  const last = useRef(0)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (paused) return
    const t = setInterval(async () => {
      const n = await api.get<any[]>(`/api/system/logs?after=${last.current}`).catch(() => [])
      if (n.length) {
        last.current = n[n.length - 1].seq
        setLines((l) => [...l, ...n].slice(-800))
      }
    }, 1500)
    return () => clearInterval(t)
  }, [paused])
  useEffect(() => {
    const el = box.current
    if (el && !paused) el.scrollTop = el.scrollHeight
  }, [lines, paused])
  const shown = lines.filter((l) => !filter || `${l.logger} ${l.message}`.toLowerCase().includes(filter.toLowerCase()))
  return (
    <div className="card overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <span className="font-semibold">Backend log</span><span className="text-[11px] text-ink-3">live · last {lines.length}</span>
        <div className="ml-auto w-56"><input className="input py-1 text-[12px]" placeholder="filter (e.g. ufd_clip, job, error)" value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        <button className="btn px-2 py-1" onClick={() => setPaused(!paused)} title={paused ? 'Resume' : 'Pause'}>{paused ? <Play size={13} /> : <Pause size={13} />}</button>
      </div>
      <div ref={box} className="mono h-80 overflow-y-auto bg-black/40 px-4 py-2 text-[11px] leading-5">
        {shown.length === 0 && <div className="text-ink-3">Waiting for log lines… (upload a file or open a case)</div>}
        {shown.map((l) => (
          <div key={l.seq} className="whitespace-pre-wrap break-all">
            <span className="text-ink-3">{l.time.slice(11, 19)}</span>{' '}
            <span style={{ color: LEVEL_COLOR[l.level] ?? 'var(--color-ink-3)' }}>{l.level.padEnd(7)}</span>{' '}
            <span className="text-ink-3">{l.logger}</span>{' '}
            <span className="text-ink-2">{l.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function JobsPanel({ jobs }: { jobs: any[] }) {
  const color: Record<string, string> = { done: 'var(--color-good)', running: 'var(--color-warn)', queued: 'var(--color-ink-3)', error: 'var(--color-crit)' }
  return (
    <div className="card overflow-hidden">
      <div className="border-b border-line px-4 py-2.5 font-semibold">Recent jobs <span className="text-[11px] font-normal text-ink-3">(all cases)</span></div>
      <div className="h-80 overflow-y-auto">
        {jobs.map((j) => (
          <div key={j.id} className="border-b border-line/60 px-4 py-2 text-[12px]" title={j.error || ''}>
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ background: color[j.status] }} />
              <span className="font-medium">{j.kind}</span><span className="text-ink-3">{j.status}</span>
              <span className="ml-auto text-[11px] text-ink-3">{fmtTime(j.created_at, false)}</span>
            </div>
            <div className="mt-0.5 truncate text-[11px] text-ink-3">{j.status === 'error' ? <span className="text-crit">{j.error.split('\n')[0]}</span> : j.message}{j.case_id && <span className="mono"> · {j.case_id}</span>}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

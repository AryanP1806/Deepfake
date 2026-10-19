import { AudioLines, ChevronLeft, ChevronRight, ChevronRight as Go, Cpu, Database, FileText, Image as ImageIcon, Lightbulb, MemoryStick, Search, Server } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDashboard } from '../../dashboard'
import { Spinner } from '../ui'

const gb = (mb: number | null | undefined) => (mb == null ? '–' : (mb / 1024).toFixed(1))

export function SystemStatus() {
  const { data, error } = useDashboard()
  const s = data?.system
  const ok = !!s?.backend.running
  const gpu = s?.gpu
  const pct = gpu?.vram_total_mb ? Math.round(((gpu.vram_used_mb ?? 0) / gpu.vram_total_mb) * 100) : null
  return (
    <section className="card p-5" aria-labelledby="sys-h" aria-live="polite">
      <h2 id="sys-h" className="display text-[19px] font-bold">System Status</h2>
      {!data && !error && <div className="mt-3"><Spinner label="Checking the backend" /></div>}
      {error && !data && <p className="mt-3 flex items-center gap-2 text-[13.5px] font-semibold text-crit"><span className="dot" style={{ '--d': 'var(--color-crit)' } as React.CSSProperties} /> Backend not reachable</p>}
      {s && (
        <>
          <p className="mt-2 flex items-center gap-2 text-[14px] font-medium text-ink"><span className="dot" style={{ '--d': ok ? 'var(--color-good)' : 'var(--color-crit)' } as React.CSSProperties} />{ok ? 'All systems operational' : 'Backend not running'}</p>
          <dl className="well mt-3 divide-y divide-line text-[13.5px]">
            <div className="flex items-center gap-3 px-3.5 py-3">
              <Server size={19} className="shrink-0 text-ink-2" aria-hidden /><dt className="w-20 shrink-0 text-ink">Backend</dt>
              <dd className="ml-auto flex items-center gap-1 font-medium" style={{ color: 'var(--color-v-real)' }}>{ok ? 'Running' : 'Stopped'}<Link to="/lab" aria-label="Open the Lab for backend details"><Go size={15} className="text-ink-3" aria-hidden /></Link></dd>
            </div>
            <div className="flex items-center gap-3 px-3.5 py-3">
              <Cpu size={19} className="shrink-0 text-ink-2" aria-hidden /><dt className="w-20 shrink-0 text-ink">GPU <span className="text-ink-3">(VRAM)</span></dt>
              {gpu?.available ? (
                <dd className="ml-auto flex min-w-0 flex-1 items-center justify-end gap-3">
                  <span className="min-w-0 text-right"><span className="block whitespace-nowrap text-[12.5px] text-ink-2">{gb(gpu.vram_used_mb)} / {gb(gpu.vram_total_mb)} GB</span>
                    <span className="meter mt-1 block w-28" role="img" aria-label={`GPU memory ${pct}% used`}><i style={{ width: `${pct}%`, background: (pct ?? 0) > 85 ? 'var(--color-v-fake)' : (pct ?? 0) > 65 ? 'var(--color-v-likely-fake)' : 'var(--color-accent)' }} /></span></span>
                  <span className="w-9 text-right text-[12.5px] text-ink-2">{pct}%</span>
                </dd>
              ) : <dd className="ml-auto text-right text-ink-2" title={gpu?.name ?? undefined}>None · using CPU</dd>}
            </div>
            {s.models.map((m) => (
              <div key={m.key} className="flex items-center gap-3 px-3.5 py-3">
                {m.key === 'clip' ? <MemoryStick size={19} className="shrink-0 text-ink-2" aria-hidden /> : <AudioLines size={19} className="shrink-0 text-ink-2" aria-hidden />}
                <dt className="w-20 shrink-0 text-ink">{m.title.replace(' model', ' Model')}</dt>
                <dd className="ml-auto flex items-center gap-2 text-ink-2">
                  <span className="dot" style={{ '--d': m.state === 'loaded' ? 'var(--color-good)' : m.state === 'idle' ? 'var(--color-v-uncertain)' : 'var(--color-ink-3)' } as React.CSSProperties} />
                  <span className="whitespace-nowrap">{m.detail}</span>
                  {m.mb ? <span className="whitespace-nowrap text-ink-3">{gb(m.mb)} GB</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  )
}

const ACTIONS = [
  { to: '/lab', title: 'Media Lab', sub: 'Run detailed analysis', icon: ImageIcon, hue: 'var(--hue-1)' },
  { to: '/url-search', title: 'URL Search', sub: 'Find related copies', icon: Search, hue: 'var(--hue-1)' },
  { to: '/vault', title: 'Evidence Vault', sub: 'View preserved files', icon: Database, hue: 'var(--hue-1)' },
  { to: '/reports', title: 'Generate Report', sub: 'Create investigation report', icon: FileText, hue: 'var(--hue-1)' },
]
export function QuickActions() {
  return (
    <section className="card p-5" aria-labelledby="qa-h">
      <h2 id="qa-h" className="display text-[19px] font-bold">Quick Actions</h2>
      <ul className="mt-3 grid grid-cols-2 gap-3">
        {ACTIONS.map((a) => (
          <li key={a.to}>
            <Link to={a.to} className="tile flex h-full flex-col gap-2 p-3.5">
              <span className="flex items-center gap-2 text-[13.5px] font-semibold text-ink"><a.icon size={18} className="shrink-0 text-accent-text" aria-hidden />{a.title}</span>
              <span className="text-[12px] leading-snug text-ink-3">{a.sub}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}

const TIPS = [
  { title: 'Use a public URL or the original file', body: 'For best results, provide the original media or a public post URL. Screenshots and low-quality copies may still be analysed, but some evidence can be lost.' },
  { title: 'A missing signal is not a clean result', body: 'No metadata, watermark or detector hit never proves a file is real. Platforms strip metadata, and new generators are often missed. “Unknown” means not established.' },
  { title: 'Sensitive content stays on this computer', body: 'Tick the safety boxes for intimate media or a possible child. Previews are blurred and nothing is sent to outside services without your consent.' },
  { title: 'Leads are not accusations', body: 'Accounts that share a fake are not shown to know each other. Treat every lead as something to confirm with authorised evidence, never as proof.' },
]
export function Tips() {
  const [i, setI] = useState(0)
  const t = TIPS[i]
  return (
    <section className="card p-5" aria-roledescription="carousel" aria-labelledby="tips-h">
      <h2 id="tips-h" className="display text-[19px] font-bold">Tips &amp; Guidance</h2>
      <div className="well mt-3 flex items-center gap-2 p-3">
        <button className="btn !min-h-[34px] !w-[34px] shrink-0 justify-center !rounded-full !p-0" onClick={() => setI((i + TIPS.length - 1) % TIPS.length)} aria-label="Previous tip"><ChevronLeft size={16} aria-hidden /></button>
        <div className="flex min-w-0 flex-1 gap-3 px-1" role="group" aria-roledescription="slide" aria-label={`Tip ${i + 1} of ${TIPS.length}`} aria-live="polite">
          <Lightbulb size={20} className="mt-0.5 shrink-0 text-accent-text" aria-hidden />
          <div><div className="text-[13.5px] font-semibold text-ink">{t.title}</div><p className="mt-1 text-[12.5px] leading-snug text-ink-2">{t.body}</p></div>
        </div>
        <button className="btn !min-h-[34px] !w-[34px] shrink-0 justify-center !rounded-full !p-0" onClick={() => setI((i + 1) % TIPS.length)} aria-label="Next tip"><ChevronRight size={16} aria-hidden /></button>
      </div>
      <div className="mt-3 flex justify-center gap-2" role="tablist" aria-label="Choose a tip">
        {TIPS.map((_, k) => <button key={k} role="tab" aria-selected={k === i} aria-label={`Tip ${k + 1}`} onClick={() => setI(k)} className="h-2 w-2 rounded-full border-0" style={{ background: k === i ? 'var(--color-accent)' : 'var(--color-line-strong)', opacity: k === i ? 1 : 0.55 }} />)}
      </div>
    </section>
  )
}

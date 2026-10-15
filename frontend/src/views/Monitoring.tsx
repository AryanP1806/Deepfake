/* eslint-disable @typescript-eslint/no-explicit-any */
import { BellRing, Play } from 'lucide-react'
import { api, fmtTime } from '../api'
import { Empty, PageHeader, Section, Spinner } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'
import { StateTag } from './Propagation'

const KIND: Record<string, { label: string; tone: string }> = {
  removed: { label: 'Removed', tone: 'var(--color-good)' }, restored: { label: 'Back online', tone: 'var(--color-crit)' },
  changed: { label: 'Changed', tone: 'var(--color-warn)' }, new_copy: { label: 'New version', tone: 'var(--color-crit)' },
  new_snapshot: { label: 'Archived', tone: 'var(--color-ink-2)' }, error: { label: 'Check failed', tone: 'var(--color-ink-3)' },
}

export default function Monitoring() {
  const { caseId, tick, toast } = useCase()
  const { data, reload } = useApi<any>(`/api/cases/${caseId}/monitor`, [tick])
  const { data: locs } = useApi<any[]>(`/api/cases/${caseId}/locations`, [tick])
  if (!data) return <Spinner />
  const cfg = data.config
  const set = async (enabled: boolean, interval = cfg.interval_minutes) => { await api.put(`/api/cases/${caseId}/monitor`, { enabled, interval_minutes: interval }); reload() }
  return (
    <div className="max-w-5xl">
      <PageHeader step="Monitor" title="Continuous incident monitoring"
        question="Track re-uploads, altered versions, removals and changes after reporting. Every re-check is itself captured as evidence."
        right={<button className="btn" onClick={async () => { await api.post(`/api/cases/${caseId}/monitor/run`); toast('Monitoring pass started') }}><Play size={14} /> Run a check now</button>} />
      <div className="grid gap-5 md:grid-cols-[320px_1fr]">
        <Section title="Schedule">
          <label className="flex items-center gap-3 text-[13px]"><input type="checkbox" checked={cfg.enabled} onChange={(e) => set(e.target.checked)} /> Monitoring enabled</label>
          <div className="mt-3"><div className="label mb-1.5">Re-check every</div>
            <select className="input" value={cfg.interval_minutes} onChange={(e) => set(cfg.enabled, +e.target.value)}>
              {[60, 180, 360, 720, 1440].map((m) => <option key={m} value={m}>{m < 1440 ? `${m / 60} h` : '24 h'}</option>)}
            </select></div>
          <div className="mt-3 text-[12px] text-ink-3">Last run: {fmtTime(cfg.last_run_at)}</div>
          <ul className="mt-4 list-disc space-y-1 pl-4 text-[12px] text-ink-3">
            <li>Re-captures every known URL and detects removal, restoration or content change</li>
            <li>Ingests and matches any new media version found at those URLs</li>
            <li>Checks the Internet Archive for new snapshots</li>
            <li>Runs search adapters you have enabled with keys</li>
          </ul>
        </Section>
        <div className="space-y-5">
          <Section title="Alerts" right={<button className="btn px-2 py-1 text-[11px]" onClick={async () => { await api.post(`/api/cases/${caseId}/alerts/seen`); reload() }}>Mark all seen</button>}>
            {data.alerts.length === 0 ? <Empty>No changes detected yet.</Empty> : (
              <div className="space-y-1.5">{data.alerts.map((a: any) => (
                <div key={a.id} className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-[13px] ${a.seen ? 'border-line' : 'border-line-strong bg-raised/50'}`}>
                  <BellRing size={14} style={{ color: KIND[a.kind]?.tone }} />
                  <span className="w-28 shrink-0 font-semibold">{KIND[a.kind]?.label ?? a.kind}</span>
                  <span className="min-w-0 flex-1 break-all text-ink-2">{a.message}</span>
                  <span className="shrink-0 text-[11px] text-ink-3">{fmtTime(a.created_at, false)}</span>
                </div>
              ))}</div>
            )}
          </Section>
          <Section title="Watched locations">
            <div className="space-y-1">{(locs ?? []).map((l) => (
              <div key={l.id} className="flex items-center gap-3 border-b border-line/60 py-1.5 text-[12.5px]">
                <StateTag s={l.state} /><span className="w-40 shrink-0 text-ink-2">{l.platform}</span><span className="mono min-w-0 flex-1 truncate text-[11px] text-ink-3">{l.url}</span>
                <span className="shrink-0 text-[11px] text-ink-3">checked {fmtTime(l.last_checked_at, false)}</span>
              </div>
            ))}</div>
          </Section>
        </div>
      </div>
    </div>
  )
}

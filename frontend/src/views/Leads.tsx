/* eslint-disable @typescript-eslint/no-explicit-any */
import { Scale } from 'lucide-react'
import { Empty, PageHeader, Spinner, StatusBadge } from '../components/ui'
import { useCase } from '../context'
import { useApi } from '../hooks'

const STRENGTH_TONE: Record<string, string> = { Strong: 'var(--color-ink)', Moderate: 'var(--color-ink-2)', Weak: 'var(--color-ink-3)' }

export default function Leads() {
  const { caseId, tick } = useCase()
  const { data } = useApi<any>(`/api/cases/${caseId}/leads`, [tick])
  return (
    <div className="max-w-5xl">
      <PageHeader step="Investigate" title="Attribution leads"
        question="Who or what is connected — and what would it take to attribute the media to anyone?" />
      {!data ? <Spinner /> : <>
        <div className="mb-5 flex gap-3 rounded-lg border border-line-strong bg-raised/50 p-4 text-[13px] text-ink-2">
          <Scale size={18} className="shrink-0 text-ink" />
          <div><b className="text-ink">Leads, not accusations.</b> {data.principle} Each lead has five parts: what was discovered → how strong the evidence is → what it suggests → what remains unknown → what evidence attribution would require.</div>
        </div>
        {data.leads.length === 0 && <Empty>No leads yet. Capture locations under Propagation to connect accounts to the media.</Empty>}
        <div className="space-y-4">
          {data.leads.map((l: any, i: number) => (
            <div key={l.id} className="card overflow-hidden">
              <div className="flex items-center justify-between border-b border-line px-5 py-3">
                <div className="flex items-center gap-3"><span className="mono text-[11px] text-ink-3">#{i + 1}</span><span className="font-semibold">{l.subject}</span>
                  <span className="rounded border border-line px-1.5 text-[10px] uppercase tracking-wider text-ink-3">{l.kind.replace('_', ' ')}</span></div>
                <div className="text-right text-[12px]"><span className="text-ink-3">Evidence strength </span><b style={{ color: STRENGTH_TONE[l.evidence_strength] }}>{l.evidence_strength}</b>
                  {l.strength_scope && <div className="text-[10.5px] text-ink-3">{l.strength_scope}</div>}</div>
              </div>
              <div className="grid divide-line md:grid-cols-5 md:divide-x">
                <Col n="1" t="Discovered">
                  <ul className="space-y-1.5">{l.discovered.map((d: any, j: number) => <li key={j} className="flex gap-1.5"><StatusBadge status={d.status} compact /><span>{d.text}</span></li>)}</ul>
                </Col>
                <Col n="2" t="Evidence strength"><b>{l.evidence_strength}</b></Col>
                <Col n="3" t="What it suggests">{l.suggests}</Col>
                <Col n="4" t="Still unknown"><ul className="list-disc space-y-1 pl-4">{l.unknown.map((u: string) => <li key={u}>{u}</li>)}</ul></Col>
                <Col n="5" t="Required for attribution"><ul className="list-disc space-y-1 pl-4">{l.required.map((u: string) => <li key={u}>{u}</li>)}</ul></Col>
              </div>
            </div>
          ))}
        </div>
      </>}
    </div>
  )
}

function Col({ n, t, children }: { n: string; t: string; children: React.ReactNode }) {
  return (
    <div className="p-4 text-[12.5px] text-ink-2">
      <div className="label mb-2"><span className="mono mr-1">{n}</span>{t}</div>
      {children}
    </div>
  )
}

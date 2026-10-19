/* eslint-disable @typescript-eslint/no-explicit-any */
import { ArrowRight, CheckCircle2, Circle, OctagonX } from 'lucide-react'
import { Link } from 'react-router-dom'
import { PageHeader, Spinner } from '../components/ui'
import { useCase } from '../context'
import { casePath } from '../lib/paths'
import { useApi } from '../hooks'

const ACTION_LINK: Record<string, string> = { capture: 'spread', add_urls: 'spread', complaint: 'takedown', escalate: 'takedown', monitor: 'monitor' }

export default function NextAction() {
  const { caseId, tick } = useCase()
  const { data } = useApi<any>(`/api/cases/${caseId}/next-action`, [tick])
  if (!data) return <Spinner />
  const phases = [...new Set<string>(data.checklist.map((c: any) => c.phase))]
  return (
    <div className="max-w-4xl">
      <PageHeader step="Act" title="What should I do now?" question="The safest, highest-value next step given the evidence — and what it does not yet prove." />
      <div className="card border-ink/40 p-6">
        <div className="label">Do this next</div>
        <div className="mt-2 text-2xl font-semibold leading-snug">{data.primary.title}</div>
        <p className="mt-2 max-w-2xl text-ink-2">{data.primary.why}</p>
        <div className="mt-4 flex gap-2">
          {ACTION_LINK[data.primary.action] && <Link className="btn btn-primary" to={casePath(caseId, ACTION_LINK[data.primary.action])}>Go <ArrowRight size={14} /></Link>}
          {data.primary.link && <a className="btn" href={data.primary.link} target="_blank" rel="noreferrer">{data.primary.link}</a>}
        </div>
      </div>
      {data.dont.length > 0 && (
        <div className="card mt-4 p-5">
          <div className="label mb-2">Avoid</div>
          <ul className="space-y-1.5">{data.dont.map((d: string) => <li key={d} className="flex gap-2 text-[13px] text-ink-2"><OctagonX size={15} className="shrink-0 text-crit" />{d}</li>)}</ul>
        </div>
      )}
      <div className="mt-5 grid gap-4 md:grid-cols-2">
        {phases.map((p) => (
          <div key={p} className="card p-5">
            <div className="label mb-2">{p}</div>
            <ul className="space-y-2.5">{data.checklist.filter((c: any) => c.phase === p).map((c: any) => (
              <li key={c.title} className="flex gap-2.5 text-[13px]">
                {c.done ? <CheckCircle2 size={16} className="shrink-0 text-good" /> : <Circle size={16} className="shrink-0 text-ink-3" />}
                <div><div className={c.done ? 'text-ink-3 line-through' : ''}>{c.title}</div><div className="text-[12px] text-ink-3">{c.why}</div></div>
              </li>
            ))}</ul>
          </div>
        ))}
      </div>
      <div className="card mt-5 p-5">
        <div className="label mb-2">Biggest open questions</div>
        {data.top_gaps.map((g: any) => <div key={g.id} className="border-b border-line/60 py-2 text-[13px] last:border-0"><b>{g.question}</b><div className="text-ink-3">→ {g.next_evidence}</div></div>)}
        <Link to={casePath(caseId, 'gaps')} className="mt-2 inline-block text-[12px] text-ink-3 underline">All evidence gaps →</Link>
      </div>
      <p className="mt-4 text-[12px] text-ink-3">{data.disclaimer}</p>
    </div>
  )
}

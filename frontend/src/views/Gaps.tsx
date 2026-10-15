/* eslint-disable @typescript-eslint/no-explicit-any */
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { PageHeader, Spinner } from '../components/ui'
import { useCase } from '../context'
import { casePath } from '../lib/paths'
import { useApi } from '../hooks'

const ACTION_LINK: Record<string, string> = { capture: 'propagation', add_urls: 'propagation', complaint: 'complaint', monitor: 'monitor', detectors: '/settings', upload_reference: 'overview' }

export default function Gaps() {
  const { caseId, tick } = useCase()
  const { data } = useApi<any[]>(`/api/cases/${caseId}/gaps`, [tick])
  const [all, setAll] = useState(false)
  const shown = all ? data ?? [] : (data ?? []).slice(0, 3)
  return (
    <div className="max-w-4xl">
      <PageHeader step="Prove" title="Evidence gaps"
        question="What can this case still not prove — and which single piece of evidence would move it furthest?" />
      {!data ? <Spinner /> : (
        <div className="space-y-3">
          {shown.map((g, i) => (
            <div key={g.id} className="card flex gap-5 p-5" style={{ borderLeft: '3px solid var(--color-st-unknown)' }}>
              <div className="flex w-14 shrink-0 flex-col items-center">
                <div className="mono text-2xl font-semibold">{i + 1}</div>
                <div className="mt-1 h-1.5 w-12 overflow-hidden rounded bg-raised"><div className="h-full bg-ink-2" style={{ width: `${Math.min(100, (g.score / (data[0]?.score || 1)) * 100)}%` }} /></div>
                <div className="mt-1 text-[10px] text-ink-3">priority</div>
              </div>
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{g.question}</div>
                <p className="mt-1 text-[13px] text-ink-3">{g.why}</p>
                <div className="mt-3 rounded-lg bg-raised/60 p-3 text-[13px]"><span className="label mr-2">Next evidence</span><span className="text-ink">{g.next_evidence}</span></div>
                <div className="mt-2 flex flex-wrap items-center gap-4 text-[12px] text-ink-3">
                  <span>Who can obtain it: <b className="text-ink-2">{g.who}</b></span><span>Effort: <b className="text-ink-2">{g.effort}</b></span><span>Value: <b className="text-ink-2">{g.value}/5</b></span>
                  {g.action && ACTION_LINK[g.action] && <Link className="btn ml-auto px-2.5 py-1 text-[12px]" to={ACTION_LINK[g.action].startsWith('/') ? ACTION_LINK[g.action] : casePath(caseId, ACTION_LINK[g.action])}>Do this →</Link>}
                </div>
              </div>
            </div>
          ))}
          {data.length > 3 && (
            <button className="btn w-full justify-center" onClick={() => setAll(!all)}>{all ? <><ChevronUp size={14} /> Show top 3 only</> : <><ChevronDown size={14} /> Show {data.length - 3} more gaps</>}</button>
          )}
        </div>
      )}
    </div>
  )
}

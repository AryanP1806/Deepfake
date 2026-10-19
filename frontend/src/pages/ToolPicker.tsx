import { ArrowRight } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { CaseBadge } from '../components/dashboard/CaseRow'
import { SensitiveImg, Spinner } from '../components/ui'
import { type CaseSummary } from '../dashboard'
import { useApi } from '../hooks'
import { casePath } from '../lib/paths'
import { TOOLS } from '../lib/tools'

/** A tool analyses the media of a case, so choose which case to open it for (cases with matching media come first). */
export default function ToolPicker() {
  const { tool = '' } = useParams()
  const t = TOOLS[tool]
  const { data, error } = useApi<CaseSummary[]>('/api/cases-summary')
  if (!t) return <div className="mx-auto max-w-2xl p-6"><h1 className="display text-2xl font-bold">Unknown tool</h1><Link to="/" className="mt-3 inline-block text-accent-text underline">Back to Home</Link></div>
  const rows = [...(data ?? [])].sort((a, b) => Number(b.modality === t.modality) - Number(a.modality === t.modality))
  return (
    <div className="mx-auto max-w-[900px] space-y-5">
      <header className="flex items-start gap-4 px-1">
        <span className="icon-chip shrink-0" style={{ '--chip': t.hue } as React.CSSProperties}><t.icon size={20} aria-hidden /></span>
        <div><h1 className="display text-[28px] font-extrabold leading-tight">{t.title}</h1><p className="mt-1 max-w-2xl text-[14px] text-ink-2">{t.about}</p></div>
      </header>
      <section className="card p-4" aria-labelledby="pick-h">
        <h2 id="pick-h" className="display px-3 pb-2 pt-1 text-[17px] font-bold">Choose a case to open it for</h2>
        {!data && !error && <div className="p-4"><Spinner label="Loading cases" /></div>}
        {error && <p role="alert" className="p-4 text-[13px] text-crit">{error}</p>}
        {data && rows.length === 0 && (
          <div className="well m-2 p-5 text-center text-[13.5px] text-ink-2">This tool works on the media of a case, and you have none yet.
            <div className="mt-3"><Link to="/" className="btn btn-primary">Start an investigation</Link></div></div>
        )}
        <ul className="row-sep">
          {rows.map((c) => (
            <li key={c.id}>
              <Link to={casePath(c.id, t.route)} className="nav-item !gap-4 !rounded-none !px-3 !py-3">
                {c.thumb ? <SensitiveImg caseId={c.id} sha={c.thumb} blur={c.sensitive} className="h-12 w-16 shrink-0 rounded-lg bg-black/80" /> : <span className="h-12 w-16 shrink-0 rounded-lg bg-raised" />}
                <span className="min-w-0 flex-1"><span className="block truncate text-[14px] font-semibold text-ink">{c.title}</span>
                  <span className="block text-[12.5px] text-ink-3">{c.modality ?? 'no media'} · {c.media_count} media{t.modality && c.modality === t.modality ? ' · matches this tool' : ''}</span></span>
                <CaseBadge c={c} /><ArrowRight size={16} className="text-ink-3" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}

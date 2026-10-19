import { ArrowRight } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDashboard, type CaseSummary } from '../../dashboard'
import { DeleteCaseDialog } from '../DeleteCaseDialog'
import { Spinner } from '../ui'
import { CaseRow } from './CaseRow'

export function RecentCases() {
  const { data, error, reload } = useDashboard()
  const [del, setDel] = useState<CaseSummary | null>(null)
  return (
    <section className="card p-4" aria-labelledby="recent-h">
      <div className="mb-1 flex items-center justify-between px-3 pt-2">
        <h2 id="recent-h" className="display text-[19px] font-bold">Recent Cases</h2>
        <Link to="/cases" className="inline-flex items-center gap-1 text-[13px] font-semibold text-accent-text hover:underline">View all cases <ArrowRight size={14} aria-hidden /></Link>
      </div>
      {!data && !error && <div className="p-4"><Spinner label="Loading cases" /></div>}
      {error && !data && <p role="alert" className="p-4 text-[13px] text-crit">Could not load cases: {error}</p>}
      {data && data.recent.length === 0 && (
        <p className="well m-3 p-5 text-center text-[13.5px] text-ink-2">No cases yet. Paste a link above to start your first investigation.</p>
      )}
      {data && data.recent.length > 0 && (
        <ul className="row-sep" aria-label="Most recent cases">
          {data.recent.map((c) => <CaseRow key={c.id} c={c} onDelete={setDel} onChanged={reload} />)}
        </ul>
      )}
      {del && <DeleteCaseDialog caseId={del.id} title={del.title} onClose={() => setDel(null)} onDone={() => { setDel(null); reload() }} />}
    </section>
  )
}

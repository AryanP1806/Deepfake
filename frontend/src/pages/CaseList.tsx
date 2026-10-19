import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { type CaseSummary } from '../dashboard'
import { CaseRow } from '../components/dashboard/CaseRow'
import { DeleteCaseDialog } from '../components/DeleteCaseDialog'
import { Spinner } from '../components/ui'
import { useApi } from '../hooks'

type Filter = 'all' | 'fake' | 'real' | 'unknown' | 'working'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' }, { id: 'fake', label: 'Fake' }, { id: 'real', label: 'Real' }, { id: 'unknown', label: 'Unknown / uncertain' }, { id: 'working', label: 'Investigating' },
]
const group = (c: CaseSummary): Filter => {
  if (c.investigating) return 'working'
  const l = c.verdict?.label
  return l === 'fake' || l === 'likely_fake' ? 'fake' : l === 'real' || l === 'likely_real' ? 'real' : 'unknown'
}

/** Every case, newest first, with a quick filter. Opening one goes to its workspace; creating one lives on Home / Investigation. */
export default function CaseList() {
  const [archived, setArchived] = useState(false)
  const { data, loading, error, reload } = useApi<CaseSummary[]>(`/api/cases-summary?archived=${archived}`)
  const [q, setQ] = useState('')
  const [f, setF] = useState<Filter>('all')
  const [del, setDel] = useState<CaseSummary | null>(null)
  const rows = useMemo(() => (data ?? []).filter((c) => (f === 'all' || group(c) === f) && (!q.trim() || `${c.title} ${c.id}`.toLowerCase().includes(q.trim().toLowerCase()))), [data, f, q])
  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3 px-1">
        <div>
          <h1 className="display text-[28px] font-extrabold leading-tight">{archived ? 'Archived cases' : 'Cases'}</h1>
          <p className="mt-1 text-[14px] text-ink-2">{archived ? 'Archived cases are kept on disk and can be restored or deleted.' : 'Every investigation you have started, newest first.'}</p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => setArchived(!archived)}>{archived ? 'Back to active cases' : 'Show archived'}</button>
          {!archived && <Link to="/investigation" className="btn btn-primary">New investigation</Link>}
        </div>
      </div>
      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-3 px-2 pb-3">
          <label className="sr-only" htmlFor="case-filter">Filter cases by title or id</label>
          <input id="case-filter" className="input !w-64" placeholder="Filter by title or id" value={q} onChange={(e) => setQ(e.target.value)} />
          <div role="radiogroup" aria-label="Verdict filter" className="seg flex-wrap">
            {FILTERS.map((x) => <button key={x.id} role="radio" aria-checked={f === x.id} onClick={() => setF(x.id)}>{x.label}</button>)}
          </div>
        </div>
        {loading && !data && <div className="p-4"><Spinner label="Loading cases" /></div>}
        {error && <p role="alert" className="p-4 text-[13px] text-crit">Could not load cases: {error}</p>}
        {data && rows.length === 0 && <p className="well m-2 p-5 text-center text-[13.5px] text-ink-2">{data.length ? 'No case matches this filter.' : archived ? 'Nothing archived.' : 'No cases yet. Start one from Home or Investigation.'}</p>}
        {rows.length > 0 && <ul className="row-sep">{rows.map((c) => <CaseRow key={c.id} c={c} onDelete={setDel} onChanged={reload} />)}</ul>}
      </div>
      {del && <DeleteCaseDialog caseId={del.id} title={del.title} onClose={() => setDel(null)} onDone={() => { setDel(null); reload() }} />}
    </div>
  )
}

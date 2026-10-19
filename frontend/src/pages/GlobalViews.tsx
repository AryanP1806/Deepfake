/* eslint-disable @typescript-eslint/no-explicit-any */
import { Link } from 'react-router-dom'
import { fmtBytes, fmtTime } from '../api'
import { Spinner } from '../components/ui'
import { useApi } from '../hooks'
import { casePath } from '../lib/paths'

function Head({ title, sub }: { title: string; sub: string }) {
  return <header className="px-1"><h1 className="display text-[28px] font-extrabold leading-tight">{title}</h1><p className="mt-1 max-w-2xl text-[14px] text-ink-2">{sub}</p></header>
}
function Empty({ children }: { children: React.ReactNode }) { return <p className="well m-2 p-5 text-center text-[13.5px] text-ink-2">{children}</p> }

export function VaultPage() {
  const { data, error } = useApi<any>('/api/vault-overview')
  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <Head title="Evidence Vault" sub="Everything preserved across your cases: originals and captures are hashed and stored read-only, and every action is chained in a tamper-evident custody log." />
      <section className="card p-4" aria-label="Preserved evidence by case">
        {!data && !error && <div className="p-4"><Spinner label="Reading the vault" /></div>}
        {error && <p role="alert" className="p-4 text-[13px] text-crit">{error}</p>}
        {data && (
          <>
            <dl className="grid grid-cols-3 gap-3 px-2 pb-4 text-center">
              {[['Preserved items', data.totals.items], ['Total size', fmtBytes(data.totals.bytes)], ['Custody entries', data.totals.custody_entries]].map(([k, v]) => (
                <div key={String(k)} className="well p-3"><dd className="text-[22px] font-bold text-ink">{v}</dd><dt className="text-[12px] text-ink-3">{k}</dt></div>
              ))}
            </dl>
            {data.cases.length === 0 ? <Empty>The vault is empty. Evidence is preserved automatically when you add media or capture a page.</Empty> : (
              <table className="table-quiet w-full text-[13px]"><caption className="sr-only">Evidence per case</caption>
                <thead><tr><th scope="col">Case</th><th scope="col">Items</th><th scope="col">Size</th><th scope="col">Custody log</th><th scope="col">Packages</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
                <tbody>{data.cases.map((r: any) => (
                  <tr key={r.case_id}><td className="max-w-xs truncate font-medium text-ink">{r.title}{r.status === 'archived' ? <span className="tag ml-2">archived</span> : null}</td><td>{r.items}</td><td>{fmtBytes(r.bytes)}</td>
                    <td>{r.custody_entries}{r.last_custody ? <span className="block text-[11.5px] text-ink-3">last {fmtTime(r.last_custody, false)}</span> : null}</td><td>{r.exports}</td>
                    <td className="text-right"><Link className="font-semibold text-accent-text hover:underline" to={casePath(r.case_id, 'evidence')}>Open</Link></td></tr>
                ))}</tbody></table>
            )}
          </>
        )}
      </section>
    </div>
  )
}

export function MonitoringPage() {
  const { data, error } = useApi<any>('/api/monitoring-overview')
  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <Head title="Monitoring" sub="Watch captured pages for removal, change or re-upload. Turn it on per case; alerts from every case collect here." />
      <section className="card p-4" aria-label="Monitoring by case">
        {!data && !error && <div className="p-4"><Spinner label="Loading monitoring" /></div>}
        {error && <p role="alert" className="p-4 text-[13px] text-crit">{error}</p>}
        {data && (data.cases.length === 0 ? <Empty>No cases to monitor yet.</Empty> : (
          <table className="table-quiet w-full text-[13px]"><caption className="sr-only">Monitoring status per case</caption>
            <thead><tr><th scope="col">Case</th><th scope="col">Monitoring</th><th scope="col">Places</th><th scope="col">Last check</th><th scope="col">Unseen alerts</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{data.cases.map((r: any) => (
              <tr key={r.case_id}><td className="max-w-xs truncate font-medium text-ink">{r.title}</td>
                <td>{r.enabled ? `On · every ${r.interval_minutes} min` : 'Off'}</td><td>{r.places}</td><td>{r.last_run_at ? fmtTime(r.last_run_at, false) : '–'}</td>
                <td>{r.unseen > 0 ? <span className="badge" style={{ '--b': 'var(--color-v-fake)' } as React.CSSProperties}>{r.unseen}</span> : 0}</td>
                <td className="text-right"><Link className="font-semibold text-accent-text hover:underline" to={casePath(r.case_id, 'monitor')}>Manage</Link></td></tr>
            ))}</tbody></table>
        ))}
      </section>
      {data && data.alerts.length > 0 && (
        <section className="card p-4" aria-labelledby="al-h"><h2 id="al-h" className="display px-2 pb-2 text-[17px] font-bold">Recent alerts</h2>
          <ul className="row-sep">{data.alerts.map((a: any) => (
            <li key={a.id} className="flex items-start justify-between gap-4 px-2 py-2.5 text-[13px]"><span><span className={a.seen ? 'text-ink-2' : 'font-semibold text-ink'}>{a.message}</span><span className="block text-[12px] text-ink-3">{a.case_title} · {a.kind.replace('_', ' ')}</span></span>
              <span className="shrink-0 text-[12px] text-ink-3">{fmtTime(a.created_at, false)}</span></li>
          ))}</ul></section>
      )}
    </div>
  )
}

export function ReportsPage() {
  const { data, error } = useApi<any>('/api/reports-overview')
  return (
    <div className="mx-auto max-w-[1100px] space-y-5">
      <Head title="Reports" sub="Complaint drafts, takedown tracking and evidence packages for each case. Nothing is filed for you: you submit, and record the reference number you receive." />
      <section className="card p-4" aria-label="Reports by case">
        {!data && !error && <div className="p-4"><Spinner label="Loading reports" /></div>}
        {error && <p role="alert" className="p-4 text-[13px] text-crit">{error}</p>}
        {data && (data.cases.length === 0 ? <Empty>No cases yet.</Empty> : (
          <table className="table-quiet w-full text-[13px]"><caption className="sr-only">Report status per case</caption>
            <thead><tr><th scope="col">Case</th><th scope="col">Complaint</th><th scope="col">Takedown reports</th><th scope="col">Evidence packages</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead>
            <tbody>{data.cases.map((r: any) => (
              <tr key={r.case_id}><td className="max-w-xs truncate font-medium text-ink">{r.title}</td>
                <td className="capitalize">{r.complaint_state}{r.ack_number ? <span className="mono block text-[11.5px] text-ink-3">ref {r.ack_number}</span> : null}</td><td>{r.takedown_reports}</td>
                <td>{r.exports.length ? r.exports.map((e: any) => <a key={e.name} className="block font-semibold text-accent-text hover:underline" href={`/api/cases/${r.case_id}/exports/${e.name}`}>{e.name} <span className="font-normal text-ink-3">({fmtBytes(e.size)})</span></a>) : <span className="text-ink-3">none yet</span>}</td>
                <td className="whitespace-nowrap text-right"><Link className="font-semibold text-accent-text hover:underline" to={casePath(r.case_id, 'complaint')}>Complaint</Link> · <Link className="font-semibold text-accent-text hover:underline" to={casePath(r.case_id, 'takedown')}>Takedown</Link></td></tr>
            ))}</tbody></table>
        ))}
      </section>
    </div>
  )
}

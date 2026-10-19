/* eslint-disable @typescript-eslint/no-explicit-any */
import { Archive, Trash2, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'

/** Permanent deletion needs a typed confirmation. Archiving (hide, keep evidence) is offered first. */
export function DeleteCaseDialog({ caseId, title, onClose, onDone }: { caseId: string; title: string; onClose: () => void; onDone: (what: 'deleted' | 'archived') => void }) {
  const [pre, setPre] = useState<any>(null)
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { api.get(`/api/cases/${caseId}/deletion-preview`).then(setPre).catch((e) => setErr(String(e.message))) }, [caseId])

  const archive = async () => {
    setBusy(true)
    try { await api.patch(`/api/cases/${caseId}`, { status: 'archived' }); onDone('archived') } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true)
    setErr('')
    try { await api.delete(`/api/cases/${caseId}?confirm=DELETE`); onDone('deleted') } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div className="card w-full max-w-lg p-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 text-lg font-semibold"><Trash2 size={18} /> Remove this case?</div>
        <p className="mt-1 truncate text-[13px] text-ink-3">{title}</p>

        <div className="mt-4 rounded-lg border border-line bg-bg/50 p-3">
          <div className="flex items-start gap-3">
            <Archive size={18} className="mt-0.5 shrink-0 text-ink-2" />
            <div className="flex-1">
              <div className="font-semibold">Archive (recommended)</div>
              <p className="text-[12.5px] text-ink-3">Hides the case from your list but keeps all evidence and the custody log, so you can restore it later or hand it to the police.</p>
            </div>
            <button className="btn" disabled={busy} onClick={archive}>Archive</button>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-crit/60 bg-crit/5 p-3">
          <div className="flex items-center gap-2 font-semibold text-crit"><TriangleAlert size={16} /> Delete permanently</div>
          {pre ? (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12.5px] text-ink-2">
              <li>{pre.media} media file(s) and {pre.locations} captured page(s) — {pre.megabytes} MB of evidence</li>
              <li>The write-once evidence vault and the chain-of-custody log</li>
              <li>{pre.claims} findings and {pre.reports} follow-up record(s)</li>
              {pre.exports.length > 0 ? <li>{pre.exports.length} exported evidence package(s) stored in this case</li> : <li className="text-warn">No evidence package has been exported from this case.</li>}
            </ul>
          ) : <div className="mt-2 text-[12.5px] text-ink-3">Checking what would be removed…</div>}
          <p className="mt-2 text-[12px] text-ink-3">This cannot be undone. If a police complaint depends on this evidence, export the package first. A short note that the case was deleted (title, time, counts) is kept.</p>
          {pre?.active_jobs > 0 && <p className="mt-2 text-[12px] text-warn">Something is still running for this case. Wait for it to finish before deleting.</p>}
          <label className="label mt-3 block">Type DELETE to confirm</label>
          <input className="input mt-1" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="DELETE" />
          <button className="btn mt-3 w-full justify-center border-crit text-crit" disabled={typed !== 'DELETE' || busy || (pre?.active_jobs ?? 0) > 0} onClick={remove}>
            <Trash2 size={14} /> {busy ? 'Deleting…' : 'Delete this case forever'}
          </button>
        </div>
        {err && <div className="mt-3 text-sm text-crit">{err}</div>}
        <button className="btn mt-4 w-full justify-center" onClick={onClose}>Cancel</button>
      </div>
    </div>
  )
}

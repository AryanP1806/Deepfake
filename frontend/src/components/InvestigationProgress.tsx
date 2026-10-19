/* eslint-disable @typescript-eslint/no-explicit-any */
import { ArrowRight, CheckCircle2, ChevronDown, CircleDashed, Loader2, MinusCircle, X, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { casePath } from '../lib/paths'

interface Stage { key: string; title: string; status: 'pending' | 'running' | 'done' | 'skipped' | 'error'; detail: string }
interface Source { name: string; status: 'ran' | 'skipped' | 'failed' | 'unavailable'; detail: string; found: number; round: number }
interface State {
  running: boolean; started_at: string; finished_at: string | null; error: string | null; stages: Stage[]; sources: Source[]
  summary: { locations: number; verified_copies: number; seconds: number; by_relation: Record<string, number> } | null
}

/** Shows the link → evidence pipeline: what is happening now, what each step found, what could NOT be searched. */
export function InvestigationProgress({ caseId, onChange, kick = 0 }: { caseId: string; onChange: () => void; kick?: number }) {
  const [st, setSt] = useState<State | null>(null)
  const [open, setOpen] = useState(false)
  const [hidden, setHidden] = useState(false)
  const sig = useRef('')
  const cb = useRef(onChange)
  cb.current = onChange

  useEffect(() => {
    let stop = false
    let timer: number
    const poll = async () => {
      try {
        const d = await api.get(`/api/cases/${caseId}/investigation`)
        if (stop) return
        const s: State | null = d.state
        setSt(s)
        const signature = s ? s.stages.map((x) => x.status).join() + String(s.running) : ''
        if (signature !== sig.current) {
          if (sig.current !== '') cb.current()  // refresh case data when a stage completes (media appear progressively)
          sig.current = signature
        }
        timer = window.setTimeout(poll, s?.running ? 1500 : 6000)
      } catch {
        timer = window.setTimeout(poll, 5000)
      }
    }
    poll()
    return () => { stop = true; clearTimeout(timer) }
  }, [caseId, kick])

  useEffect(() => { setHidden(localStorage.getItem(`inv-hidden-${caseId}`) === (st?.finished_at ?? 'x')) }, [caseId, st?.finished_at])

  if (!st || (!st.running && hidden)) return null
  const done = st.stages.filter((s) => s.status === 'done' || s.status === 'skipped').length
  const total = st.stages.length
  const current = st.stages.find((s) => s.status === 'running')
  const failed = st.stages.some((s) => s.status === 'error') || !!st.error
  const pct = st.running ? Math.max(4, Math.round((done / total) * 100)) : 100

  return (
    <div className={`card mb-5 overflow-hidden ${st.running ? 'border-line-strong' : failed ? 'border-warn/60' : 'border-good/60'}`}>
      <div className="flex items-start gap-4 p-4">
        {st.running ? <Loader2 size={22} className="mt-0.5 shrink-0 animate-spin" /> : failed ? <XCircle size={22} className="mt-0.5 shrink-0 text-warn" /> : <CheckCircle2 size={22} className="mt-0.5 shrink-0 text-good" />}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <span className="text-[15px] font-semibold">
              {st.running ? 'Investigating the link…' : failed ? 'Investigation finished with problems' : 'Investigation finished'}
            </span>
            {st.running && current && <span className="text-[13px] text-ink-2">{current.title}</span>}
            {!st.running && st.summary && (
              <span className="text-[13px] text-ink-2">
                {st.summary.verified_copies} verified cop{st.summary.verified_copies === 1 ? 'y' : 'ies'} found in {st.summary.locations} place{st.summary.locations === 1 ? '' : 's'} · took {st.summary.seconds}s
              </span>
            )}
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded bg-raised"><div className="h-full rounded bg-ink transition-all duration-700" style={{ width: `${pct}%` }} /></div>
          {st.running && current?.detail && <div className="mt-1.5 truncate text-[12px] text-ink-3">{current.detail}</div>}
          {st.error && <div className="mt-1.5 text-[12px] text-warn">{st.error}</div>}
        </div>
        {!st.running && (
          <div className="flex shrink-0 items-center gap-2">
            <Link to={casePath(caseId, 'spread')} className="btn btn-primary">See where it appeared <ArrowRight size={14} /></Link>
            <button className="btn px-2" title="Dismiss" onClick={() => { localStorage.setItem(`inv-hidden-${caseId}`, st.finished_at ?? 'x'); setHidden(true) }}><X size={14} /></button>
          </div>
        )}
      </div>

      <div className="grid gap-x-4 gap-y-1.5 border-t border-line bg-bg/40 px-4 py-3 text-[12.5px] md:grid-cols-2">
        {st.stages.filter((s) => s.key !== 'done').map((s) => (
          <div key={s.key} className="flex items-start gap-2">
            <span className="mt-0.5">
              {s.status === 'done' && <CheckCircle2 size={14} className="text-good" />}
              {s.status === 'running' && <Loader2 size={14} className="animate-spin" />}
              {s.status === 'pending' && <CircleDashed size={14} className="text-ink-3" />}
              {s.status === 'skipped' && <MinusCircle size={14} className="text-ink-3" />}
              {s.status === 'error' && <XCircle size={14} className="text-warn" />}
            </span>
            <span className="min-w-0">
              <span className={s.status === 'running' ? 'font-semibold text-ink' : s.status === 'pending' ? 'text-ink-3' : 'text-ink-2'}>{s.title}</span>
              {s.detail && s.status !== 'pending' && <span className="block truncate text-[11.5px] text-ink-3">{s.detail}</span>}
            </span>
          </div>
        ))}
      </div>

      {st.sources.length > 0 && (
        <div className="border-t border-line px-4 py-2.5">
          <button className="flex items-center gap-1 text-[12px] text-ink-3 hover:text-ink" onClick={() => setOpen(!open)}>
            <ChevronDown size={13} className={open ? '' : '-rotate-90'} /> What was searched, and what was not ({st.sources.length})
          </button>
          {open && (
            <ul className="mt-2 space-y-1 text-[12px]">
              {st.sources.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className={`w-20 shrink-0 font-medium ${s.status === 'ran' ? 'text-good' : s.status === 'failed' ? 'text-warn' : 'text-ink-3'}`}>{s.status === 'ran' ? 'searched' : s.status === 'failed' ? 'failed' : 'not used'}</span>
                  <span className="text-ink-2">{s.name}<span className="text-ink-3"> — {s.detail}</span></span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

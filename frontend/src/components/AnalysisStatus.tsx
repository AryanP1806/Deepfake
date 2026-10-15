/* eslint-disable @typescript-eslint/no-explicit-any */
import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

const KIND_LABEL: Record<string, string> = {
  analyze: 'Analysing', genealogy: 'Comparing copies', capture: 'Capturing web pages', export: 'Building evidence package',
  monitor: 'Monitoring check', warmup: 'Loading AI models',
}

const fmtS = (s?: number | null) => (s == null ? '' : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`)

/** Big, always-visible "what is the system doing right now" panel for a case.
 *  Polls the server (so it also works if the live event stream was missed) and calls
 *  onChange whenever a step finishes, so results appear progressively. */
export function AnalysisStatus({ caseId, onChange, kick = 0 }: { caseId: string; onChange: () => void; kick?: number }) {
  const [active, setActive] = useState<any[]>([])
  const [tickNow, setTickNow] = useState(Date.now())
  const lastSig = useRef('')
  const fetchedAt = useRef(Date.now())
  const cb = useRef(onChange)
  cb.current = onChange

  useEffect(() => {
    let stop = false
    let timer: number
    const poll = async () => {
      try {
        const d = await api.get(`/api/cases/${caseId}/analysis-status`)
        if (stop) return
        fetchedAt.current = Date.now()
        setActive(d.active)
        const sig = JSON.stringify(d.active.map((a: any) => [a.job_id, a.steps?.filter((s: any) => s.status !== 'pending' && s.status !== 'running').length]))
        if (sig !== lastSig.current) {
          if (lastSig.current !== '') cb.current()
          lastSig.current = sig
        }
        timer = window.setTimeout(poll, d.active.length ? 1000 : 2500)
      } catch {
        timer = window.setTimeout(poll, 4000)
      }
    }
    poll()
    return () => { stop = true; clearTimeout(timer) }
  }, [caseId, kick])

  useEffect(() => {
    if (!active.length) return
    const t = setInterval(() => setTickNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [active.length])

  if (!active.length) return null
  const drift = (tickNow - fetchedAt.current) / 1000
  return (
    <div className="mb-5 space-y-3">
      {active.map((j) => {
        const steps: any[] = j.steps ?? []
        const done = steps.filter((s) => s.status !== 'pending' && s.status !== 'running').length
        const running = steps.find((s) => s.status === 'running')
        const pct = steps.length ? Math.round((done / steps.length) * 100) : Math.round((j.progress ?? 0) * 100)
        const coldModel = running && running.typical_s && j.elapsed_s > 0 && ['ufd_clip', 'spai', 'trufor', 'sbi', 'asr', 'faces'].includes(running.name)
        return (
          <div key={j.job_id} className="card overflow-hidden border-line-strong">
            <div className="flex items-center gap-4 p-4">
              <Loader2 size={22} className="shrink-0 animate-spin text-ink" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <span className="text-[15px] font-semibold">{KIND_LABEL[j.kind] ?? j.kind}{j.media ? ` “${j.media.label}”` : ''}…</span>
                  {steps.length > 0 && <span className="text-[13px] text-ink-2">step {Math.min(done + 1, steps.length)} of {steps.length}{running ? `: ${running.title}` : j.message ? `: ${j.message}` : ''}</span>}
                  {!steps.length && j.message && <span className="text-[13px] text-ink-2">{j.message}</span>}
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded bg-raised">
                  <div className="h-full rounded bg-ink transition-all duration-700" style={{ width: `${Math.max(3, pct)}%` }} />
                </div>
                <div className="mt-1.5 flex justify-between text-[12px] text-ink-3">
                  <span>{j.status === 'queued' ? 'Waiting for a free worker…' : `Running for ${fmtS(j.elapsed_s + drift)}`}{j.eta_s ? ` · about ${fmtS(Math.max(1, j.eta_s - drift))} left` : ''}</span>
                  <span>{pct}%</span>
                </div>
                {coldModel && running.typical_s && (j.elapsed_s + drift) > running.typical_s * 4 + 10 && (
                  <div className="mt-1 text-[12px] text-warn">Loading the AI model onto the GPU — this happens once after a restart and can take about a minute.</div>
                )}
              </div>
            </div>
            {steps.length > 0 && (
              <div className="grid gap-x-4 gap-y-1 border-t border-line bg-bg/40 px-4 py-3 text-[12px] sm:grid-cols-2 xl:grid-cols-4">
                {steps.map((s) => (
                  <div key={s.name} className="flex items-center gap-1.5">
                    {s.status === 'done' && <CheckCircle2 size={13} className="text-good" />}
                    {s.status === 'running' && <Loader2 size={13} className="animate-spin text-ink" />}
                    {s.status === 'pending' && <CircleDashed size={13} className="text-ink-3" />}
                    {s.status === 'not_checked' && <CircleDashed size={13} className="text-ink-3 opacity-50" />}
                    {s.status === 'error' && <XCircle size={13} className="text-crit" />}
                    <span className={`truncate ${s.status === 'running' ? 'font-semibold text-ink' : s.status === 'not_checked' ? 'text-ink-3 line-through' : 'text-ink-2'}`}>{s.title}</span>
                    {s.duration_s != null && <span className="mono ml-auto shrink-0 text-[10.5px] text-ink-3">{s.duration_s.toFixed(1)}s</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

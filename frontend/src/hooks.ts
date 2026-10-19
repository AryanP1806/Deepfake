import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

export function useApi<T>(url: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const seq = useRef(0)
  const reload = useCallback(async () => {
    if (!url) return
    const my = ++seq.current
    setLoading(true)
    try {
      const d = await api.get<T>(url)
      if (my === seq.current) { setData(d); setError(null) }
    } catch (e) {
      if (my === seq.current) setError(String((e as Error).message))
    } finally {
      if (my === seq.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, ...deps])
  useEffect(() => { reload() }, [reload])
  return { data, error, loading, reload, setData }
}

export interface JobEvent { job_id: string; kind?: string; status?: string; progress?: number; message?: string; error?: string }

/** Subscribe to a case's server-sent job events. Calls onDone when any job finishes. */
export function useCaseEvents(caseId: string | undefined, onDone: () => void) {
  const [jobs, setJobs] = useState<Record<string, JobEvent>>({})
  const cb = useRef(onDone)
  cb.current = onDone
  useEffect(() => {
    if (!caseId) return
    const es = new EventSource(`/api/cases/${caseId}/events`)
    es.addEventListener('job', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data) as JobEvent
      setJobs((j) => ({ ...j, [d.job_id]: { ...j[d.job_id], ...d } }))
      if (d.status === 'done' || d.status === 'error') {
        cb.current()
        setTimeout(() => setJobs((j) => { const n = { ...j }; delete n[d.job_id]; return n }), d.status === 'error' ? 8000 : 2500)
      }
    })
    return () => es.close()
  }, [caseId])
  return jobs
}

/** Call ``fn`` every ``ms`` milliseconds while the tab is visible (and once when it becomes visible again). */
export function usePolling(fn: () => void, ms: number) {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => {
    const tick = () => { if (document.visibilityState === 'visible') ref.current() }
    const t = setInterval(tick, ms)
    document.addEventListener('visibilitychange', tick)
    return () => { clearInterval(t); document.removeEventListener('visibilitychange', tick) }
  }, [ms])
}

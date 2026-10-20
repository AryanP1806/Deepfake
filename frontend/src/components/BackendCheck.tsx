import { Check, Copy, FlaskConical, TriangleAlert, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { getFeatures, useMode } from '../lib/demo'
import { LINKS } from '../lib/links'

const CMD = 'cd backend && uv run --extra capture --extra provenance --extra ocr --extra ml --extra asr --extra detectors uvicorn dfir.main:app --port 8000'
const NEEDS = ['investigate', 'spread', 'takedown', 'case_delete', 'lead_engine', 'generator', 'source']
type Kind = 'demo' | 'down' | 'old'
const SHOW_MS: Record<Kind, number> = { demo: 7000, down: 8000, old: 12000 }

/** A small pop-up (not a banner) that says which situation the page is in: showing the saved demo, backend unreachable, or
 *  backend older than this page. It closes itself after a few seconds and has a close button; the "Demo mode" chip in the
 *  top bar brings it back. */
export function BackendCheck() {
  const mode = useMode()
  const [live, setLive] = useState<'ok' | 'old' | 'down'>('ok')
  const [closed, setClosed] = useState<Kind | null>(null)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (mode !== 'live') return
    let stop = false
    const check = async () => {
      try {
        const h = await (await fetch('/api/health')).json()
        if (!stop) setLive(NEEDS.every((f) => (h.features ?? getFeatures()).includes(f)) ? 'ok' : 'old')
      } catch { if (!stop) setLive('down') }
    }
    check()
    const t = setInterval(check, 15000)
    return () => { stop = true; clearInterval(t) }
  }, [mode])

  const kind: Kind | null = mode === 'demo' ? 'demo' : mode === 'down' ? 'down' : mode === 'live' && live !== 'ok' ? live : null
  useEffect(() => {
    const again = () => setClosed(null)
    window.addEventListener('dfir-show-status', again)
    return () => window.removeEventListener('dfir-show-status', again)
  }, [])
  useEffect(() => {
    if (!kind || closed === kind) return
    const t = setTimeout(() => setClosed(kind), SHOW_MS[kind])
    return () => clearTimeout(t)
  }, [kind, closed])

  if (!kind || closed === kind) return null
  return (
    <div role={kind === 'demo' ? 'status' : 'alert'} className="pop fixed bottom-4 right-4 z-[70] w-[min(26rem,calc(100vw-2rem))] p-4 text-[13px]">
      <button className="btn btn-quiet absolute right-2 top-2 !min-h-0 !p-1.5" onClick={() => setClosed(kind)} aria-label="Dismiss this message"><X size={15} aria-hidden /></button>
      <div className="flex items-start gap-3 pr-6">
        {kind === 'demo' ? <FlaskConical size={20} className="mt-0.5 shrink-0 text-accent-text" aria-hidden /> : <TriangleAlert size={20} className="mt-0.5 shrink-0 text-crit" aria-hidden />}
        <div className="min-w-0 flex-1">
          {kind === 'demo' && (
            <>
              <div className="font-semibold text-ink">Demo mode: saved example cases</div>
              <p className="mt-1 text-ink-2">The analysis backend isn’t connected, so you’re looking at completed examples. New analysis is switched off.</p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] font-semibold">
                <a className="text-accent-text hover:underline" href={LINKS.readme} target="_blank" rel="noreferrer">Run it yourself (Docker)</a>
                <a className="text-accent-text hover:underline" href={LINKS.drive} target="_blank" rel="noreferrer">Project description</a>
              </div>
            </>
          )}
          {kind === 'down' && <div className="font-semibold text-ink">Can’t reach the backend. Start it, then reload this page.</div>}
          {kind === 'old' && <div className="font-semibold text-ink">The backend is running an older version than this page, so some screens won’t work. Restart it, then reload.</div>}
          {kind !== 'demo' && (
            <div className="mt-2">
              <div className="flex items-center gap-2">
                <code className="mono block min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded border border-line-strong bg-bg px-2 py-1.5 text-[11.5px]">{CMD}</code>
                <button className="btn shrink-0 !min-h-0 !px-2 !py-1.5 text-[12px]" onClick={() => { navigator.clipboard.writeText(CMD); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>
                  {copied ? <Check size={13} aria-hidden /> : <Copy size={13} aria-hidden />} {copied ? 'Copied' : 'Copy'}
                </button>
              </div>
              <div className="mt-1 text-[11.5px] text-ink-3">Using Docker instead? Run <span className="mono">docker compose up --build</span>.</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

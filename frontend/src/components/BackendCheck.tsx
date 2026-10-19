import { Check, Copy, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

const CMD = 'cd backend && uv run --extra capture --extra provenance --extra ocr --extra ml --extra asr --extra detectors uvicorn dfir.main:app --port 8000'
const NEEDS = ['investigate', 'spread', 'takedown', 'case_delete', 'lead_engine', 'generator', 'source']

/** The page and the backend are separate programs. If the backend was started before the last update it keeps running
 *  the old code, and new screens would load forever. Say so plainly, with the fix. */
export function BackendCheck() {
  const [state, setState] = useState<'ok' | 'old' | 'down'>('ok')
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    let stop = false
    const check = async () => {
      try {
        const h = await (await fetch('/api/health')).json()
        if (!stop) setState(NEEDS.every((f) => (h.features ?? []).includes(f)) ? 'ok' : 'old')
      } catch {
        if (!stop) setState('down')
      }
    }
    check()
    const t = setInterval(check, 15000)
    return () => { stop = true; clearInterval(t) }
  }, [])
  if (state === 'ok') return null
  return (
    <div className="sticky top-0 z-[60] border-b border-crit bg-surface px-6 py-3 text-[13px]">
      <div className="mx-auto flex max-w-5xl items-start gap-3">
        <TriangleAlert size={20} className="mt-0.5 shrink-0 text-crit" />
        <div className="min-w-0 flex-1">
          {state === 'down'
            ? <div className="font-semibold">Can't reach the backend. Start it, then reload this page.</div>
            : <div className="font-semibold">The backend is running an older version than this page, so some screens will not work (they load forever, and pasted links are ignored).</div>}
          <div className="mt-1 text-ink-2">
            {state === 'old' && <>Stop it (click its terminal window and press <b>Ctrl+C</b>), then start it again, and reload this page. Your cases are kept.</>}
            {state === 'down' && <>Start it from the project folder:</>}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <code className="mono block min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded border border-line-strong bg-black/40 px-2.5 py-1.5 text-[12px]">{CMD}</code>
            <button className="btn shrink-0 px-2.5 py-1.5 text-[12px]" onClick={() => { navigator.clipboard.writeText(CMD); setCopied(true); setTimeout(() => setCopied(false), 1500) }}>
              {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <div className="mt-1 text-[11.5px] text-ink-3">Using Docker instead? Run <span className="mono">docker compose up --build</span>.</div>
        </div>
      </div>
    </div>
  )
}

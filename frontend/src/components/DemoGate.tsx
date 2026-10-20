import { FlaskConical, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { LINKS } from '../lib/links'

/** The "soft error": shown when someone tries something that needs the analysis backend while the saved demo is on. */
export function DemoGate() {
  const [open, setOpen] = useState(false)
  const close = useRef<HTMLButtonElement>(null)
  const opener = useRef<HTMLElement | null>(null)
  useEffect(() => {
    const on = () => { opener.current = document.activeElement as HTMLElement | null; setOpen(true) }
    window.addEventListener('dfir-demo-blocked', on)
    return () => window.removeEventListener('dfir-demo-blocked', on)
  }, [])
  useEffect(() => {
    if (!open) return
    close.current?.focus()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey); opener.current?.focus?.() }
  }, [open])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4" onClick={() => setOpen(false)}>
      <div role="dialog" aria-modal="true" aria-labelledby="demo-gate-h" className="pop relative w-full max-w-md p-6" onClick={(e) => e.stopPropagation()}>
        <button className="btn btn-quiet absolute right-3 top-3 !min-h-0 !p-1.5" onClick={() => setOpen(false)} aria-label="Close"><X size={16} aria-hidden /></button>
        <div className="flex items-center gap-2.5"><span className="icon-chip !h-10 !w-10"><FlaskConical size={19} aria-hidden /></span>
          <h2 id="demo-gate-h" className="display text-[20px] font-bold leading-tight">This is a saved demo</h2></div>
        <p className="mt-4 text-[14px] leading-relaxed text-ink-2">
          To run more analysis, please connect the backend. This site shows completed example cases and has no analysis engine attached, so uploading, starting an
          investigation, re-running checks and deleting are switched off.
        </p>
        <p className="mt-2 text-[13px] text-ink-3">Everything you can see here is real output from the app. You can run it yourself with Docker; nothing needs installing by hand.</p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button ref={close} className="btn" onClick={() => setOpen(false)}>Keep exploring</button>
          <a className="btn" href={LINKS.drive} target="_blank" rel="noreferrer">Project description</a>
          <a className="btn btn-primary" href={LINKS.readme} target="_blank" rel="noreferrer">Run it yourself</a>
        </div>
      </div>
    </div>
  )
}

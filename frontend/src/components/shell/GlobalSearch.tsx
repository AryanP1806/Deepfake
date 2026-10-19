import { Search } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api'

interface Hit { type: string; title: string; subtitle: string; case_id: string; route: string }

/** Search cases, media (name or hash), places (URL, domain, handle, caption). Ctrl/Cmd+K focuses it. */
export function GlobalSearch() {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [loading, setLoading] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLDivElement>(null)
  const nav = useNavigate()
  const listId = useId()
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); input.current?.focus(); input.current?.select() }
    }
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown) }
  }, [])

  useEffect(() => {
    if (q.trim().length < 2) { setHits([]); setLoading(false); return }
    setLoading(true)
    const t = setTimeout(() => {
      api.get<{ results: Hit[] }>(`/api/search?q=${encodeURIComponent(q.trim())}`).then((r) => { setHits(r.results); setActive(-1) }).catch(() => setHits([])).finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const go = (h: Hit) => { setOpen(false); setQ(''); nav(h.route) }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive((a) => Math.min(hits.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter' && hits.length) { e.preventDefault(); go(hits[active >= 0 ? active : 0]) }
    else if (e.key === 'Escape') { setOpen(false); input.current?.blur() }
  }
  const showPanel = open && q.trim().length >= 2

  return (
    <div ref={box} className="relative w-full max-w-xl" role="search">
      <div className="well flex items-center gap-2.5 px-3.5 py-2 !rounded-[14px]">
        <Search size={17} className="shrink-0 text-ink-3" aria-hidden />
        <input ref={input} value={q} onChange={(e) => { setQ(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)} onKeyDown={onKeyDown}
          role="combobox" aria-expanded={showPanel} aria-controls={listId} aria-autocomplete="list" aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          aria-label="Search cases, keywords, domains or hashes" placeholder="Search cases, keywords, domains, or hashes…" autoComplete="off" spellCheck={false}
          className="min-w-0 flex-1 bg-transparent text-[13.5px] text-ink outline-none placeholder:text-ink-3 focus-visible:!outline-none" />
        <kbd className="tag hidden shrink-0 !text-[11px] sm:inline-flex">{isMac ? '⌘' : 'Ctrl'} K</kbd>
      </div>
      {showPanel && (
        <ul id={listId} role="listbox" aria-label="Search results" className="pop absolute left-0 right-0 top-full z-40 mt-2 max-h-96 overflow-y-auto p-1.5">
          {loading && hits.length === 0 && <li className="px-3 py-2.5 text-[13px] text-ink-3" role="status">Searching…</li>}
          {!loading && hits.length === 0 && <li className="px-3 py-2.5 text-[13px] text-ink-3" role="status">Nothing matches “{q.trim()}”. Try part of a title, a domain, an @handle or the start of a file hash.</li>}
          {hits.map((h, i) => (
            <li key={`${h.route}-${i}`} id={`${listId}-${i}`} role="option" aria-selected={i === active}>
              <button className={`nav-item w-full !items-start text-left ${i === active ? '!bg-raised' : ''}`} onMouseEnter={() => setActive(i)} onClick={() => go(h)}>
                <span className="tag mt-0.5 shrink-0 !text-[10.5px]">{h.type}</span>
                <span className="min-w-0"><span className="block truncate text-[13px] font-medium text-ink">{h.title}</span><span className="block truncate text-[11.5px] text-ink-3">{h.subtitle}</span></span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

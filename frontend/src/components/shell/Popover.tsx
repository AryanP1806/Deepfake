import { useEffect, useId, useRef, useState, type ReactNode } from 'react'

/** A button that opens a floating panel. Closes on outside click and Escape (focus returns to the button). */
export function Popover({ button, children, align = 'right', width = 'w-80', label, className = '' }: {
  button: (p: { open: boolean; toggle: () => void; id: string; ref: React.RefObject<HTMLButtonElement | null> }) => ReactNode
  children: (close: () => void) => ReactNode
  align?: 'left' | 'right'
  width?: string
  label: string
  className?: string
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const id = useId()
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus() } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey) }
  }, [open])
  return (
    <div ref={box} className={`relative ${className}`}>
      {button({ open, toggle: () => setOpen((o) => !o), id, ref: btn })}
      {open && (
        <div id={id} role="dialog" aria-label={label} className={`pop absolute top-full z-40 mt-2 ${width} ${align === 'right' ? 'right-0' : 'left-0'} max-w-[calc(100vw-1.5rem)]`}>
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  )
}

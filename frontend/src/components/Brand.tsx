import { Link } from 'react-router-dom'

export function Brand({ small = false }: { small?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5">
      <svg width={small ? 24 : 30} height={small ? 24 : 30} viewBox="0 0 32 32" aria-hidden>
        <rect x="1" y="1" width="30" height="30" rx="8" fill="none" stroke="#f2f2ee" strokeWidth="1.5" />
        <circle cx="16" cy="16" r="7" fill="none" stroke="#f2f2ee" strokeWidth="1.5" />
        <circle cx="16" cy="16" r="2.4" fill="#f2f2ee" />
        <path d="M16 3v5M16 24v5M3 16h5M24 16h5" stroke="#8f8e86" strokeWidth="1.5" />
      </svg>
      <div className="leading-tight">
        <div className={`font-semibold tracking-tight ${small ? 'text-sm' : 'text-base'}`}>DFIR</div>
        {!small && <div className="text-[11px] text-ink-3">Deepfake Investigation & Response</div>}
      </div>
    </Link>
  )
}

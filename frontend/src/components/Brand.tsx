import { Link } from 'react-router-dom'

export function Brand({ small = false }: { small?: boolean }) {
  return (
    <Link to="/" className="flex items-center gap-2.5" aria-label="DFIR home">
      <svg width={small ? 24 : 30} height={small ? 24 : 30} viewBox="0 0 32 32" aria-hidden className="text-ink">
        <rect x="1.5" y="1.5" width="29" height="29" rx="7" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="16" cy="16" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <circle cx="16" cy="16" r="2.3" fill="var(--color-accent-text)" />
        <path d="M16 3.5v5M16 23.5v5M3.5 16h5M23.5 16h5" stroke="var(--color-ink-3)" strokeWidth="1.5" />
      </svg>
      <div className="leading-tight">
        <div className={`display font-semibold tracking-tight ${small ? 'text-[15px]' : 'text-lg'}`}>DFIR</div>
        {!small && <div className="text-[11px] text-ink-3">Deepfake Investigation &amp; Response</div>}
      </div>
    </Link>
  )
}

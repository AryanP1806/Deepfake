import { Archive, ArchiveRestore, CircleAlert, CircleCheck, CircleHelp, EllipsisVertical, FolderOpen, Loader, Trash2 } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtTime, INCIDENT_TYPES } from '../../api'
import { caseBadge, TONE_VAR, type CaseSummary, type Tone } from '../../dashboard'
import { Popover } from '../shell/Popover'
import { SensitiveImg } from '../ui'

const TONE_ICON: Record<Tone, typeof CircleAlert> = { crit: CircleAlert, warn: CircleHelp, good: CircleCheck, info: Loader, neutral: CircleHelp }

export function CaseBadge({ c }: { c: Pick<CaseSummary, 'verdict' | 'investigating'> }) {
  const b = caseBadge(c)
  const Icon = TONE_ICON[b.tone]
  return (
    <span className="badge" style={{ '--b': TONE_VAR[b.tone] } as React.CSSProperties}>
      <Icon size={14} className={b.busy ? 'animate-spin' : ''} aria-hidden />{b.label}
    </span>
  )
}

const KIND: Record<string, string> = { image: 'Image', video: 'Video', audio: 'Audio' }

export function CaseRow({ c, onDelete, onChanged }: { c: CaseSummary; onDelete: (c: CaseSummary) => void; onChanged: () => void }) {
  const nav = useNavigate()
  const when = new Date(c.created_at)
  const day = when.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' })
  const time = when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', hour12: false })
  const sub = [c.modality ? KIND[c.modality] : INCIDENT_TYPES[c.incident_type], `${c.media_count} media`, `${c.location_count} places`].join(' · ')
  const archived = c.status === 'archived'
  return (
    <li className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 px-3 py-3 sm:grid-cols-[auto_minmax(0,1fr)_9rem_6.5rem_auto]">
      <Link to={`/case/${c.id}/overview`} className="block" tabIndex={-1} aria-hidden>
        {c.thumb ? <SensitiveImg caseId={c.id} sha={c.thumb} blur={c.sensitive} className="h-12 w-16 rounded-lg bg-black/80" />
          : <span className="grid h-12 w-16 place-items-center rounded-lg bg-raised text-ink-3"><FolderOpen size={18} /></span>}
      </Link>
      <div className="min-w-0">
        <Link to={`/case/${c.id}/overview`} className="block truncate text-[14px] font-semibold text-ink hover:underline">{c.title}</Link>
        <div className="truncate text-[12.5px] text-ink-3">{sub}{c.is_demo ? ' · demo' : ''}</div>
      </div>
      <div className="hidden sm:block"><CaseBadge c={c} /></div>
      <div className="hidden text-right text-[12.5px] leading-tight text-ink-3 sm:block"><div>{day}</div><div>{time}</div></div>
      <Popover label={`Options for ${c.title}`} width="w-52"
        button={({ open, toggle, id, ref }) => (
          <button ref={ref} onClick={toggle} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} className="btn btn-quiet !min-h-[36px] !w-[36px] justify-center !rounded-full !p-0">
            <EllipsisVertical size={17} aria-hidden /><span className="sr-only">Options for {c.title}</span>
          </button>
        )}>
        {(close) => (
          <div role="menu" className="p-1.5">
            <button role="menuitem" className="nav-item w-full text-left text-[13px]" onClick={() => { close(); nav(`/case/${c.id}/overview`) }}><FolderOpen size={15} aria-hidden /> Open case</button>
            <button role="menuitem" className="nav-item w-full text-left text-[13px]" onClick={async () => { close(); await api.patch(`/api/cases/${c.id}`, { status: archived ? 'open' : 'archived' }); onChanged() }}>
              {archived ? <ArchiveRestore size={15} aria-hidden /> : <Archive size={15} aria-hidden />} {archived ? 'Restore' : 'Archive'}
            </button>
            <button role="menuitem" className="nav-item w-full text-left text-[13px] text-crit" onClick={() => { close(); onDelete(c) }}><Trash2 size={15} aria-hidden /> Delete…</button>
          </div>
        )}
      </Popover>
      <div className="col-span-3 sm:hidden"><div className="flex items-center justify-between"><CaseBadge c={c} /><span className="text-[12px] text-ink-3">{fmtTime(c.created_at, false)}</span></div></div>
    </li>
  )
}

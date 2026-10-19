import { Bell, ChevronDown, Menu, Moon, Settings, Sun, Terminal } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtTime } from '../../api'
import { useDashboard } from '../../dashboard'
import { usePrefs } from '../../prefs'
import { GlobalSearch } from './GlobalSearch'
import { Popover } from './Popover'

function ModeSwitch() {
  const { mode, set } = usePrefs()
  const dark = mode === 'dark'
  return (
    <div className="hidden items-center gap-2 sm:flex">
      <Sun size={16} className="text-ink-3" aria-hidden />
      <button role="switch" aria-checked={dark} aria-label="Dark mode" onClick={() => set({ mode: dark ? 'light' : 'dark' })}
        className="well relative h-[26px] w-[48px] !rounded-full p-0">
        <span className={`absolute top-[2px] h-[20px] w-[20px] rounded-full bg-accent transition-all ${dark ? 'left-[24px]' : 'left-[3px]'}`} />
      </button>
      <Moon size={16} className="text-ink-3" aria-hidden />
    </div>
  )
}

function Notifications() {
  const { data, reload } = useDashboard()
  const nav = useNavigate()
  const n = data?.counts.alerts ?? 0
  const markAll = async (close: () => void) => {
    const ids = [...new Set((data?.alerts ?? []).map((a) => a.case_id))]
    await Promise.all(ids.map((id) => api.post(`/api/cases/${id}/alerts/seen`).catch(() => null)))
    reload(); close()
  }
  return (
    <Popover label="Notifications" width="w-96"
      button={({ open, toggle, id, ref }) => (
        <button ref={ref} onClick={toggle} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} className="btn relative !min-h-[40px] !w-[40px] justify-center !rounded-full !p-0">
          <Bell size={18} aria-hidden />
          {n > 0 && <span className="absolute right-[7px] top-[7px] h-2.5 w-2.5 rounded-full bg-crit ring-2 ring-[var(--color-surface)]" />}
          <span className="sr-only">Notifications{n > 0 ? `: ${n} unseen` : ': none'}</span>
        </button>
      )}>
      {(close) => (
        <div className="p-2">
          <div className="flex items-center justify-between px-2.5 py-1.5"><div className="font-semibold">Alerts</div>{n > 0 && <button className="btn btn-quiet !min-h-0 !px-2 !py-1 text-[12px]" onClick={() => markAll(close)}>Mark all as seen</button>}</div>
          {n === 0 ? <p className="px-2.5 py-3 text-[13px] text-ink-3">No new alerts. Turn on monitoring in a case to be told when a copy is removed, changed or re-posted.</p> : (
            <ul className="max-h-80 overflow-y-auto">
              {data!.alerts.map((a) => (
                <li key={a.id}><button className="nav-item w-full !items-start text-left" onClick={() => { close(); nav(`/case/${a.case_id}/monitor`) }}>
                  <span className="min-w-0"><span className="block text-[13px] font-medium text-ink">{a.message}</span><span className="block truncate text-[11.5px] text-ink-3">{a.case_title} · {fmtTime(a.created_at, false)}</span></span>
                </button></li>
              ))}
            </ul>
          )}
          <div className="border-t border-line px-2.5 pt-2"><Link to="/monitoring" onClick={close} className="text-[12.5px] font-semibold text-accent-text">Open monitoring</Link></div>
        </div>
      )}
    </Popover>
  )
}

function BackendMenu() {
  const { data } = useDashboard()
  const b = data?.system.backend
  return (
    <Popover label="Backend" width="w-72"
      button={({ open, toggle, id, ref }) => (
        <button ref={ref} onClick={toggle} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined} className="btn !min-h-[40px] gap-2 !px-3.5">
          <Terminal size={16} aria-hidden /><span className="hidden md:inline">Lab · backend</span><ChevronDown size={14} aria-hidden />
        </button>
      )}>
      {(close) => (
        <div className="p-2 text-[13px]">
          <div className="flex items-center gap-2 px-2.5 py-2"><span className="dot" style={{ '--d': b ? 'var(--color-good)' : 'var(--color-crit)' } as React.CSSProperties} />
            <span className="font-semibold">{b?.running ? `Backend running · v${b.version}` : 'Backend not reachable'}</span></div>
          <Link to="/lab" onClick={close} className="nav-item">Open the Lab (system, logs, quick analysis)</Link>
          <a href="/docs" target="_blank" rel="noreferrer" onClick={close} className="nav-item">API documentation</a>
          <Link to="/settings?tab=detectors" onClick={close} className="nav-item">Detectors &amp; models</Link>
        </div>
      )}
    </Popover>
  )
}

export function TopBar({ onMenu }: { onMenu: () => void }) {
  return (
    <header className="flex items-center gap-3 px-4 py-4 lg:px-6">
      <button className="btn !min-h-[40px] !w-[40px] justify-center !p-0 lg:hidden" onClick={onMenu} aria-label="Open navigation"><Menu size={18} /></button>
      <div className="min-w-0 flex-1"><GlobalSearch /></div>
      <div className="flex items-center gap-3">
        <ModeSwitch />
        <Notifications />
        <BackendMenu />
        <Link to="/settings" className="btn !min-h-[40px] !w-[40px] justify-center !rounded-full !p-0" title="Settings"><Settings size={18} aria-hidden /><span className="sr-only">Settings</span></Link>
      </div>
    </header>
  )
}

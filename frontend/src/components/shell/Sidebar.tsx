import { AudioLines, ChevronRight, Cpu, Database, FileText, FolderOpen, House, Image as ImageIcon, Network, Radio, ScanSearch, Search, Settings as SettingsIcon, Smartphone, Video, Workflow, type LucideIcon } from 'lucide-react'
import { Link, NavLink } from 'react-router-dom'
import { Brand } from '../Brand'
import { useDashboard } from '../../dashboard'
import { usePrefs } from '../../prefs'

interface Item { to: string; label: string; icon: LucideIcon; end?: boolean; badge?: 'alerts' }

const MAIN: Item[] = [
  { to: '/', label: 'Home', icon: House, end: true },
  { to: '/cases', label: 'Cases', icon: FolderOpen },
  { to: '/investigation', label: 'Investigation', icon: Workflow },
  { to: '/lab', label: 'Media Lab', icon: ImageIcon },
  { to: '/url-search', label: 'URL Search', icon: Search },
  { to: '/monitoring', label: 'Monitoring', icon: Radio, badge: 'alerts' },
  { to: '/vault', label: 'Evidence Vault', icon: Database },
  { to: '/reports', label: 'Reports', icon: FileText },
]
export const TOOL_NAV: Item[] = [
  { to: '/tools/image', label: 'Image Forensics', icon: ScanSearch },
  { to: '/tools/video', label: 'Video Forensics', icon: Video },
  { to: '/tools/audio', label: 'Audio Forensics', icon: AudioLines },
  { to: '/tools/generator', label: 'AI Generator Check', icon: Cpu },
  { to: '/tools/device', label: 'Device Fingerprinting', icon: Smartphone },
  { to: '/tools/graph', label: 'Graph & Link Analysis', icon: Network },
]

function NavItem({ item, alerts }: { item: Item; alerts: number }) {
  return (
    <NavLink to={item.to} end={item.end} className="nav-item mb-1 text-[14px] font-medium">
      <item.icon size={19} className="shrink-0" aria-hidden />
      <span className="flex-1">{item.label}</span>
      {item.badge === 'alerts' && alerts > 0 && (
        <span className="grid h-5 min-w-5 place-items-center rounded-full bg-crit px-1.5 text-[11px] font-bold text-crit-ink">
          {alerts}<span className="sr-only"> unseen alerts</span>
        </span>
      )}
    </NavLink>
  )
}

export function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data } = useDashboard()
  const { prefs } = usePrefs()
  const alerts = data?.counts.alerts ?? 0
  const name = prefs.profile?.name?.trim() || ''
  const initials = (name || 'You').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()
  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={onClose} aria-hidden />}
      <aside aria-label="Primary" className={`fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col overflow-y-auto bg-bg px-4 py-5 transition-transform lg:static lg:translate-x-0 ${open ? 'translate-x-0 shadow-2xl' : '-translate-x-full'}`}>
        <div className="px-1 pb-5"><Brand /></div>
        <nav aria-label="Main">
          {MAIN.map((it) => <NavItem key={it.to} item={it} alerts={alerts} />)}
        </nav>
        <div className="label mt-6 px-3 pb-2">Analysis tools</div>
        <nav aria-label="Analysis tools">
          {TOOL_NAV.map((it) => <NavItem key={it.to} item={it} alerts={0} />)}
        </nav>
        <div className="mt-auto pt-6">
          <Link to="/settings" className="nav-item mb-3 !px-3 text-[14px] font-medium"><SettingsIcon size={19} aria-hidden /> <span>Settings</span></Link>
          <Link to="/settings?tab=appearance#profile" className="tile flex items-center gap-3 p-3 text-left" aria-label={`${name || 'Add your name'}, ${prefs.profile?.role || 'Investigator'}. Open profile settings`}>
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-accent text-[13px] font-bold text-accent-ink" aria-hidden>{initials}</span>
            <span className="min-w-0 flex-1 leading-tight">
              <span className="block truncate text-[13.5px] font-semibold text-ink">{name || 'Add your name'}</span>
              <span className="block truncate text-[12px] text-ink-3">{prefs.profile?.role || 'Investigator'}</span>
            </span>
            <ChevronRight size={16} className="shrink-0 text-ink-3" aria-hidden />
          </Link>
        </div>
      </aside>
    </>
  )
}

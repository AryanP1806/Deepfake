import {
  Activity, Archive, ChevronDown, FlaskConical, Bell, Compass, FileWarning, GitBranch, Globe2, HandHelping, LayoutDashboard, ListChecks,
  Camera, Cpu, Microscope, MoreHorizontal, Moon, Network, Radar, ScanSearch, Send, Settings as SettingsIcon, ShieldAlert, Sun, Trash2, Upload, Users,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, INCIDENT_TYPES } from '../api'
import { Brand } from '../components/Brand'
import { Spinner, StatusLegend } from '../components/ui'
import { Provider, Toasts, useConsentGate, useToasts, type CaseData } from '../context'
import { useApi, useCaseEvents } from '../hooks'
import Complaint from '../views/Complaint'
import Decomposition from '../views/Decomposition'
import Evidence from '../views/Evidence'
import Gaps from '../views/Gaps'
import Genealogy from '../views/Genealogy'
import Graph from '../views/Graph'
import Leads from '../views/Leads'
import Monitoring from '../views/Monitoring'
import NextAction from '../views/NextAction'
import Overview from '../views/Overview'
import Propagation from '../views/Propagation'
import { casePath } from '../lib/paths'
import { AnalysisStatus } from '../components/AnalysisStatus'
import { DeleteCaseDialog } from '../components/DeleteCaseDialog'
import { InvestigationProgress } from '../components/InvestigationProgress'
import Spread from '../views/Spread'
import Takedown from '../views/Takedown'
import Source from '../views/Source'
import Forensics from '../views/Forensics'
import Generator from '../views/Generator'
import Timeline from '../views/Timeline'
import { usePrefs } from '../prefs'
import { guardDemo } from '../lib/demo'

/** Main path (plain language) first; the specialist tools live under "Advanced". */
export const STEPS = [
  { path: 'overview', label: 'Overview', hint: 'What we found, in one page', icon: LayoutDashboard, group: 'main' },
  { path: 'decomposition', label: '1 · Is it fake?', hint: 'AI and editing checks', icon: ScanSearch, group: 'main' },
  { path: 'spread', label: '2 · Where it appeared', hint: 'Copies, versions, places', icon: Radar, group: 'main' },
  { path: 'takedown', label: '3 · Take action', hint: 'Get it removed, register the complaint', icon: HandHelping, group: 'main' },
  { path: 'forensics', label: 'Forensic modules', hint: 'Sensor noise, camera, editing, video, audio, provenance', icon: Microscope, group: 'deep' },
  { path: 'source', label: 'Device & source', hint: 'Camera, software, timestamps', icon: Camera, group: 'deep' },
  { path: 'generator', label: 'AI generator', hint: 'Which tool family made it?', icon: Cpu, group: 'deep' },
  { path: 'leads', label: 'Accounts & leads', hint: 'Roles, graph, ranked leads', icon: Users, group: 'deep' },
  { path: 'complaint', label: 'Complaint form', hint: 'Prepared text for the portal', icon: Send, group: 'adv' },
  { path: 'evidence', label: 'Evidence package', hint: 'Preserve · hashes · custody', icon: Archive, group: 'adv' },
  { path: 'timeline', label: 'Timeline', hint: 'What happened when', icon: Activity, group: 'adv' },
  { path: 'genealogy', label: 'Media genealogy', hint: 'How the versions differ', icon: GitBranch, group: 'adv' },
  { path: 'propagation', label: 'Pages & captures', hint: 'Captured pages, archive records', icon: Globe2, group: 'adv' },
  { path: 'graph', label: 'Evidence graph', hint: 'Connections between items', icon: Network, group: 'adv' },
  { path: 'gaps', label: 'Evidence gaps', hint: 'What is still unknown', icon: FileWarning, group: 'adv' },
  { path: 'act', label: 'Checklist', hint: 'Do / do not list', icon: Compass, group: 'adv' },
  { path: 'monitor', label: 'Monitoring', hint: 'Watch for removals and re-uploads', icon: Bell, group: 'adv' },
]

export default function CaseWorkspace() {
  const { caseId = '' } = useParams()
  const [tick, setTick] = useState(0)
  const { data, reload } = useApi<CaseData>(`/api/cases/${caseId}`, [tick])
  const refresh = useCallback(() => setTick((t) => t + 1), [])
  const jobs = useCaseEvents(caseId, refresh)
  const { withConsent, dialog } = useConsentGate(caseId)
  const { items, toast } = useToasts()
  const [uploading, setUploading] = useState(false)
  const [advOpen, setAdvOpen] = useState(false)
  const [menu, setMenu] = useState(false)
  const [del, setDel] = useState(false)
  const [keyOpen, setKeyOpen] = useState(false)
  const nav = useNavigate()
  const { mode, set: setPrefs } = usePrefs()
  const { data: plan } = useApi<any>(`/api/cases/${caseId}/takedown`, [tick])

  const upload = async (f: File, role = 'primary') => {
    setUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', f)
      fd.append('role', role)
      await api.post(`/api/cases/${caseId}/media`, fd)
      toast(`${f.name} preserved and queued for analysis`)
      reload()
    } catch (e) {
      toast((e as Error).message, 'err')
    } finally {
      setUploading(false)
    }
  }

  if (!data) return <div className="p-10"><Spinner label="Opening case" /></div>
  const c = data.case
  const running = Object.values(jobs).filter((j) => j.status !== 'done')

  return (
    <Provider value={{ data, caseId, tick, refresh, withConsent, toast }}>
      <div className="flex h-full">
        <aside className="flex w-[17rem] shrink-0 flex-col border-r border-line bg-surface" aria-label="Case navigation">
          <div className="flex items-center justify-between border-b border-line px-4 py-3.5">
            <Brand small />
            <div className="flex items-center gap-0.5">
              <NavLink to="/lab" className="btn btn-quiet !min-h-0 !p-1.5" title="Lab: run a single file without a case"><FlaskConical size={15} /><span className="sr-only">Lab</span></NavLink>
              <NavLink to="/settings" className="btn btn-quiet !min-h-0 !p-1.5" title="Settings and appearance"><SettingsIcon size={15} /><span className="sr-only">Settings</span></NavLink>
            </div>
          </div>
          <div className="border-b border-line px-4 py-3.5">
            <div className="flex items-center gap-2">
              {c.is_demo && <span className="tag">DEMO</span>}
              <span className="label">{INCIDENT_TYPES[c.incident_type]}</span>
            </div>
            <div className="mt-1.5 flex items-start justify-between gap-2">
              <h1 className="display line-clamp-3 text-[17px] font-semibold leading-snug">{c.title}</h1>
              <div className="relative">
                <button className="btn btn-quiet !min-h-0 !p-1.5" title="Case options" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}><MoreHorizontal size={15} /><span className="sr-only">Case options</span></button>
                {menu && (
                  <div role="menu" className="pop absolute right-0 top-9 z-30 w-48 p-1" onMouseLeave={() => setMenu(false)}>
                    <button role="menuitem" className="nav-item w-full text-left text-[13px]" onClick={() => { setMenu(false); nav('/') }}><LayoutDashboard size={14} /> All cases</button>
                    <button role="menuitem" className="nav-item w-full text-left text-[13px] text-crit" onClick={() => { setMenu(false); setDel(true) }}><Trash2 size={14} /> Archive or delete…</button>
                  </div>
                )}
              </div>
            </div>
            <div className="mono mt-1 text-[10.5px] text-ink-3">{c.id}</div>
          </div>
          <nav className="flex-1 overflow-y-auto px-2 py-3">
            <div className="label px-3 pb-1">Review</div>
            {STEPS.filter((x) => x.group === 'main').map((st) => (
              <NavLink key={st.path} to={casePath(caseId, st.path)} className="nav-item mb-0.5 text-[13.5px]">
                <st.icon size={16} className="shrink-0" aria-hidden />
                <span className="flex-1 font-medium leading-tight">{st.label}<span className="block text-[11px] font-normal text-ink-3">{st.hint}</span></span>
                {st.path === 'takedown' && plan?.summary?.overdue > 0 && <span className="rounded-full bg-crit px-1.5 py-0.5 text-[10px] font-bold text-crit-ink">{plan.summary.overdue} overdue</span>}
              </NavLink>
            ))}
            <div className="label mt-4 px-3 pb-1">Investigate deeper</div>
            {STEPS.filter((x) => x.group === 'deep').map((st) => (
              <NavLink key={st.path} to={casePath(caseId, st.path)} className="nav-item mb-0.5 text-[13px]">
                <st.icon size={15} className="shrink-0" aria-hidden />
                <span className="flex-1 leading-tight">{st.label}<span className="block text-[10.5px] text-ink-3">{st.hint}</span></span>
              </NavLink>
            ))}
            <button className="label mt-4 flex w-full items-center gap-1.5 px-3 py-1.5 hover:text-ink" aria-expanded={advOpen} onClick={() => setAdvOpen(!advOpen)}>
              <ChevronDown size={12} className={advOpen ? '' : '-rotate-90'} aria-hidden /> Advanced &amp; evidence
            </button>
            {(advOpen || STEPS.some((x) => x.group === 'adv' && window.location.pathname.includes(`/${x.path}`))) && STEPS.filter((x) => x.group === 'adv').map((st) => (
              <NavLink key={st.path} to={casePath(caseId, st.path)} className="nav-item text-[12.5px]">
                <st.icon size={14} className="shrink-0" aria-hidden />
                <span className="flex-1 leading-tight">{st.label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="space-y-1.5 border-t border-line p-3">
            <label onClick={guardDemo} className={`btn w-full justify-center ${uploading ? 'opacity-50' : ''}`}>
              <Upload size={14} aria-hidden /> {uploading ? 'Preserving…' : 'Add media file'}
              <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </label>
            <label onClick={guardDemo} className="btn btn-quiet w-full justify-center">
              <Upload size={14} aria-hidden /> Add authentic reference
              <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0], 'reference')} />
            </label>
          </div>
        </aside>

        <main id="main" tabIndex={-1} className="min-w-0 flex-1 overflow-y-auto outline-none">
          <div className="sticky top-0 z-20 border-b border-line bg-bg px-8 py-2">
            <div className="flex items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-2 text-[12.5px] text-ink-3">
                <NavLink to="/" className="hover:text-ink">All cases</NavLink><span aria-hidden>/</span><span className="truncate text-ink-2">{c.title}</span>
              </div>
              <div className="flex items-center gap-3">
                {running.slice(0, 2).map((j) => (
                  <div key={j.job_id} className="w-52" role="status">
                    <div className="flex justify-between text-[11px] text-ink-3"><span className="truncate">{j.kind ?? 'job'} · {j.message ?? j.status}</span><span>{Math.round((j.progress ?? 0) * 100)}%</span></div>
                    <div className="meter mt-1" aria-hidden><i style={{ width: `${Math.max(3, (j.progress ?? 0.03) * 100)}%` }} /></div>
                  </div>
                ))}
                <div className="relative">
                  <button className="btn btn-quiet !min-h-0 !px-2 !py-1 text-[12px]" aria-expanded={keyOpen} onClick={() => setKeyOpen(!keyOpen)}>Evidence key</button>
                  {keyOpen && <div className="pop absolute right-0 top-9 z-30 w-[26rem] p-4" onMouseLeave={() => setKeyOpen(false)}><StatusLegend stacked /></div>}
                </div>
                <button className="btn btn-quiet !min-h-0 !p-1.5" onClick={() => setPrefs({ mode: mode === 'dark' ? 'light' : 'dark' })} title={mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
                  {mode === 'dark' ? <Sun size={15} /> : <Moon size={15} />}<span className="sr-only">Toggle light and dark mode</span>
                </button>
              </div>
            </div>
          </div>
          {data.safety_notices.length > 0 && (
            <div className="space-y-2 px-8 pt-5">
              {data.safety_notices.map((n) => (
                <div key={n.title} role="note" className={`flex gap-3 rounded-lg border p-3 text-sm ${n.level === 'critical' ? 'border-crit bg-crit/10' : 'border-warn/60 bg-warn/5'}`}>
                  <ShieldAlert size={16} className={n.level === 'critical' ? 'text-crit' : 'text-warn'} aria-hidden />
                  <div><span className="font-semibold">{n.title}. </span><span className="text-ink-2">{n.text}</span></div>
                </div>
              ))}
            </div>
          )}
          <div className="px-8 py-6">
            <InvestigationProgress caseId={caseId} onChange={refresh} kick={tick} />
            <AnalysisStatus caseId={caseId} onChange={refresh} kick={tick} />
            <Routes>
              <Route index element={<Navigate to={casePath(caseId)} replace />} />
              <Route path="overview" element={<Overview />} />
              <Route path="decomposition" element={<Decomposition />} />
              <Route path="decomposition/:mediaId" element={<Decomposition />} />
              <Route path="spread" element={<Spread />} />
              <Route path="takedown" element={<Takedown />} />
              <Route path="forensics" element={<Forensics />} />
              <Route path="source" element={<Source />} />
              <Route path="generator" element={<Generator />} />
              <Route path="genealogy" element={<Genealogy />} />
              <Route path="propagation" element={<Propagation />} />
              <Route path="graph" element={<Graph />} />
              <Route path="timeline" element={<Timeline />} />
              <Route path="leads" element={<Leads />} />
              <Route path="gaps" element={<Gaps />} />
              <Route path="evidence" element={<Evidence />} />
              <Route path="complaint" element={<Complaint />} />
              <Route path="act" element={<NextAction />} />
              <Route path="monitor" element={<Monitoring />} />
              <Route path="*" element={<div className="text-ink-3"><ListChecks /> Not found</div>} />
            </Routes>
          </div>
        </main>
      </div>
      {dialog}
      {del && <DeleteCaseDialog caseId={caseId} title={c.title} onClose={() => setDel(false)} onDone={() => nav('/')} />}
      <Toasts items={items} />
    </Provider>
  )
}

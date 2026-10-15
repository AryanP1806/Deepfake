import {
  Activity, Archive, FlaskConical, Bell, Compass, FileWarning, GitBranch, Globe2, LayoutDashboard, ListChecks, Network, ScanSearch,
  Send, ShieldAlert, Upload, UserSearch,
} from 'lucide-react'
import { useCallback, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useParams } from 'react-router-dom'
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
import Timeline from '../views/Timeline'

export const STEPS = [
  { path: 'overview', label: 'Case overview', chain: 'Summary', icon: LayoutDashboard },
  { path: 'decomposition', label: 'Forensic decomposition', chain: 'Detect · Explain', icon: ScanSearch },
  { path: 'genealogy', label: 'Media genealogy', chain: 'Trace', icon: GitBranch },
  { path: 'propagation', label: 'Propagation', chain: 'Trace', icon: Globe2 },
  { path: 'graph', label: 'Evidence graph', chain: 'Connect', icon: Network },
  { path: 'timeline', label: 'Reconstruction', chain: 'Investigate', icon: Activity },
  { path: 'leads', label: 'Attribution leads', chain: 'Investigate', icon: UserSearch },
  { path: 'gaps', label: 'Evidence gaps', chain: 'Prove', icon: FileWarning },
  { path: 'evidence', label: 'Evidence package', chain: 'Preserve', icon: Archive },
  { path: 'complaint', label: 'Complaint', chain: 'Report', icon: Send },
  { path: 'act', label: 'What to do now', chain: 'Act', icon: Compass },
  { path: 'monitor', label: 'Monitoring', chain: 'Monitor', icon: Bell },
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
        <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface/60">
          <div className="flex items-center justify-between border-b border-line p-4"><Brand small /><NavLink to="/lab" className="flex items-center gap-1 text-[11px] text-ink-3 hover:text-ink"><FlaskConical size={12} /> Lab</NavLink></div>
          <div className="border-b border-line p-4">
            <div className="flex items-center gap-2">
              {c.is_demo && <span className="rounded border border-line-strong px-1 text-[10px] font-bold text-ink-3">DEMO</span>}
              <span className="text-[11px] text-ink-3">{INCIDENT_TYPES[c.incident_type]}</span>
            </div>
            <div className="mt-1 line-clamp-3 text-[13px] font-semibold leading-snug">{c.title}</div>
            <div className="mono mt-1 text-[10px] text-ink-3">{c.id}</div>
          </div>
          <nav className="flex-1 overflow-y-auto p-2">
            {STEPS.map((s, i) => (
              <NavLink key={s.path} to={casePath(caseId, s.path)}
                className={({ isActive }) => `group flex items-center gap-3 rounded-lg px-3 py-2 text-[13px] transition ${isActive ? 'bg-raised text-ink' : 'text-ink-2 hover:bg-raised/60'}`}>
                <span className="mono w-4 text-right text-[10px] text-ink-3">{i + 1}</span>
                <s.icon size={15} className="shrink-0" />
                <span className="flex-1 leading-tight">{s.label}<span className="block text-[10px] uppercase tracking-wider text-ink-3">{s.chain}</span></span>
              </NavLink>
            ))}
          </nav>
          <div className="border-t border-line p-3">
            <label className={`btn w-full justify-center ${uploading ? 'opacity-50' : ''}`}>
              <Upload size={14} /> {uploading ? 'Preserving…' : 'Add media file'}
              <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
            </label>
            <label className="btn mt-1.5 w-full justify-center text-ink-3">
              <Upload size={14} /> Add authentic reference
              <input type="file" hidden accept="image/*,video/*,audio/*" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0], 'reference')} />
            </label>
          </div>
        </aside>

        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="sticky top-0 z-20 border-b border-line bg-bg/90 px-8 py-2.5 backdrop-blur">
            <div className="flex items-center justify-between gap-4">
              <StatusLegend />
              {running.length > 0 && (
                <div className="flex items-center gap-3">
                  {running.slice(0, 2).map((j) => (
                    <div key={j.job_id} className="w-56">
                      <div className="flex justify-between text-[11px] text-ink-3"><span className="truncate">{j.kind ?? 'job'} · {j.message ?? j.status}</span><span>{Math.round((j.progress ?? 0) * 100)}%</span></div>
                      <div className="mt-1 h-1 overflow-hidden rounded bg-raised"><div className="h-full bg-ink-2 transition-all" style={{ width: `${(j.progress ?? 0.03) * 100}%` }} /></div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          {data.safety_notices.length > 0 && (
            <div className="space-y-2 px-8 pt-5">
              {data.safety_notices.map((n) => (
                <div key={n.title} className={`flex gap-3 rounded-lg border p-3 text-sm ${n.level === 'critical' ? 'border-crit bg-crit/10' : 'border-warn/60 bg-warn/5'}`}>
                  <ShieldAlert size={16} className={n.level === 'critical' ? 'text-crit' : 'text-warn'} />
                  <div><span className="font-semibold">{n.title}. </span><span className="text-ink-2">{n.text}</span></div>
                </div>
              ))}
            </div>
          )}
          <div className="px-8 py-6">
            <AnalysisStatus caseId={caseId} onChange={refresh} kick={tick} />
            <Routes>
              <Route index element={<Navigate to={casePath(caseId)} replace />} />
              <Route path="overview" element={<Overview />} />
              <Route path="decomposition" element={<Decomposition />} />
              <Route path="decomposition/:mediaId" element={<Decomposition />} />
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
      <Toasts items={items} />
    </Provider>
  )
}

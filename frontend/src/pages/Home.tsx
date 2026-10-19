import { AnalysisModules } from '../components/dashboard/AnalysisModules'
import { NewInvestigation } from '../components/dashboard/NewInvestigation'
import { RecentCases } from '../components/dashboard/RecentCases'
import { QuickActions, SystemStatus, Tips } from '../components/dashboard/SidePanels'

/** Home dashboard: start an investigation, jump to a tool, resume a case, see that the backend is healthy. */
export default function Home() {
  return (
    <div className="mx-auto grid max-w-[1500px] gap-6 xl:grid-cols-[minmax(0,1fr)_23rem]">
      <div className="min-w-0 space-y-6">
        <NewInvestigation />
        <AnalysisModules />
        <RecentCases />
      </div>
      <div className="space-y-6">
        <SystemStatus />
        <QuickActions />
        <Tips />
      </div>
    </div>
  )
}

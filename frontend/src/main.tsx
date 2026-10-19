import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import AppShell from './components/shell/AppShell'
import CaseList from './pages/CaseList'
import CaseWorkspace from './pages/CaseWorkspace'
import { MonitoringPage, ReportsPage, VaultPage } from './pages/GlobalViews'
import Home from './pages/Home'
import Investigation from './pages/Investigation'
import Lab from './pages/Lab'
import Settings from './pages/Settings'
import ToolPicker from './pages/ToolPicker'
import { BackendCheck } from './components/BackendCheck'
import { PrefsProvider } from './prefs'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PrefsProvider>
      <BrowserRouter>
        <BackendCheck />
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Home />} />
            <Route path="/cases" element={<CaseList />} />
            <Route path="/investigation" element={<Investigation />} />
            <Route path="/url-search" element={<Investigation copies />} />
            <Route path="/tools/:tool" element={<ToolPicker />} />
            <Route path="/monitoring" element={<MonitoringPage />} />
            <Route path="/vault" element={<VaultPage />} />
            <Route path="/reports" element={<ReportsPage />} />
            <Route path="/lab" element={<Lab />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
          <Route path="/case/:caseId/*" element={<CaseWorkspace />} />
        </Routes>
      </BrowserRouter>
    </PrefsProvider>
  </StrictMode>,
)

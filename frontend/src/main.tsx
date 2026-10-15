import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import CaseList from './pages/CaseList'
import CaseWorkspace from './pages/CaseWorkspace'
import Lab from './pages/Lab'
import Settings from './pages/Settings'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<CaseList />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/lab" element={<Lab />} />
        <Route path="/case/:caseId/*" element={<CaseWorkspace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)

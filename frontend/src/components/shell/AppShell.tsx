import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { DashboardProvider } from '../../dashboard'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

/** Global frame for every page outside a case: sidebar, top bar, and the page below. A drawer on narrow screens. */
export default function AppShell() {
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  useEffect(() => { setOpen(false) }, [loc.pathname])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])
  return (
    <DashboardProvider>
      <div className="flex h-full">
        <Sidebar open={open} onClose={() => setOpen(false)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar onMenu={() => setOpen(true)} />
          <main id="main" tabIndex={-1} className="flex-1 overflow-y-auto px-4 pb-8 pt-1 outline-none lg:px-6">
            <Outlet />
          </main>
        </div>
      </div>
    </DashboardProvider>
  )
}

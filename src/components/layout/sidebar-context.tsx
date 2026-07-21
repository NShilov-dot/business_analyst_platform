import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { PanelLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'

type SidebarContextValue = {
  collapsed: boolean
  toggle: () => void
  /** Mobile off-canvas drawer state (< lg). Independent of `collapsed`. */
  mobileOpen: boolean
  setMobileOpen: (value: boolean) => void
}

const SidebarContext = createContext<SidebarContextValue | null>(null)

export function useSidebar() {
  const ctx = useContext(SidebarContext)
  if (!ctx) throw new Error('useSidebar must be used within <SidebarProvider>')
  return ctx
}

const STORAGE_KEY = 'sidebar:collapsed'
// Keep in sync with Tailwind's `lg` breakpoint — below it the sidebar is a drawer.
const DESKTOP_QUERY = '(min-width: 1024px)'

export function SidebarProvider({ children }: { children: ReactNode }) {
  // Initialise from localStorage synchronously so the first paint already has
  // the right width (no collapse flicker on load).
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === 'true'
    } catch {
      return false
    }
  })
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, String(collapsed))
    } catch {
      /* localStorage unavailable — state stays in-memory only */
    }
  }, [collapsed])

  // Leaving the mobile breakpoint always closes the drawer so it can't linger
  // open (and block scroll) after a rotation / window resize.
  useEffect(() => {
    const mq = window.matchMedia(DESKTOP_QUERY)
    const onChange = (e: MediaQueryListEvent) => {
      if (e.matches) setMobileOpen(false)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const toggle = () => {
    if (window.matchMedia(DESKTOP_QUERY).matches) {
      setCollapsed((c) => !c)
    } else {
      setMobileOpen((o) => !o)
    }
  }

  return (
    <SidebarContext.Provider
      value={{ collapsed, toggle, mobileOpen, setMobileOpen }}
    >
      {children}
    </SidebarContext.Provider>
  )
}

export function SidebarTrigger() {
  const { toggle } = useSidebar()
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label="Открыть или скрыть меню"
      title="Меню"
      className="shrink-0"
    >
      <PanelLeft />
    </Button>
  )
}

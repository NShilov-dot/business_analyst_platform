import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import {
  FilePlus2,
  FileText,
  LayoutDashboard,
  ListTodo,
  LogOut,
  Moon,
  SquareKanban,
  Sun,
  User,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { useAuth } from '../../auth/AuthProvider'
import { useSidebar } from './sidebar-context'
import { cn } from '@/lib/utils'

type NavItem = { to: string; label: string; icon: LucideIcon; end?: boolean }

const NAV: NavItem[] = [
  { to: '/', label: 'Дашборд', icon: LayoutDashboard, end: true },
  { to: '/board', label: 'Доска заявок', icon: SquareKanban },
  { to: '/intake', label: 'Новая заявка', icon: FilePlus2 },
  { to: '/documents', label: 'Документы', icon: FileText },
]

const SECONDARY_NAV: NavItem[] = [
  { to: '/tasks', label: 'Задачи (демо)', icon: ListTodo },
  { to: '/profile', label: 'Профиль', icon: User },
]

// Shared row styling for nav links and footer action buttons. Collapsed ->
// centre the icon and drop the horizontal padding (desktop only — the mobile
// drawer always renders expanded). Active rows take the BuildX accent fill.
function rowClasses(collapsed: boolean, active: boolean) {
  return cn(
    'flex w-full items-center gap-3 rounded-xl h-10 text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card',
    collapsed ? 'px-3 lg:justify-center lg:px-0' : 'px-3',
    active
      ? 'bg-primary text-primary-foreground'
      : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
  )
}

function SectionLabel({ collapsed, children }: { collapsed: boolean; children: string }) {
  return (
    <div
      className={cn(
        'px-2 pb-2 pt-1.5 text-[10.5px] font-semibold uppercase tracking-wider text-muted-foreground',
        collapsed && 'lg:hidden',
      )}
    >
      {children}
    </div>
  )
}

export function AppSidebar() {
  const { collapsed, mobileOpen, setMobileOpen } = useSidebar()
  const { logout } = useAuth()
  const { resolvedTheme, setTheme } = useTheme()
  const { pathname } = useLocation()
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  const isDark = mounted && resolvedTheme === 'dark'

  // Navigating always dismisses the mobile drawer.
  useEffect(() => {
    setMobileOpen(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname])

  // Text labels: in the desktop collapsed state they are visually hidden
  // (sr-only) — the mobile drawer is always fully expanded.
  const labelClasses = cn('truncate', collapsed && 'lg:sr-only')

  const renderNav = (items: NavItem[]) =>
    items.map(({ to, label, icon: Icon, end }) => (
      <NavLink
        key={to}
        to={to}
        end={end}
        aria-label={label}
        title={collapsed ? label : undefined}
        className={({ isActive }) => rowClasses(collapsed, isActive)}
      >
        <Icon className="h-5 w-5 shrink-0" />
        <span className={labelClasses}>{label}</span>
      </NavLink>
    ))

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 lg:hidden"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        className={cn(
          'border-r border-border bg-card text-card-foreground flex flex-col',
          // Mobile: off-canvas drawer
          'fixed inset-y-0 left-0 z-50 w-[280px] max-w-[85vw] transition-transform duration-200 ease-in-out',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          // Desktop: static sticky column, width driven by `collapsed`
          'lg:sticky lg:top-0 lg:z-auto lg:h-screen lg:max-w-none lg:shrink-0 lg:translate-x-0 lg:overflow-hidden lg:transition-[width]',
          collapsed ? 'lg:w-16' : 'lg:w-[236px]',
        )}
      >
        {/* Brand */}
        <div
          className={cn(
            'flex h-[70px] shrink-0 items-center px-4',
            collapsed && 'lg:justify-center lg:px-0',
          )}
        >
          <Link
            to="/"
            aria-label="BuildX · AI Business Analyst — на главную"
            className="flex items-center gap-[11px]"
          >
            <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-primary text-[15px] font-bold text-primary-foreground">
              B
            </div>
            <div className={cn('leading-tight', collapsed && 'lg:hidden')}>
              <div className="text-sm font-bold">BuildX</div>
              <div className="text-[11px] text-muted-foreground">AI Business Analyst</div>
            </div>
          </Link>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            aria-label="Закрыть меню"
            className="ml-auto flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted lg:hidden"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto p-2 space-y-1">
          <SectionLabel collapsed={collapsed}>Навигация</SectionLabel>
          {renderNav(NAV)}
          <SectionLabel collapsed={collapsed}>Платформа</SectionLabel>
          {renderNav(SECONDARY_NAV)}
        </nav>

        {/* Double acceptance note from the design */}
        <div
          className={cn(
            'mx-3 mb-2 rounded-[14px] border border-border bg-muted/60 px-3 py-3.5',
            collapsed && 'lg:hidden',
          )}
        >
          <div className="mb-1.5 text-xs font-semibold">Двойная приёмка</div>
          <div className="text-[11.5px] leading-snug text-muted-foreground">
            Тикет закрывается только после гейта BA и подтверждения ценности бизнесом.
          </div>
        </div>

        {/* Footer: theme toggle + logout */}
        <div className="shrink-0 border-t border-border p-2 space-y-1">
          <button
            type="button"
            onClick={() => setTheme(isDark ? 'light' : 'dark')}
            aria-label={isDark ? 'Светлая тема' : 'Тёмная тема'}
            title={collapsed ? (isDark ? 'Светлая тема' : 'Тёмная тема') : undefined}
            className={rowClasses(collapsed, false)}
          >
            {isDark ? (
              <Sun className="h-5 w-5 shrink-0" />
            ) : (
              <Moon className="h-5 w-5 shrink-0" />
            )}
            <span className={labelClasses}>{isDark ? 'Светлая тема' : 'Тёмная тема'}</span>
          </button>

          <button
            type="button"
            onClick={logout}
            aria-label="Выйти"
            title={collapsed ? 'Выйти' : undefined}
            className={rowClasses(collapsed, false)}
          >
            <LogOut className="h-5 w-5 shrink-0" />
            <span className={labelClasses}>Выйти</span>
          </button>
        </div>
      </aside>
    </>
  )
}

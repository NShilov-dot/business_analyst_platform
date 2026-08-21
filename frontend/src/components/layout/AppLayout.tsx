import { type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Bell, Plus, Search } from 'lucide-react'
import { SidebarProvider, SidebarTrigger } from './sidebar-context'
import { AppSidebar } from './AppSidebar'
import { Toaster } from '@/components/ui/sonner'
import { useAuth } from '../../auth/AuthProvider'

// Route → header title/subtitle, per the design's page map. First matching
// prefix wins, so keep the more specific paths on top.
const PAGE_TITLES: Array<[string, string, string]> = [
  ['/board', 'Доска заявок', 'Поток запросов по статусам жизненного цикла.'],
  ['/tickets', 'Карточка заявки', 'Трассируемость требований и двойная приёмка.'],
  ['/intake', 'Новая заявка', 'Опишите задачу ассистенту — он задаст вопросы и соберёт заявку.'],
  ['/tasks', 'Задачи (демо)', 'Стартовый демо-модуль платформы.'],
  ['/profile', 'Профиль', 'Данные текущей сессии.'],
  ['/', 'Обзор портфеля', 'Ключевые метрики приёма и ведения бизнес-запросов.'],
]

// Keycloak realm roles → display label for the header chip.
const ROLE_LABELS: Array<[string, string]> = [
  ['ba', 'Бизнес-аналитик'],
  ['business_owner', 'Бизнес-заказчик'],
  ['executor', 'Исполнитель'],
  ['approver', 'Согласующий'],
  ['tenant_admin', 'Администратор'],
  ['platform_admin', 'Администратор платформы'],
]

function IconButton({ icon: Icon, label }: { icon: typeof Search; label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      className="relative hidden h-10 w-10 items-center justify-center rounded-[11px] border border-border bg-card hover:bg-muted md:flex"
    >
      <Icon className="h-5 w-5" />
      {label === 'Уведомления' && (
        <span className="absolute right-2.5 top-2 h-[7px] w-[7px] rounded-full bg-destructive" />
      )}
    </button>
  )
}

function Header() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const { user } = useAuth()

  const [, title, subtitle] =
    PAGE_TITLES.find(([prefix]) =>
      prefix === '/' ? pathname === '/' : pathname.startsWith(prefix),
    ) ?? PAGE_TITLES[PAGE_TITLES.length - 1]

  const roleLabel =
    ROLE_LABELS.find(([role]) => user.roles.includes(role))?.[1] ?? 'Заявитель'
  const initials = user.subject.slice(0, 2).toUpperCase()

  return (
    <header className="sticky top-0 z-10 flex shrink-0 items-center gap-3 border-b border-border bg-card px-3 py-3 sm:gap-5 sm:px-8 sm:pb-[18px] sm:pt-[22px]">
      <SidebarTrigger />
      <div className="min-w-0">
        <h1 className="truncate text-[17px] font-bold tracking-tight sm:text-[23px]">
          {title}
        </h1>
        <p className="mt-[3px] hidden text-[13.5px] text-muted-foreground sm:block">
          {subtitle}
        </p>
      </div>
      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <button
          type="button"
          onClick={() => navigate('/intake')}
          aria-label="Новая заявка"
          className="flex items-center gap-[7px] rounded-[11px] bg-primary p-2.5 text-[13px] font-semibold text-primary-foreground hover:bg-primary/90 sm:px-[15px]"
        >
          <Plus className="h-[19px] w-[19px]" />
          <span className="hidden sm:inline">Новая заявка</span>
        </button>
        <IconButton icon={Search} label="Поиск" />
        <IconButton icon={Bell} label="Уведомления" />
        <div className="flex items-center gap-[9px] sm:pl-1.5">
          <div className="flex h-[38px] w-[38px] flex-none items-center justify-center rounded-full bg-foreground text-[13px] font-bold text-primary">
            {initials}
          </div>
          <div className="hidden leading-tight lg:block">
            <div className="text-[13px] font-semibold">{roleLabel}</div>
            <div className="text-[11px] text-muted-foreground">{user.tenant_id}</div>
          </div>
        </div>
      </div>
    </header>
  )
}

export function AppLayout({ children }: { children: ReactNode }) {
  return (
    <SidebarProvider>
      <div className="flex min-h-screen bg-background">
        <AppSidebar />

        {/* Main column — flex-1 so it rescales as the sidebar width animates */}
        <div className="flex flex-1 flex-col min-w-0">
          <Header />
          <main className="w-full flex-1 px-3 pb-12 pt-4 sm:px-8 sm:pt-[26px]">
            {children}
          </main>
        </div>
      </div>

      <Toaster />
    </SidebarProvider>
  )
}

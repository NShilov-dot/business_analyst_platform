import { type ReactNode } from 'react'
import { AtSign, LogOut, Mail } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useAuth } from '../auth/AuthProvider'
import { displayName, roleLabels, userInitials } from '@/lib/user'

function ContactRow({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Mail
  label: string
  value: string | null
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex h-9 w-9 flex-none items-center justify-center rounded-[11px] border border-border bg-muted/50">
        <Icon className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="truncate text-sm font-medium text-foreground">
          {value ?? <span className="font-normal text-muted-foreground">не указано</span>}
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export default function ProfilePage() {
  const { user, logout } = useAuth()
  const labels = roleLabels(user.roles)

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      {/* Identity */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 pt-6 sm:flex-nowrap">
          <div className="flex h-16 w-16 flex-none items-center justify-center rounded-full bg-foreground text-xl font-bold text-primary">
            {userInitials(user)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xl font-bold tracking-tight text-foreground">
              {displayName(user)}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {labels.length > 0 ? (
                labels.map((label) => (
                  <Badge key={label} variant="secondary">
                    {label}
                  </Badge>
                ))
              ) : (
                <span className="text-sm text-muted-foreground">Роли не назначены</span>
              )}
            </div>
          </div>
          <Button variant="outline" onClick={logout} className="w-full sm:ml-auto sm:w-auto">
            <LogOut className="h-4 w-4" /> Выйти
          </Button>
        </CardContent>
      </Card>

      {/* Contacts */}
      <Section title="Контактные данные">
        <div className="grid gap-4 sm:grid-cols-2">
          <ContactRow icon={Mail} label="Email" value={user.email} />
          <ContactRow icon={AtSign} label="Логин" value={user.username} />
        </div>
      </Section>
    </div>
  )
}

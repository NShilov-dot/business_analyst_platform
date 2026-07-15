import { type ReactNode } from 'react'
import { LogOut } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useAuth } from '../auth/AuthProvider'

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] items-start gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  )
}

export default function ProfilePage() {
  const { user, logout } = useAuth()

  return (
    <div className="max-w-lg space-y-6">
      <h1 className="text-2xl font-semibold text-foreground">Profile</h1>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Account</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="space-y-4 text-sm">
            <Field label="User ID">
              <span className="font-mono break-all text-foreground">{user.subject}</span>
            </Field>
            <Field label="Tenant">
              <span className="font-mono break-all text-foreground">{user.tenant_id}</span>
            </Field>
            <Field label="Roles">
              {user.roles.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {user.roles.map((role) => (
                    <Badge key={role} variant="secondary">
                      {role}
                    </Badge>
                  ))}
                </div>
              ) : (
                <span className="text-muted-foreground">No roles assigned</span>
              )}
            </Field>
          </dl>
        </CardContent>
      </Card>

      <Button variant="outline" onClick={logout}>
        <LogOut className="h-4 w-4" /> Log out
      </Button>
    </div>
  )
}

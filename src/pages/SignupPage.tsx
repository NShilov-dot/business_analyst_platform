import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Building2, CheckCircle2 } from 'lucide-react'
import { signupApi, SignupInput, SignupResult } from '../api/signup'
import { getErrorMessage } from '../lib/errors'
import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form'
import { Input } from '@/components/ui/input'

// Mirrors the backend SignupRequest (app/modules/tenants/interface/schemas.py):
// slug pattern + 12-char password floor are enforced server-side too.
const schema = z
  .object({
    company_name: z.string().trim().min(1, 'Company name is required').max(200),
    slug: z
      .string()
      .trim()
      .regex(
        /^[a-z][a-z0-9_]{1,40}$/,
        'Start with a letter; lowercase letters, digits and underscores only (2–41 chars).',
      ),
    admin_email: z.string().trim().email('Enter a valid email address'),
    admin_password: z
      .string()
      .min(12, 'Use at least 12 characters')
      .max(128, 'Password is too long'),
    confirm_password: z.string(),
  })
  .refine((v) => v.admin_password === v.confirm_password, {
    message: 'Passwords do not match',
    path: ['confirm_password'],
  })

type FormValues = z.infer<typeof schema>

export default function SignupPage() {
  const [created, setCreated] = useState<SignupResult | null>(null)

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      company_name: '',
      slug: '',
      admin_email: '',
      admin_password: '',
      confirm_password: '',
    },
  })

  const { mutate, isPending, error } = useMutation({
    mutationFn: (input: SignupInput) => signupApi.register(input),
    onSuccess: (res) => setCreated(res),
  })

  function onSubmit(values: FormValues) {
    mutate({
      company_name: values.company_name,
      slug: values.slug,
      admin_email: values.admin_email,
      admin_password: values.admin_password,
    })
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-10">
      <Card className="w-full max-w-md">
        {created ? (
          <>
            <CardHeader className="text-center">
              <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                <CheckCircle2 className="h-6 w-6 text-primary" />
              </div>
              <CardTitle>Company created</CardTitle>
              <CardDescription>
                <span className="font-medium text-foreground">{created.slug}</span> is ready.
                Sign in as the administrator to start inviting your team.
              </CardDescription>
            </CardHeader>
            <CardFooter>
              {/* Full navigation — the OIDC login lives on the backend origin. */}
              <Button className="w-full" onClick={() => window.location.assign(created.login_url)}>
                Continue to sign in
              </Button>
            </CardFooter>
          </>
        ) : (
          <>
            <CardHeader className="text-center">
              <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                <Building2 className="h-6 w-6 text-primary" />
              </div>
              <CardTitle>Register your company</CardTitle>
              <CardDescription>
                Create your organisation and its first administrator account.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Form {...form}>
                <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
                  <FormField
                    control={form.control}
                    name="company_name"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Company name</FormLabel>
                        <FormControl>
                          <Input placeholder="ACME Corp" autoFocus {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="slug"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Workspace identifier</FormLabel>
                        <FormControl>
                          <Input placeholder="acme" autoComplete="off" {...field} />
                        </FormControl>
                        <FormDescription>
                          Lowercase, no spaces — used internally to isolate your data.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="admin_email"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Administrator email</FormLabel>
                        <FormControl>
                          <Input type="email" placeholder="you@company.com" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="admin_password"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Password</FormLabel>
                        <FormControl>
                          <Input type="password" autoComplete="new-password" {...field} />
                        </FormControl>
                        <FormDescription>At least 12 characters.</FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="confirm_password"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Confirm password</FormLabel>
                        <FormControl>
                          <Input type="password" autoComplete="new-password" {...field} />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {error && (
                    <p className="text-sm text-red-500" role="alert">
                      {getErrorMessage(error)}
                    </p>
                  )}

                  <Button type="submit" className="w-full" disabled={isPending}>
                    {isPending ? 'Creating…' : 'Create company'}
                  </Button>
                </form>
              </Form>
            </CardContent>
            <CardFooter className="justify-center">
              <p className="text-sm text-muted-foreground">
                Already have an account?{' '}
                <a
                  href="/v1/auth/login?return_to=/"
                  className="font-medium text-primary hover:underline"
                >
                  Sign in
                </a>
              </p>
            </CardFooter>
          </>
        )}
      </Card>
    </div>
  )
}

export type TenantTheme = Partial<
  Record<'primary' | 'primaryForeground' | 'ring' | 'radius', string>
>

const CSS_VAR: Record<keyof TenantTheme, string> = {
  primary: '--primary',
  primaryForeground: '--primary-foreground',
  ring: '--ring',
  radius: '--radius',
}

export function applyTenantTheme(theme: TenantTheme): void {
  const root = document.documentElement
  for (const [key, value] of Object.entries(theme)) {
    if (value) root.style.setProperty(CSS_VAR[key as keyof TenantTheme], value)
  }
}

export function clearTenantTheme(): void {
  const root = document.documentElement
  for (const cssVar of Object.values(CSS_VAR)) {
    root.style.removeProperty(cssVar)
  }
}

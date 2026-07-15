// Per-tenant branding: override the design tokens at runtime by setting CSS
// custom properties on <html>. Values are raw HSL triples in the same format as
// index.css (e.g. "221.2 83.2% 53.3%"); --radius takes a length (e.g. "0.5rem").
//
// CAVEAT: these are written as INLINE styles on <html>, so per the CSS cascade
// they override BOTH the :root and the .dark token values — a single override
// applies to light AND dark mode and flattens the dark-tuned --primary /
// --primary-foreground / --ring. Pass a colour that reads acceptably on both the
// near-white (light) and near-black (dark) surfaces, or extend this to emit
// per-scheme values (e.g. a data-tenant attribute with :root[...] / .dark[...]
// rules in index.css) before relying on it for dark-mode tenants.
//
// Intended use: call applyTenantTheme(...) once tenant branding is available
// (e.g. after /auth/me). Keys left unset fall back to the defaults in index.css.
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

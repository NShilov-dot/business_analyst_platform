// Pre-paint theme: apply the persisted/system colour scheme before the app
// bundle mounts, so dark-mode users don't see a light flash (next-themes only
// runs after its JS loads). Served from origin as a plain blocking script to
// satisfy the strict CSP (script-src 'self' — inline scripts are blocked).
// Mirrors next-themes' default storage key ('theme').
(function () {
  try {
    var stored = localStorage.getItem('theme')
    var dark =
      stored === 'dark' ||
      ((!stored || stored === 'system') &&
        window.matchMedia('(prefers-color-scheme: dark)').matches)
    var root = document.documentElement
    root.classList.toggle('dark', dark)
    root.style.colorScheme = dark ? 'dark' : 'light'
  } catch (e) {
    /* localStorage/matchMedia unavailable — fall back to light. */
  }
})()

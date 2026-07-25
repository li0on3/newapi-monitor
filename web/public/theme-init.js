(() => {
  try {
    const saved = localStorage.getItem('newapi-monitor-theme')
    const preference = saved === 'light' || saved === 'dark' ? saved : 'system'
    const theme = preference === 'system'
      ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : preference
    const root = document.documentElement

    root.classList.add(theme)
    root.dataset.theme = theme
    root.style.colorScheme = theme
    document.querySelector('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'dark' ? '#252525' : '#ffffff')
  } catch {
    // The CSS light theme remains the safe fallback when browser storage is unavailable.
  }
})()

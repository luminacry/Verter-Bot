// Applies the saved theme before React mounts so the page never flashes the wrong theme.
// Kept as an external file because the CSP forbids inline scripts.
;(function () {
  try {
    var saved = localStorage.getItem("theme")
    var dark =
      saved === "dark" ||
      (saved !== "light" &&
        window.matchMedia("(prefers-color-scheme: dark)").matches)
    document.documentElement.classList.add(dark ? "dark" : "light")
    document.documentElement.style.colorScheme = dark ? "dark" : "light"
  } catch {
    // Storage can be unavailable (privacy mode); the theme provider takes over after mount.
  }
})()

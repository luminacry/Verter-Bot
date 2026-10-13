// Fails when the JavaScript needed for any first screen exceeds the budget in
// docs/DEV_PLAN.md (8.2). A screen is the entry chunk plus every lazily loaded
// route chunk on its route chain, each with its static imports.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { gzipSync } from "node:zlib"

const BUDGET_KB = 250

// Nested routes load their lazy chunks together; list each chain here.
const SCREENS = {
  login: ["src/features/auth/login-page.tsx"],
  overview: [
    "src/components/app-shell.tsx",
    "src/features/overview/overview-page.tsx",
  ],
  plugins: [
    "src/components/app-shell.tsx",
    "src/features/plugins/plugins-page.tsx",
  ],
  sandbox: [
    "src/components/app-shell.tsx",
    "src/features/sandbox/sandbox-page.tsx",
  ],
}

const dist = join(import.meta.dirname, "..", "dist")
const manifest = JSON.parse(
  readFileSync(join(dist, ".vite", "manifest.json"), "utf8")
)

function closure(keys, seen = new Set()) {
  for (const key of keys) {
    if (seen.has(key)) continue
    if (!manifest[key])
      throw new Error(`${key} is not a chunk in the Vite manifest`)
    seen.add(key)
    closure(manifest[key].imports ?? [], seen)
  }
  return seen
}

function gzipKb(keys) {
  let total = 0
  for (const key of keys) {
    const file = manifest[key].file
    if (file.endsWith(".js")) {
      total +=
        gzipSync(readFileSync(join(dist, file)), { level: 9 }).length / 1024
    }
  }
  return total
}

const entryKey = Object.keys(manifest).find((key) => manifest[key].isEntry)
const listed = new Set(Object.values(SCREENS).flat())
const screens = { ...SCREENS }
for (const key of Object.keys(manifest)) {
  if (manifest[key].isDynamicEntry && !listed.has(key)) screens[key] = [key]
}

let failed = false
for (const [name, chain] of Object.entries(screens)) {
  const kb = gzipKb(closure([entryKey, ...chain]))
  const over = kb > BUDGET_KB
  failed ||= over
  console.log(
    `${kb.toFixed(1).padStart(8)} KB  ${name}${over ? "  OVER BUDGET" : ""}`
  )
}
console.log(`Budget: ${BUDGET_KB} KB gzip of JavaScript per first screen`)
if (failed) process.exit(1)

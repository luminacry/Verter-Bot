import { MonitorIcon, MoonIcon, SunIcon } from "lucide-react"
import { useTheme } from "next-themes"
import { useTranslation } from "react-i18next"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

const THEMES = ["light", "dark", "system"] as const
type Theme = (typeof THEMES)[number]

function isTheme(value: unknown): value is Theme {
  return THEMES.includes(value as Theme)
}

export function ThemeToggle() {
  const { t } = useTranslation()
  const { theme, setTheme } = useTheme()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            variant="ghost"
            size="icon"
            className="relative"
            aria-label={t("preferences.theme")}
          />
        }
      >
        <SunIcon className="scale-100 rotate-0 transition-transform dark:scale-0 dark:-rotate-90" />
        <MoonIcon className="absolute scale-0 rotate-90 transition-transform dark:scale-100 dark:rotate-0" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={isTheme(theme) ? theme : "system"}
          onValueChange={(value: unknown) => {
            if (isTheme(value)) {
              setTheme(value)
            }
          }}
        >
          <DropdownMenuRadioItem closeOnClick value="light">
            <SunIcon />
            {t("preferences.light")}
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem closeOnClick value="dark">
            <MoonIcon />
            {t("preferences.dark")}
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem closeOnClick value="system">
            <MonitorIcon />
            {t("preferences.system")}
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

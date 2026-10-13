import i18n from "i18next"
import { initReactI18next } from "react-i18next"

import { en } from "./locales/en"
import { zhCN } from "./locales/zh-CN"

export const LANGUAGES = ["zh-CN", "en"] as const
export type Language = (typeof LANGUAGES)[number]

const STORAGE_KEY = "verter-language"

export function isLanguage(
  value: string | null | undefined
): value is Language {
  return value === "zh-CN" || value === "en"
}

export function detectLanguage(
  stored: string | null,
  preferred: readonly string[]
): Language {
  if (isLanguage(stored)) {
    return stored
  }
  return preferred.some((lang) => lang.toLowerCase().startsWith("zh"))
    ? "zh-CN"
    : "en"
}

void i18n.use(initReactI18next).init({
  resources: {
    "zh-CN": { translation: zhCN },
    en: { translation: en },
  },
  lng: detectLanguage(localStorage.getItem(STORAGE_KEY), navigator.languages),
  fallbackLng: "zh-CN",
  interpolation: { escapeValue: false },
})

document.documentElement.lang = i18n.language
i18n.on("languageChanged", (lng) => {
  document.documentElement.lang = lng
  localStorage.setItem(STORAGE_KEY, lng)
})

export default i18n

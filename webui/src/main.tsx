import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RouterProvider } from "@tanstack/react-router"
import { ThemeProvider } from "next-themes"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { toast } from "sonner"

import "@/i18n"
import "./index.css"

import { Toaster } from "@/components/ui/sonner"
import { sessionQuery } from "@/features/auth/session"
import i18n from "@/i18n"
import { onUnauthorized } from "@/lib/api/client"
import { createAppRouter } from "@/router"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 10_000, refetchOnWindowFocus: false },
  },
})
const router = createAppRouter(queryClient)

onUnauthorized(() => {
  if (router.state.location.pathname === "/login") {
    return
  }
  queryClient.removeQueries({ queryKey: sessionQuery.queryKey })
  toast.error(i18n.t("auth.sessionExpired"), { id: "session-expired" })
  void router.navigate({
    to: "/login",
    search: { redirect: router.state.location.href },
  })
})

const container = document.getElementById("root")
if (!container) {
  throw new Error("Missing #root element")
}

createRoot(container).render(
  <StrictMode>
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
        <Toaster position="top-center" />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>
)

import type { QueryClient } from "@tanstack/react-query"
import {
  createRootRouteWithContext,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  redirect,
} from "@tanstack/react-router"
import type { ParseKeys } from "i18next"

import { sessionQuery } from "@/features/auth/session"

interface RouterContext {
  queryClient: QueryClient
}

interface LoginSearch {
  redirect?: string
}

// Only same-origin paths, so a crafted link cannot redirect off-site after login.
function parseLoginSearch(search: Record<string, unknown>): LoginSearch {
  const { redirect } = search
  return typeof redirect === "string" && /^\/(?!\/)/.test(redirect)
    ? { redirect }
    : {}
}

const rootRoute = createRootRouteWithContext<RouterContext>()({
  component: Outlet,
  notFoundComponent: lazyRouteComponent(
    () => import("@/features/not-found/not-found-page"),
    "NotFoundPage"
  ),
})

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  validateSearch: parseLoginSearch,
  beforeLoad: async ({ context }) => {
    const session = await context.queryClient
      .query(sessionQuery)
      .catch(() => null)
    if (session) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router redirects are thrown
      throw redirect({ to: "/" })
    }
  },
  component: lazyRouteComponent(
    () => import("@/features/auth/login-page"),
    "LoginPage"
  ),
})

const appRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "app",
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.query(sessionQuery)
    } catch {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- TanStack Router redirects are thrown
      throw redirect({ to: "/login", search: { redirect: location.href } })
    }
  },
  component: lazyRouteComponent(
    () => import("@/components/app-shell"),
    "AppShell"
  ),
})

const overviewRoute = createRoute({
  getParentRoute: () => appRoute,
  path: "/",
  staticData: { titleKey: "nav.overview" },
  component: lazyRouteComponent(
    () => import("@/features/overview/overview-page"),
    "OverviewPage"
  ),
})

const routeTree = rootRoute.addChildren([
  loginRoute,
  appRoute.addChildren([overviewRoute]),
])

export function createAppRouter(queryClient: QueryClient) {
  return createRouter({
    routeTree,
    context: { queryClient },
    defaultPreload: "intent",
    scrollRestoration: true,
  })
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>
  }
  interface StaticDataRouteOption {
    titleKey?: ParseKeys
  }
}

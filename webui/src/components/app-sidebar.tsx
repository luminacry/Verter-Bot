import { Link } from "@tanstack/react-router"
import { LayoutDashboardIcon, PuzzleIcon, ShieldIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

import { BrandMark } from "@/components/brand-mark"
import { NavMain, type NavItem } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar"

const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", titleKey: "nav.overview", icon: LayoutDashboardIcon },
  { to: "/plugins", titleKey: "nav.plugins", icon: PuzzleIcon },
  { to: "/sandbox", titleKey: "nav.sandbox", icon: ShieldIcon },
]

export function AppSidebar(props: React.ComponentProps<typeof Sidebar>) {
  const { t } = useTranslation()

  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/" />}>
              <BrandMark className="bg-sidebar-primary text-sidebar-primary-foreground" />
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-medium">{t("app.name")}</span>
                <span className="truncate text-xs">{t("app.console")}</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain items={NAV_ITEMS} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}

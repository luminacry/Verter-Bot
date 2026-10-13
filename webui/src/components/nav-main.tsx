import { Link, useMatchRoute, type LinkProps } from "@tanstack/react-router"
import type { ParseKeys } from "i18next"
import type { LucideIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

export interface NavItem {
  to: NonNullable<LinkProps["to"]>
  titleKey: ParseKeys
  icon: LucideIcon
}

export function NavMain({ items }: { items: readonly NavItem[] }) {
  const { t } = useTranslation()
  const matchRoute = useMatchRoute()

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{t("nav.section")}</SidebarGroupLabel>
      <SidebarMenu>
        {items.map((item) => {
          const title = t(item.titleKey)
          return (
            <SidebarMenuItem key={item.to}>
              <SidebarMenuButton
                render={<Link to={item.to} />}
                isActive={matchRoute({ to: item.to }) !== false}
                tooltip={title}
              >
                <item.icon />
                <span>{title}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          )
        })}
      </SidebarMenu>
    </SidebarGroup>
  )
}

import { useQuery } from "@tanstack/react-query"
import { ChevronsUpDownIcon, LogOutIcon } from "lucide-react"
import { useTranslation } from "react-i18next"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { sessionQuery, useLogout } from "@/features/auth/session"

function UserIdentity({ username }: { username: string }) {
  const { t } = useTranslation()
  return (
    <>
      <Avatar>
        <AvatarFallback>{username.slice(0, 2).toUpperCase()}</AvatarFallback>
      </Avatar>
      <div className="grid flex-1 text-left text-sm leading-tight">
        <span className="truncate font-medium">{username}</span>
        <span className="truncate text-xs text-muted-foreground">
          {t("auth.administrator")}
        </span>
      </div>
    </>
  )
}

export function NavUser() {
  const { t } = useTranslation()
  const { isMobile } = useSidebar()
  const { data: session } = useQuery(sessionQuery)
  const logout = useLogout()
  const username = session?.username ?? ""

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton size="lg" className="aria-expanded:bg-muted" />
            }
          >
            <UserIdentity username={username} />
            <ChevronsUpDownIcon className="ml-auto size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="min-w-56"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            <DropdownMenuGroup>
              <DropdownMenuLabel className="p-0 font-normal">
                <div className="flex items-center gap-2 px-1 py-1.5">
                  <UserIdentity username={username} />
                </div>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              disabled={logout.isPending}
              onClick={() => {
                logout.mutate()
              }}
            >
              <LogOutIcon />
              {t("auth.logout")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}

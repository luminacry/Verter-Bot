import { cn } from "cn"
import { BotIcon } from "lucide-react"

export function BrandMark({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground",
        className
      )}
    >
      <BotIcon className="size-4" />
    </div>
  )
}

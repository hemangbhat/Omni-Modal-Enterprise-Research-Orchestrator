import { MaterialIcon } from "@/components/material-icon";

type TopBarProps = {
  searchPlaceholder?: string;
};

/**
 * Desktop top app bar used across content pages (search + utility icons +
 * profile avatar), matching the OMERO reference screens.
 */
export function TopBar({ searchPlaceholder = "Search workspace..." }: TopBarProps) {
  return (
    <header className="sticky top-0 z-40 flex h-16 w-full items-center justify-between border-b border-outline-variant/20 bg-surface/80 px-lg backdrop-blur-md md:px-xl">
      <div className="relative w-full max-w-md">
        <MaterialIcon
          name="search"
          size={18}
          className="absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
        />
        <input
          type="text"
          placeholder={searchPlaceholder}
          className="w-full rounded-full border border-outline-variant/30 bg-surface-container py-1.5 pl-10 pr-4 font-body-md text-body-md text-on-surface transition-all placeholder:text-on-surface-variant/50 focus:border-primary-fixed-dim/50 focus:outline-none focus:ring-1 focus:ring-primary-fixed-dim/50"
        />
      </div>
      <div className="flex items-center gap-sm">
        <button
          type="button"
          className="flex h-9 w-9 items-center justify-center rounded-full text-on-surface-variant transition-all hover:bg-surface-bright hover:text-primary-fixed-dim"
          aria-label="Notifications"
        >
          <MaterialIcon name="notifications" />
        </button>
        <button
          type="button"
          className="flex h-9 w-9 items-center justify-center rounded-full text-on-surface-variant transition-all hover:bg-surface-bright hover:text-primary-fixed-dim"
          aria-label="Live status"
        >
          <MaterialIcon name="sensors" />
        </button>
        <div className="ml-sm h-8 w-8 overflow-hidden rounded-full border border-outline-variant/50 bg-gradient-to-tr from-surface-variant to-outline">
          <span className="flex h-full w-full items-center justify-center font-mono-sm text-mono-sm text-on-surface">
            U
          </span>
        </div>
      </div>
    </header>
  );
}

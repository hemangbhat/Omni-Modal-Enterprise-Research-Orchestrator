/**
 * Active-workspace helper.
 *
 * The workspace switcher (top bar) persists the selected workspace id in
 * localStorage under this key. Upload and list pages read it to scope their
 * requests so workspaces behave as real containers, not just labels.
 */

export const ACTIVE_WORKSPACE_KEY = "omni_active_workspace";

export function getActiveWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACTIVE_WORKSPACE_KEY);
}

export function setActiveWorkspaceId(id: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACTIVE_WORKSPACE_KEY, id);
}

/** Append ?workspace_id= to a path when an active workspace is selected. */
export function withWorkspaceQuery(path: string): string {
  const id = getActiveWorkspaceId();
  if (!id) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}workspace_id=${encodeURIComponent(id)}`;
}

import { LIVE_ROLES } from "@/lib/live/roles";
import type { LiveProfile } from "@/lib/live/session";
import { ROLES } from "@/lib/seed/roles";
import type { Role, RoleId } from "@/lib/types";

/**
 * The one place the effective role is derived, used by both the UI (`useRole`) and the action
 * guards, so what a screen enables is exactly what its action allows.
 * With a live session the backend role decides: it picks the closest seeded role for wording and
 * mock-flow flags, and backend-enforced permissions override those flags.
 */
export function resolveRole(seededRole: RoleId, profile: LiveProfile | null): Role {
  if (!profile) return ROLES[seededRole];
  const live = LIVE_ROLES[profile.role];
  return {
    ...ROLES[live.uiRole],
    name: profile.display_name ?? profile.email ?? "Signed in",
    label: live.label,
    canUpload: live.canUpload,
    canAccessCases: live.canInvestigate,
  };
}

"use client";

import { useMemo } from "react";
import { buildEvidence } from "@/lib/domain/evidence";
import { scoreAlerts } from "@/lib/domain/priority";
import { LIVE_ROLES } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROLES } from "@/lib/seed/roles";
import type { Role } from "@/lib/types";
import { useDemoState } from "./store";

export { useDemoState, useHydrated } from "./store";

export function useRole(): Role {
  const state = useDemoState();
  const profile = useLiveProfile();
  return useMemo(() => {
    if (!profile) return ROLES[state.role];
    // Live session: the backend role decides; the seeded role only supplies the UI's permission flags.
    const live = LIVE_ROLES[profile.role];
    // Backend permissions win wherever the backend enforces them (an admin maps to "officer" but can upload).
    return {
      ...ROLES[live.uiRole],
      name: profile.display_name ?? profile.email ?? "Signed in",
      label: live.label,
      canUpload: live.canUpload,
      canAccessCases: live.canInvestigate,
    };
  }, [profile, state.role]);
}

export function useScoredAlerts() {
  const state = useDemoState();
  return useMemo(() => scoreAlerts(state), [state]);
}

export function useEvidenceRegistry() {
  const fixed = useDemoState().data.driftFixed;
  return useMemo(() => buildEvidence(fixed), [fixed]);
}

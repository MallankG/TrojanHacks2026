"use client";

import { useMemo } from "react";
import { buildEvidence } from "@/lib/domain/evidence";
import { scoreAlerts } from "@/lib/domain/priority";
import { useLiveProfile } from "@/lib/live/session";
import type { Role } from "@/lib/types";
import { resolveRole } from "./role";
import { useDemoState } from "./store";

export { useDemoState, useHydrated } from "./store";

export function useRole(): Role {
  const state = useDemoState();
  const profile = useLiveProfile();
  return useMemo(() => resolveRole(state.role, profile), [profile, state.role]);
}

export function useScoredAlerts() {
  const state = useDemoState();
  return useMemo(() => scoreAlerts(state), [state]);
}

export function useEvidenceRegistry() {
  const fixed = useDemoState().data.driftFixed;
  return useMemo(() => buildEvidence(fixed), [fixed]);
}

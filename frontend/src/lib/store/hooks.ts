"use client";

import { useMemo } from "react";
import { buildEvidence } from "@/lib/domain/evidence";
import { scoreAlerts } from "@/lib/domain/priority";
import { ROLES } from "@/lib/seed/roles";
import type { Role } from "@/lib/types";
import { useDemoState } from "./store";

export { useDemoState, useHydrated } from "./store";

export function useRole(): Role {
  const state = useDemoState();
  return ROLES[state.role];
}

export function useScoredAlerts() {
  const state = useDemoState();
  return useMemo(() => scoreAlerts(state), [state]);
}

export function useEvidenceRegistry() {
  const fixed = useDemoState().data.driftFixed;
  return useMemo(() => buildEvidence(fixed), [fixed]);
}

import type { RoleId } from "@/lib/types";
import type { BackendRole } from "./session";

/**
 * The database roles are the source of truth. `uiRole` maps each one onto the UI's role vocabulary
 * (analyst / steward / officer / auditor) so the seeded screens keep their wording and guards.
 * The flags mirror the backend's checks so live panels hide actions the API would reject anyway;
 * the backend and RLS still enforce every rule.
 */
export interface LiveRole {
  label: string;
  uiRole: RoleId;
  canUpload: boolean;
  canSteward: boolean;
  canInvestigate: boolean;
  /** Accept, reject, escalate and record a disposition (four-eyes reviewer step). */
  canReview: boolean;
}

export const LIVE_ROLES: Record<BackendRole, LiveRole> = {
  admin: { label: "Administrator", uiRole: "officer", canUpload: true, canSteward: true, canInvestigate: true, canReview: true },
  reviewer: { label: "BSA officer (reviewer)", uiRole: "officer", canUpload: false, canSteward: false, canInvestigate: true, canReview: true },
  investigator: { label: "Investigator", uiRole: "analyst", canUpload: true, canSteward: false, canInvestigate: true, canReview: false },
  analyst: { label: "Fraud / BSA analyst", uiRole: "analyst", canUpload: true, canSteward: false, canInvestigate: true, canReview: false },
  data_steward: { label: "Data steward", uiRole: "steward", canUpload: true, canSteward: true, canInvestigate: false, canReview: false },
};

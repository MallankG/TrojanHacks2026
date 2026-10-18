import type { Role, RoleId } from "@/lib/types";

export const ROLES: Record<RoleId, Role> = {
  analyst: {
    id: "analyst",
    name: "M. Rivera",
    label: "Fraud / BSA analyst",
    short: "Analyst",
    canUpload: true,
    canFixData: true,
    canAccessCases: true,
    canDecide: true,
    canApproveEscalation: false,
    canEditPolicy: false,
    canApproveRepair: false,
    readOnly: false,
  },
  steward: {
    id: "steward",
    name: "P. Nair",
    label: "Data steward",
    short: "Steward",
    canUpload: true,
    canFixData: true,
    canAccessCases: false,
    canDecide: false,
    canApproveEscalation: false,
    canEditPolicy: false,
    canApproveRepair: true,
    readOnly: false,
  },
  officer: {
    id: "officer",
    name: "J. Okafor",
    label: "BSA officer",
    short: "Officer",
    canUpload: false,
    canFixData: false,
    canAccessCases: true,
    canDecide: true,
    canApproveEscalation: true,
    canEditPolicy: true,
    canApproveRepair: true,
    readOnly: false,
  },
  auditor: {
    id: "auditor",
    name: "A. Lee",
    label: "Auditor (read-only)",
    short: "Auditor",
    canUpload: false,
    canFixData: false,
    canAccessCases: true,
    canDecide: false,
    canApproveEscalation: false,
    canEditPolicy: false,
    canApproveRepair: false,
    readOnly: true,
  },
};

export const ROLE_ORDER: RoleId[] = ["analyst", "steward", "officer", "auditor"];

export function roleInitials(role: Role): string {
  return role.name.replace(/[^A-Za-z]/g, "").slice(0, 2).toUpperCase();
}

export function deniedMessage(role: Role): string {
  return `Your role (${role.label}) cannot do this.`;
}

export const PERMISSION_MATRIX: Array<{ action: string; values: [string, string, string, string] }> = [
  { action: "Upload and fix data", values: ["Yes", "Yes", "No", "No"] },
  { action: "Approve data repairs", values: ["No", "Yes", "Yes", "No"] },
  { action: "Open cases and use copilot", values: ["Yes", "No", "Yes", "Read only"] },
  { action: "Record a disposition", values: ["Yes", "No", "Yes", "No"] },
  { action: "Approve escalations", values: ["No", "No", "Yes", "No"] },
  { action: "Change policy, weights, AI scope", values: ["No", "No", "Yes", "No"] },
  { action: "Read lineage and audit", values: ["Yes", "Yes", "Yes", "Yes"] },
];

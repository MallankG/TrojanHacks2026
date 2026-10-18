import type { Metadata } from "next";
import { AuditTrail } from "@/features/governance/audit-trail";

export const metadata: Metadata = { title: "Access and audit · Governance" };

export default function GovernanceAuditPage() {
  return <AuditTrail />;
}

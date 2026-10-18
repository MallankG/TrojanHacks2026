import type { Metadata } from "next";
import { EnterpriseWorkspace } from "@/features/governance/enterprise-workspace";
export const metadata: Metadata = { title: "Enterprise investigation workspace" };
export default function EnterprisePage() { return <EnterpriseWorkspace />; }

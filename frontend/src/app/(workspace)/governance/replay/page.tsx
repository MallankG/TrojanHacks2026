import type { Metadata } from "next";
import { ReplayView } from "@/features/governance/replay-view";

export const metadata: Metadata = { title: "Investigation replay · Governance" };

export default function GovernanceReplayPage() {
  return <ReplayView />;
}

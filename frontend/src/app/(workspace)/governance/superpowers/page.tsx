import type { Metadata } from "next";
import { GovernanceSuperpowers } from "@/features/governance/superpowers";

export const metadata: Metadata = { title: "Data governance superpowers" };
export default function GovernanceSuperpowersPage() {
  return <GovernanceSuperpowers />;
}

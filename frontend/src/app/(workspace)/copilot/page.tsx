import type { Metadata } from "next";
import { CaseAccessGate } from "@/components/shell/access-restricted";
import { CopilotWorkspace } from "@/features/copilot/copilot-workspace";

export const metadata: Metadata = { title: "AI copilot" };

export default function CopilotPage() {
  return (
    <CaseAccessGate>
      <CopilotWorkspace />
    </CaseAccessGate>
  );
}

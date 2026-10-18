"use client";

import { useDemoState } from "@/lib/store/hooks";
import { LiveUploadDialog } from "@/features/live/live-data";
import { ConfirmCaseDialog } from "./confirm-case-dialog";
import { EditClaimDialog } from "./edit-claim-dialog";
import { EvidenceDialog } from "./evidence-dialog";
import { HelpDialog, KillSwitchDialog, SessionTimeoutDialog } from "./simple-dialogs";
import { MappingFixDialog } from "./mapping-fix-dialog";
import { OnboardingWizard } from "./onboarding-wizard";
import { SwitchCaseDialog } from "./switch-case-dialog";

/**
 * Renders the active dialog. Dialogs live in the shell (not in pages) so they are
 * never left open on a page that Next.js keeps hidden after navigation.
 */
export function ModalHost() {
  const modal = useDemoState().modal;
  if (!modal) return null;
  switch (modal.kind) {
    case "onboard":
      return <OnboardingWizard />;
    case "liveUpload":
      return <LiveUploadDialog />;
    case "mappingFix":
      return <MappingFixDialog />;
    case "evidence":
      return <EvidenceDialog id={modal.id} />;
    case "confirm":
      return <ConfirmCaseDialog action={modal.action} />;
    case "killSwitch":
      return <KillSwitchDialog />;
    case "editClaim":
      return <EditClaimDialog />;
    case "switchCase":
      return <SwitchCaseDialog />;
    case "help":
      return <HelpDialog />;
    case "sessionTimeout":
      return <SessionTimeoutDialog />;
  }
}

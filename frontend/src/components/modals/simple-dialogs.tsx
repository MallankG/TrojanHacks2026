"use client";

import { useRouter } from "next/navigation";
import { ROUTES } from "@/lib/routes";
import { closeModal, confirmKillSwitch, signOut } from "@/lib/store/actions";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Dialog } from "@/components/ui/dialog";

export function HelpDialog() {
  return (
    <Dialog title="How TraceLayer works and talks" onClose={closeModal}>
      <div className="grid grid-cols-2 gap-3">
        <Callout tone="ok" className="leading-[1.7]">
          <b>We say</b>
          <br />
          Suspicious pattern
          <br />
          Risk signal
          <br />
          Review recommended
          <br />
          Evidence observed
        </Callout>
        <Callout tone="bad" className="leading-[1.7]">
          <b>We never say</b>
          <br />
          Criminal, guilty
          <br />
          Proven laundering
          <br />
          Auto-file, freeze
        </Callout>
      </div>
      <ul className="flex list-disc flex-col gap-1.5 pl-5 text-[13.5px]">
        <li>Status always has a word and a shape, never color alone.</li>
        <li>Every graph has a table view; every chart has a text label.</li>
        <li>Keyboard: Tab to move, Enter or Space to press, Esc closes dialogs.</li>
        <li>Demo path: Data operations, Risk intelligence, Graph workbench, Copilot, Cases, Governance.</li>
      </ul>
    </Dialog>
  );
}

export function KillSwitchDialog() {
  return (
    <Dialog
      title="Turn AI drafting off?"
      role="alertdialog"
      size="sm"
      hideClose
      onClose={closeModal}
      footer={
        <>
          <Button variant="ghost" onClick={closeModal}>
            Keep on
          </Button>
          <Button variant="dark" onClick={confirmKillSwitch}>
            Turn off
          </Button>
        </>
      }
    >
      <p className="text-[13.5px] text-muted">Drafting stops on every case. Investigators keep working from verified facts and manual edits. The change is logged and can be reversed.</p>
    </Dialog>
  );
}

export function SessionTimeoutDialog() {
  const router = useRouter();
  const signInAgain = () => {
    signOut();
    router.push(ROUTES.signIn);
  };
  return (
    <Dialog
      title="You were signed out"
      role="alertdialog"
      size="sm"
      hideClose
      onClose={signInAgain}
      footer={
        <>
          <span />
          <Button onClick={signInAgain}>Sign in again</Button>
        </>
      }
    >
      <p className="text-[13.5px] text-muted">Your session ended after 15 minutes of inactivity. Drafts and filters are kept on this device. Sign in to continue.</p>
    </Dialog>
  );
}

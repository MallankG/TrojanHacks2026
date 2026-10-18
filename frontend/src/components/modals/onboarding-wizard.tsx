"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { simulatedHash } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { DATA_OWNERS } from "@/lib/seed/data-ops";
import {
  mappingFor,
  ONBOARD_FILES,
  ONBOARD_STEPS,
  ONBOARD_TITLES,
  previewRows,
  profileFor,
  summaryFor,
  validationFor,
  type OnboardFileKey,
  type TargetMapping,
} from "@/lib/seed/onboarding";
import { deniedMessage } from "@/lib/seed/roles";
import { closeModal, commitBatch } from "@/lib/store/actions";
import { useRole } from "@/lib/store/hooks";
import type { Batch } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { OptionCard } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/dialog";
import { Checkbox, Field, Select } from "@/components/ui/field";
import { Icon } from "@/components/ui/icon";
import { KeyValueList } from "@/components/ui/key-value";
import { Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th } from "@/components/ui/table";
import { TONE_CLASSES } from "@/components/ui/tone";
import { CheckRow } from "@/features/data/check-row";

const CHECK_DURATION_MS = 1200;

type Step = 1 | 2 | 3 | 4 | 5;

export function OnboardingWizard() {
  const router = useRouter();
  const role = useRole();
  const [step, setStep] = useState<Step>(1);
  const [file, setFile] = useState<OnboardFileKey | null>(null);
  const [mapping, setMapping] = useState<TargetMapping>("");
  const [checks, setChecks] = useState<"idle" | "running" | "done">("idle");
  const [owner, setOwner] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);

  const selected = ONBOARD_FILES.find((f) => f.key === file);
  const needsChoice = file === "ach7";
  const summary = file ? summaryFor(file, mapping) : null;
  const quarantine = Boolean(summary?.quarantine);

  const runChecks = () => {
    setChecks("running");
    timer.current = setTimeout(() => setChecks("done"), CHECK_DURATION_MS);
  };

  const requestClose = () => {
    if (file || step > 1) setDiscarding(true);
    else closeModal();
  };

  const goBack = () => {
    if (step === 1) {
      requestClose();
      return;
    }
    setConfirmed(false);
    setStep((s) => (s - 1) as Step);
  };

  const nextDisabled =
    !role.canUpload ||
    (step === 1 && (!selected || Boolean(selected.error))) ||
    (step === 3 && needsChoice && (mapping === "" || mapping === "memo")) ||
    (step === 4 && checks !== "done") ||
    (step === 5 && (!confirmed || (quarantine && owner === "")));

  const nextLabel = ["Profile file", "Map columns", "Validate", "Review and confirm", quarantine ? "Quarantine and notify owner" : "Commit normalized records"][step - 1];

  const goNext = () => {
    if (!file || !summary) return;
    if (step === 3) {
      setStep(4);
      runChecks();
      return;
    }
    if (step < 5) {
      setStep((s) => (s + 1) as Step);
      return;
    }
    const name = file === "seed" ? "seed_suite_20261017" : (selected?.name ?? "").replace(/\.csv$/, "");
    const rejected = summary.rejected.split(" ")[0];
    const batch: Batch = {
      name,
      rail: file === "ach7" ? "ACH" : file === "seed" ? "Mixed" : "Wire",
      rows: file === "ach7" ? "19,880" : file === "seed" ? "5,000" : "1,104",
      accepted: summary.accepted,
      rejected,
      status: quarantine ? "Quarantined" : rejected !== "0" ? "Partially accepted" : "Loaded",
      tone: quarantine ? "warn" : rejected !== "0" ? "info" : "ok",
      owner: quarantine ? owner : "Payments Ops · P. Nair",
      schema: file === "ach7" ? (quarantine ? "ach.v4 · mapping v4.0" : "ach.v4 · mapping v4.1") : file === "seed" ? "scenario.v1" : "wire.v3",
      hash: `${simulatedHash(name, 4)}…${simulatedHash(`${name}x`, 4)}`,
      checks: validationFor(file, mapping),
    };
    if (commitBatch(batch, quarantine, owner)) router.push(ROUTES.data);
  };

  if (discarding) {
    return (
      <Dialog
        key="discard"
        title="Discard this onboarding?"
        role="alertdialog"
        size="sm"
        hideClose
        onClose={() => setDiscarding(false)}
        footer={
          <>
            <Button variant="ghost" onClick={() => setDiscarding(false)}>
              Keep editing
            </Button>
            <Button variant="dark" onClick={closeModal}>
              Discard
            </Button>
          </>
        }
      >
        <p className="text-[13.5px] text-muted">Your file choice and column mapping will be lost. Nothing has been written yet.</p>
      </Dialog>
    );
  }

  return (
    <Dialog
      key="wizard"
      eyebrow="Source onboarding"
      title={ONBOARD_TITLES[step - 1]}
      size="lg"
      onClose={requestClose}
      footer={
        <>
          <Button variant="ghost" onClick={goBack}>
            {step === 1 ? "Cancel" : "Back"}
          </Button>
          <Button disabled={nextDisabled} onClick={goNext}>
            {nextLabel}
          </Button>
        </>
      }
    >
      <ol aria-label="Steps" className="flex flex-wrap gap-1.5">
        {ONBOARD_STEPS.map((label, i) => (
          <li key={label} aria-current={i + 1 === step ? "step" : undefined} className={cn("rounded-full px-2.5 py-0.5 text-xs font-semibold", TONE_CLASSES[i + 1 < step ? "ok" : i + 1 === step ? "dark" : "mute"])}>
            {i + 1} {label}
          </li>
        ))}
      </ol>

      {!role.canUpload ? <Callout tone="warn">{deniedMessage(role)}</Callout> : null}

      {step === 1 ? (
        <>
          <div className="rounded-[14px] border-2 border-dashed border-[#9fb0c6] bg-hover p-5 text-center">
            <Icon name="upload" size={36} className="mx-auto text-brand" />
            <p className="mt-1.5 font-semibold">Drop a CSV here, or pick a file below</p>
            <p className="text-xs text-muted">.csv only · up to 200 MB · UTF-8 · header row required · synthetic data only</p>
          </div>
          <div className="flex flex-col gap-2">
            {ONBOARD_FILES.map((f) => (
              <OptionCard
                key={f.key}
                pressed={file === f.key}
                onClick={() => {
                  setFile(f.key);
                  setMapping("");
                  setChecks("idle");
                  setOwner("");
                  setConfirmed(false);
                }}
              >
                <b>{f.name}</b>
                <span className="text-xs text-muted">{f.note}</span>
              </OptionCard>
            ))}
          </div>
          {selected?.error ? (
            <Callout tone="bad" role="alert" title="This file cannot be used.">
              {selected.error}
            </Callout>
          ) : null}
        </>
      ) : null}

      {step === 2 && file ? (
        <>
          <p className="text-[13.5px] text-muted">Profiled {selected?.name}: types, null rates, formats and values found in the first 20,000 rows.</p>
          <DataTable caption="Column profile">
            <thead>
              <tr>
                <Th>Column</Th>
                <Th>Type</Th>
                <Th>Nulls</Th>
                <Th>Example</Th>
                <Th>Finding</Th>
              </tr>
            </thead>
            <tbody>
              {profileFor(file).map((c) => (
                <tr key={c.column}>
                  <Td className="font-mono">{c.column}</Td>
                  <Td>{c.type}</Td>
                  <Td>{c.nulls}</Td>
                  <Td className="font-mono text-xs text-muted">{c.example}</Td>
                  <Td>{c.finding ? <Pill tone="warn">{c.finding}</Pill> : null}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        </>
      ) : null}

      {step === 3 && file ? (
        <>
          <DataTable caption="Column mapping">
            <thead>
              <tr>
                <Th>Contract field</Th>
                <Th>Your column</Th>
                <Th>Confidence</Th>
                <Th>Status</Th>
              </tr>
            </thead>
            <tbody>
              {mappingFor(file, mapping).map((m) => (
                <tr key={m.field}>
                  <Td className="font-mono">{m.field}</Td>
                  <Td className="font-mono">{m.column}</Td>
                  <Td>{m.confidence}</Td>
                  <Td>
                    <Pill tone={m.ok ? "ok" : "warn"}>{m.status}</Pill>
                  </Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
          {needsChoice ? (
            <>
              <Field label="Which column holds target_account? (low confidence, needs your choice)">
                {(props) => (
                  <Select {...props} value={mapping} onChange={(event) => setMapping(event.target.value as TargetMapping)}>
                    <option value="">Choose a column</option>
                    <option value="bene_acct_no">bene_acct_no (account tokens, new column)</option>
                    <option value="memo">memo (free text)</option>
                    <option value="skip">Leave unmapped</option>
                  </Select>
                )}
              </Field>
              {mapping === "memo" ? <Callout tone="warn">memo holds free text, not account tokens. Choose another column or leave it unmapped.</Callout> : null}
              {mapping === "skip" ? <Callout tone="warn">Leaving target_account unmapped fails a required check. The whole batch will be quarantined.</Callout> : null}
            </>
          ) : null}
          <div>
            <div className="mb-1.5 text-xs text-muted">Preview, 3 of 20 rows, normalized</div>
            <DataTable className="font-mono text-xs" caption="Normalized preview">
              <thead>
                <tr>
                  {["transaction_id", "timestamp_utc", "amount", "currency", "source", "target"].map((h) => (
                    <Th key={h}>{h}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {previewRows(file, mapping).map((row) => (
                  <tr key={row[0]}>
                    {row.map((cell, i) => (
                      <Td key={i}>{cell}</Td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </DataTable>
          </div>
        </>
      ) : null}

      {step === 4 && file ? (
        <>
          {checks === "running" ? (
            <div className="flex flex-col gap-2.5">
              <b className="text-[13.5px]">Running 10 contract checks…</b>
              <Skeleton label="Running contract checks" />
            </div>
          ) : null}
          {checks === "idle" ? <Button onClick={runChecks}>Run checks</Button> : null}
          {checks === "done" ? validationFor(file, mapping).map((c) => <CheckRow key={c.label} check={c} />) : null}
        </>
      ) : null}

      {step === 5 && summary ? (
        <>
          <KeyValueList
            items={[
              ["Accepted", summary.accepted],
              ["Rejected", quarantine ? `${summary.rejected} (quarantined)` : summary.rejected],
              ...(summary.flagged ? ([["Flagged, kept", summary.flagged]] as Array<[string, string]>) : []),
              ["Mapping", summary.mapping],
              ["File hash", <span key="hash" className="font-mono">{`sha256:${simulatedHash(`${selected?.name}${mapping}`, 16)}… (simulated)`}</span>],
            ]}
          />
          {quarantine ? (
            <Callout tone="warn" title="A required check failed. Nothing will reach monitoring.">
              <Field label="Assign a data owner to fix it" className="mt-2">
                {(props) => (
                  <Select {...props} value={owner} onChange={(event) => setOwner(event.target.value)}>
                    <option value="">Choose an owner</option>
                    {DATA_OWNERS.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </Select>
                )}
              </Field>
            </Callout>
          ) : null}
          <Checkbox
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            label="I reviewed the rejected rows. Source rows and the file hash are kept unchanged; only normalized records are written."
          />
        </>
      ) : null}
    </Dialog>
  );
}

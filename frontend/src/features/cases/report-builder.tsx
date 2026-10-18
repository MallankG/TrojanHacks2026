"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { bundleBlockers, bundleComponents } from "@/lib/domain/case";
import { completeBundle, toast, toggleReportFormat, verifyBundleHash } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";

const BUILD_MS = 1400;

const FORMATS: Array<{ key: "json" | "csv" | "pdf"; label: string }> = [
  { key: "json", label: "JSON" },
  { key: "csv", label: "CSV" },
  { key: "pdf", label: "Readable PDF" },
];

// Each check names a control the app enforces.
const PRIVACY_CHECKS = [
  "Identifiers are synthetic, and masked in display by default",
  "Emails, SSNs, card and phone numbers redacted from free text",
  "Narratives and notes pass the PII filter before display",
  "Download requires sign-in; sensitive exports require step-up authentication",
];

/** Human-reviewed SAR-support package. It is a case-support artifact, never a FinCEN filing. */
export function ReportBuilder() {
  const state = useDemoState();
  const role = useRole();
  const [running, setRunning] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const components = useMemo(() => bundleComponents(state), [state]);
  const blockers = bundleBlockers(state, role.readOnly);
  const report = state.caseWork.report;
  const fixed = state.data.driftFixed;

  // Activity hides this page on navigation; finish a pending build instead of leaving it stuck.
  useLayoutEffect(
    () => () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = null;
        setRunning(false);
        completeBundle();
      }
    },
    [],
  );

  const generate = () => {
    if (blockers.length > 0) {
      toast(blockers[0]);
      return;
    }
    setRunning(true);
    timer.current = setTimeout(() => {
      timer.current = null;
      setRunning(false);
      completeBundle();
    }, BUILD_MS);
  };

  const manifest: Array<[string, string]> = report.done
    ? [
        ["Export ID", report.exportId],
        ["Created by", report.by],
        ["Created at", report.at],
        ["Source watermark", "Oct 9 13:59:18 UTC"],
        ["Graph snapshot", `${fixed ? "GS-0412-b · v184" : "GS-0412-a · v183"}`],
        ["Feature set", "v7"],
        ["Model and rules", "xgb-aml v1.4.2 · typology-rules v3.2"],
        ["Policy chunks", "POL-FFIEC-SAR-03, POL-FINCEN-11"],
        ["Narrative revision", `r${report.revision}`],
        ["Bundle checksum", `sha256:${report.hash} (simulated)`],
      ]
    : [];

  const verifyFailed = state.caseWork.verifyResult.startsWith("Mismatch");

  return (
    <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
      <Panel>
        <CardHeader title="SAR-support bundle contents" description="Human-reviewed case-support package. Not an official FinCEN submission." />
        <ul className="flex flex-col">
          {components.map((component) => (
            <li key={component.label} className="flex items-center justify-between gap-2.5 border-t border-line-soft py-2">
              <div>
                <b className="text-[13.5px]">{component.label}</b>
                <div className="text-xs text-muted">{component.note}</div>
              </div>
              <Pill tone={component.ready ? "ok" : "warn"}>{component.ready ? "Ready" : "Missing"}</Pill>
            </li>
          ))}
        </ul>
      </Panel>
      <div className="flex flex-col gap-4">
        <Panel>
          <CardHeader title="Formats and privacy gate" />
          <div role="group" aria-label="Formats" className="flex flex-wrap gap-1.5">
            {FORMATS.map((format) => (
              <Chip key={format.key} pressed={report.formats[format.key]} disabled={role.readOnly} onClick={() => toggleReportFormat(format.key)}>
                {format.label}
              </Chip>
            ))}
          </div>
          <ul className="flex flex-col gap-1.5">
            {PRIVACY_CHECKS.map((check) => (
              <li key={check} className="flex justify-between gap-2 text-[13.5px]">
                <span>{check}</span>
                <Pill tone="ok">Pass</Pill>
              </li>
            ))}
          </ul>
          {blockers.length > 0 ? (
            <Callout tone="warn" title="Before you can generate:">
              <ul className="mt-1 list-disc pl-5">
                {blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            </Callout>
          ) : null}
          <Button className="self-start" disabled={blockers.length > 0 || running} onClick={generate}>
            Generate evidence bundle
          </Button>
          {running ? <Skeleton lines={2} label="Generating bundle" /> : null}
        </Panel>
        {report.done ? (
          <Panel>
            <CardHeader title="Manifest" />
            <KeyValueList items={manifest} mono />
            <div className="flex flex-wrap gap-2">
              <Button variant="ghost" size="sm" onClick={() => toast("Download started (simulated). This is a case-support artifact, not a FinCEN filing.")}>
                Download bundle
              </Button>
              <Button variant="ghost" size="sm" onClick={verifyBundleHash}>
                Verify hash
              </Button>
            </div>
            {state.caseWork.verifyResult ? (
              <Callout tone={verifyFailed ? "bad" : "ok"} role="status">
                {state.caseWork.verifyResult}
              </Callout>
            ) : null}
            <span className="text-xs text-muted">Hashes show tamper evidence for the demo. Storage is not claimed to be immutable.</span>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

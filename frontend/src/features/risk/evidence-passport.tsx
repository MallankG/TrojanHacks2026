"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { buildPassportSections, PASSPORT_FORMAT, PASSPORT_SECTIONS, sealPassport, SECTION_LABELS, tamperedCopy, verifyPassport, webCryptoAvailable, type EvidencePassport, type PassportVerification } from "@/lib/domain/passport";
import { demoTimestamp } from "@/lib/format";
import { ROUTES } from "@/lib/routes";
import { ROLES } from "@/lib/seed/roles";
import { recordPassport, recordPassportCheck, toast } from "@/lib/store/actions";
import { useDemoState, useEvidenceRegistry, useRole, useScoredAlerts } from "@/lib/store/hooks";
import type { AlertId } from "@/lib/types";
import { FeatureGate } from "@/components/shell/feature-gate";
import { Button, buttonClasses } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Field, TextArea } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";

function download(doc: EvidencePassport, name: string) {
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function VerificationResult({ result }: { result: PassportVerification }) {
  return (
    <div className="flex flex-col gap-2" role="status">
      <Callout tone={result.valid ? "ok" : "bad"} title={result.valid ? "Intact." : "Tampering detected."}>
        {result.valid ? "Every section hash and the package hash match the manifest." : result.reason}
      </Callout>
      {result.sections.length > 0 ? (
        <ul className="flex flex-col">
          {result.sections.map((check) => (
            <li key={check.section} className="flex items-center justify-between gap-2 border-t border-line-soft py-1.5 text-[13px]">
              <span>{SECTION_LABELS[check.section]}</span>
              <Pill tone={check.status === "match" ? "ok" : "bad"}>{check.status === "match" ? "Hash matches" : check.status === "modified" ? "Modified" : "Missing"}</Pill>
            </li>
          ))}
          <li className="flex items-center justify-between gap-2 border-t border-line-soft py-1.5 text-[13px] font-semibold">
            <span>Package hash (header and manifest)</span>
            <Pill tone={result.packageHashMatches ? "ok" : "bad"}>{result.packageHashMatches ? "Matches" : "Does not match"}</Pill>
          </li>
        </ul>
      ) : null}
    </div>
  );
}

export function EvidencePassportView({ alertId }: { alertId: AlertId }) {
  const state = useDemoState();
  const role = useRole();
  const registry = useEvidenceRegistry();
  const alerts = useScoredAlerts();
  const alert = alerts.find((a) => a.id === alertId) ?? alerts[0];
  const issued = state.passports.find((p) => p.alertId === alertId);
  const issuedDoc = issued?.document as EvidencePassport | undefined;
  const [busy, setBusy] = useState(false);
  const [pasted, setPasted] = useState("");
  const [result, setResult] = useState<PassportVerification | null>(null);
  const [drift, setDrift] = useState<string[] | null>(null);
  const sections = useMemo(() => buildPassportSections(state, alert, registry), [state, alert, registry]);
  const cryptoOk = webCryptoAvailable();

  const issue = async () => {
    setBusy(true);
    try {
      const doc = await sealPassport({ format: PASSPORT_FORMAT, subject: { type: "alert", id: alert.id }, issued_at: demoTimestamp(state.seq + 1), issued_by: ROLES[state.role].name }, sections);
      recordPassport(alert.id, doc.manifest.package_hash, doc);
      setResult(null);
      setDrift(null);
    } catch (error) {
      toast(error instanceof Error ? error.message : "Could not issue the passport.");
    } finally {
      setBusy(false);
    }
  };

  const check = async (doc: unknown) => {
    const outcome = await verifyPassport(doc);
    setResult(outcome);
    const changed = outcome.sections.filter((s) => s.status !== "match").map((s) => SECTION_LABELS[s.section]);
    recordPassportCheck(alert.id, outcome.valid, changed.join(", ") || "header");
  };

  const checkPasted = async () => {
    try {
      await check(JSON.parse(pasted));
    } catch {
      setResult({ valid: false, reason: "That is not valid JSON.", sections: [], packageHashMatches: false });
    }
  };

  /** Which sections differ between the issued passport and the case as it is now. */
  const compareWithCurrent = async () => {
    if (!issuedDoc) return;
    const current = await sealPassport(issuedDoc, sections);
    setDrift(PASSPORT_SECTIONS.filter((name) => current.manifest.sections[name] !== issuedDoc.manifest.sections[name]).map((name) => SECTION_LABELS[name]));
  };

  return (
    <FeatureGate flag="passport">
      <div className="flex flex-col gap-4">
        <Panel>
          <CardHeader
            eyebrow={`${alert.id} · ${alert.typology}`}
            title="Evidence passport"
            description="A compact, tamper-evident package of everything behind this alert. Each section is hashed with SHA-256; the package hash covers the header and every section hash."
            actions={
              <>
                <Link href={ROUTES.riskAlert(alert.id)} className={buttonClasses("ghost", "sm")}>
                  Back to alert
                </Link>
                <Button size="sm" disabled={busy || !cryptoOk || role.readOnly} onClick={issue}>
                  {issued ? "Issue a new passport" : "Issue passport"}
                </Button>
              </>
            }
          />
          {!cryptoOk ? <Callout tone="warn">Web Crypto is unavailable in this browser context. Open the app on https or localhost to hash passports.</Callout> : null}
          {role.readOnly ? <Callout tone="mute">Auditors can verify passports but not issue them.</Callout> : null}
          <ul className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(220px,1fr))]">
            {PASSPORT_SECTIONS.map((name) => {
              const value = sections[name];
              const size = Array.isArray(value) ? `${value.length} item${value.length === 1 ? "" : "s"}` : "Object";
              return (
                <li key={name} className="flex items-center justify-between gap-2 rounded-[10px] bg-subtle px-3 py-2 text-[13px]">
                  <span>{SECTION_LABELS[name]}</span>
                  <span className="text-xs text-muted">{size}</span>
                </li>
              );
            })}
          </ul>
          <span className="text-xs text-muted">Not signed: key management is not configured for this prototype. Hashes show tampering; they do not prove who issued the passport.</span>
        </Panel>

        <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
          <Panel>
            <CardHeader title="Manifest" description={issued ? `${issued.id} · issued by ${issued.by} at ${issued.at}` : "No passport issued for this alert yet."} />
            {issuedDoc ? (
              <>
                <KeyValueList
                  mono
                  items={[
                    ["Format", issuedDoc.format],
                    ["Canonicalization", issuedDoc.manifest.canonicalization],
                    ...PASSPORT_SECTIONS.map((name): [string, string] => [SECTION_LABELS[name], `${issuedDoc.manifest.sections[name].slice(0, 16)}…`]),
                    ["Package hash", issuedDoc.manifest.package_hash],
                  ]}
                />
                <div className="flex flex-wrap gap-2">
                  <Button variant="ghost" size="sm" onClick={() => download(issuedDoc, `${issued?.id}.json`)}>
                    Download JSON
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => check(issuedDoc)}>
                    Verify issued copy
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => check(tamperedCopy(issuedDoc))}>
                    Simulate tampering
                  </Button>
                  <Button variant="ghost" size="sm" onClick={compareWithCurrent}>
                    Compare with current case
                  </Button>
                </div>
                {drift ? (
                  <Callout tone={drift.length ? "warn" : "ok"} role="status">
                    {drift.length ? `Changed since this passport was issued: ${drift.join(", ")}. Issue a new passport to capture them.` : "Nothing has changed since this passport was issued."}
                  </Callout>
                ) : null}
              </>
            ) : (
              <span className="text-[13.5px] text-muted">Issue a passport to see its section hashes.</span>
            )}
          </Panel>
          <Panel>
            <CardHeader title="Verify a passport" description="Paste a downloaded passport. Every hash is recomputed in the browser; nothing is uploaded. The backend offers the same check at POST /evidence-passports/verify." />
            <Field label="Passport JSON">{(props) => <TextArea {...props} rows={6} className="font-mono text-xs" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder='{ "format": "tracelayer.evidence-passport/v1", ... }' />}</Field>
            <Button size="sm" className="self-start" disabled={!pasted.trim() || !cryptoOk} onClick={checkPasted}>
              Verify pasted passport
            </Button>
            {result ? <VerificationResult result={result} /> : null}
          </Panel>
        </div>
      </div>
    </FeatureGate>
  );
}

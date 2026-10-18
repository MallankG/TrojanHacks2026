"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { cn } from "@/lib/cn";
import { errorMessage, invalidateLive, liveApi, useLive, type CaseDecision, type CaseDetail, type Disposition } from "@/lib/live/api";
import { BAND_LABEL, BAND_TONE, CASE_TONE, alertLabel, caseLabel, utc, words } from "@/lib/live/format";
import { LIVE_ROLES, type LiveRole } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Field, Select, TextArea, TextInput } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { LiveAudit } from "./live-audit";
import { LiveData } from "./live-state";

const DISPOSITIONS: Array<[Disposition, string]> = [
  ["no_further_action", "Close, no further action"],
  ["false_positive", "Close as false positive"],
  ["monitor", "Monitor"],
  ["needs_more_information", "Needs more information"],
  ["escalate_for_sar_consideration", "Escalate for SAR-support review"],
];

/** Live backend cases at the top of the case navigator, with a quick "open a case" form. */
export function LiveCaseEntries({ selectedId }: { selectedId: string }) {
  const profile = useLiveProfile();
  const cases = useLive(async () => (profile ? (await liveApi.cases()).items : []), `cases:${profile?.id ?? "demo"}`);
  if (!profile) return null;
  const role = LIVE_ROLES[profile.role];

  return (
    <div className="flex flex-col gap-2 border-b border-line-soft pb-3">
      <div className="eyebrow ml-1">Live cases</div>
      {role.canInvestigate ? <NewCase /> : null}
      {cases.error ? (
        <Callout tone="bad" role="alert">
          {cases.error}
        </Callout>
      ) : cases.loading ? (
        <Skeleton lines={2} label="Loading live cases" />
      ) : cases.data && cases.data.length > 0 ? (
        cases.data.map((c) => {
          const selected = c.id === selectedId;
          return (
            <Link
              key={c.id}
              href={ROUTES.case(c.id)}
              aria-current={selected ? "page" : undefined}
              className={cn(
                "flex flex-col gap-1.5 rounded-xl border bg-white text-left transition-colors",
                selected ? "border-2 border-brand bg-[#f7fcfb] px-[13px] py-[11px]" : "border-line px-3.5 py-3 hover:border-[#9fb0c6]",
              )}
            >
              <span className="flex w-full justify-between gap-2">
                <b className="font-mono">{caseLabel(c.case_number)}</b>
                <Pill tone="ok">Live</Pill>
              </span>
              <span className="text-[13.5px] font-semibold">{c.title}</span>
              <Pill tone={CASE_TONE[c.status]} className="self-start">
                {words(c.status)}
              </Pill>
              <span className="text-xs text-muted">Owner {c.assignee?.display_name ?? "unassigned"}</span>
            </Link>
          );
        })
      ) : (
        <p className="ml-1 text-xs text-muted">No live cases yet. Open one from an alert or with the form above.</p>
      )}
      <div className="eyebrow ml-1 mt-1">Sample cases</div>
    </div>
  );
}

function NewCase() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await liveApi.createCase({ title: title.trim(), alert_ids: [] });
      setTitle("");
      invalidateLive("cases");
      router.push(ROUTES.case(created.id));
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="flex flex-col gap-1.5" onSubmit={submit}>
      <label className="sr-only" htmlFor="live-new-case">
        New case title
      </label>
      <div className="flex gap-1.5">
        <TextInput id="live-new-case" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="New case title" className="min-h-9 py-1.5" />
        <Button type="submit" size="sm" variant="ghost" disabled={busy || !title.trim()}>
          Open
        </Button>
      </div>
      {error ? <span className="text-xs font-semibold text-danger-ink">{error}</span> : null}
    </form>
  );
}

/** A live backend case: linked alerts, evidence, narratives, the reviewer decision and its audit trail. */
export function LiveCaseDetail({ caseId }: { caseId: string }) {
  const profile = useLiveProfile();
  const role = LIVE_ROLES[profile?.role ?? "analyst"];
  const detail = useLive(() => liveApi.case(caseId), `case:${caseId}`);
  const [auditKey, setAuditKey] = useState(0);
  const refresh = () => {
    detail.reload();
    invalidateLive("cases");
    setAuditKey((k) => k + 1);
  };

  if (!profile) {
    return <Callout tone="warn" title="This is a live backend case.">Sign in with your backend account (email and password) to open it.</Callout>;
  }

  return (
    <LiveData query={detail} label="case">
      {(c) => (
        <>
          <Panel>
            <CardHeader
              eyebrow="Live case"
              title={`${caseLabel(c.case_number)} · ${c.title}`}
              description={`Opened ${utc(c.created_at)} · owner ${c.assignee?.display_name ?? "unassigned"}`}
              actions={
                <>
                  <Pill tone={CASE_TONE[c.status]}>{words(c.status)}</Pill>
                  {c.disposition ? <Pill tone="dark">{words(c.disposition)}</Pill> : null}
                </>
              }
            />
          </Panel>
          <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
            <div className="flex flex-col gap-4">
              <Panel>
                <CardHeader title="Linked alerts" />
                {c.alerts.length === 0 ? (
                  <EmptyState title="No alerts linked." />
                ) : (
                  <ul className="flex flex-wrap gap-2">
                    {c.alerts.map(({ alerts: a }) => (
                      <li key={a.id}>
                        <Link href={ROUTES.riskAlert(a.id)} className="inline-flex items-center gap-2 rounded-xl border border-line px-3 py-2 hover:border-[#9fb0c6]">
                          <b className="font-mono">{alertLabel(a.alert_number)}</b>
                          <span className="text-[13px]">{words(a.typology)}</span>
                          <Pill tone={BAND_TONE[a.priority_band]}>{BAND_LABEL[a.priority_band]}</Pill>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
              <EvidencePanel detail={c} role={role} onChanged={refresh} />
              <Panel>
                <CardHeader title="Narratives" description="Drafted by the agent service and checked by its verifier before display." />
                {c.narratives.length === 0 ? (
                  <EmptyState title="No narrative drafted yet." description="The agent service writes drafts through POST /cases/{id}/narratives." />
                ) : (
                  <ul className="flex flex-col gap-1.5 text-[13.5px]">
                    {c.narratives.map((n) => (
                      <li key={n.id} className="flex flex-wrap items-center gap-2">
                        <b>Revision {n.version}</b>
                        <span className="text-muted">{words(n.author_type)}</span>
                        <Pill tone={n.verification_status === "passed" ? "ok" : n.verification_status === "pending" ? "mute" : "warn"}>Verifier: {words(n.verification_status)}</Pill>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
            <div className="flex flex-col gap-4">
              <DecisionPanel detail={c} role={role} onDecided={refresh} />
              <ExportPanel caseId={c.id} role={role} onExported={() => setAuditKey((k) => k + 1)} />
            </div>
          </div>
          <LiveAudit key={auditKey} objectType="case" objectId={c.id} title="Case audit trail" />
        </>
      )}
    </LiveData>
  );
}

function EvidencePanel({ detail, role, onChanged }: { detail: CaseDetail; role: LiveRole; onChanged: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const add = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await liveApi.addNote(detail.id, note.trim());
      setNote("");
      onChanged();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel>
      <CardHeader title="Evidence and notes" description="Alerts attach their transactions, signals and data-quality issues automatically." />
      {detail.evidence.length === 0 ? (
        <EmptyState title="No evidence yet." />
      ) : (
        <DataTable caption="Case evidence">
          <thead>
            <tr>
              <Th>Type</Th>
              <Th>Explanation</Th>
              <Th>Added by</Th>
              <Th>When</Th>
            </tr>
          </thead>
          <tbody>
            {detail.evidence.map((e) => (
              <Tr key={e.id}>
                <Td>{words(e.evidence_type)}</Td>
                <Td className="max-w-[420px]">{e.explanation}</Td>
                <Td>{words(e.added_by_type)}</Td>
                <Td className="whitespace-nowrap text-xs">{utc(e.created_at)}</Td>
              </Tr>
            ))}
          </tbody>
        </DataTable>
      )}
      {role.canInvestigate && detail.status !== "closed" ? (
        <form className="flex flex-col gap-2" onSubmit={add}>
          <Field label="Add an analyst note">{(props) => <TextArea {...props} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
          <div>
            <Button type="submit" variant="ghost" size="sm" disabled={busy || !note.trim()}>
              Add note
            </Button>
          </div>
        </form>
      ) : null}
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
    </Panel>
  );
}

function DecisionPanel({ detail, role, onDecided }: { detail: CaseDetail; role: LiveRole; onDecided: () => void }) {
  const [decision, setDecision] = useState<CaseDecision["decision"]>(role.canReview ? "accept" : "edit");
  const [disposition, setDisposition] = useState<Disposition | "">("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closed = detail.status === "closed";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await liveApi.decide(detail.id, { decision, disposition: disposition || null, notes: notes.trim() || undefined });
      setNotes("");
      setDisposition("");
      onDecided();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  const requestReview = async () => {
    setBusy(true);
    setError(null);
    try {
      await liveApi.updateCase(detail.id, { status: "pending_review" });
      onDecided();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel>
      <CardHeader title="Human review" description="TraceLayer never decides guilt, files SARs or freezes accounts. A reviewer records the outcome." />
      {closed ? (
        <Callout tone="ok" role="status" title="Case closed.">
          {words(detail.disposition)} · {utc(detail.disposed_at)}
        </Callout>
      ) : role.canReview ? (
        <form className="flex flex-col gap-3" onSubmit={submit}>
          <Field label="Decision">
            {(props) => (
              <Select {...props} value={decision} onChange={(e) => setDecision(e.target.value as CaseDecision["decision"])}>
                <option value="accept">Accept the case work</option>
                <option value="edit">Record an edit</option>
                <option value="reject">Reject the case work</option>
                <option value="escalate">Escalate</option>
              </Select>
            )}
          </Field>
          <Field label="Disposition" hint="Any disposition closes the case; SAR-support review or an escalate decision moves it to escalated.">
            {(props) => (
              <Select {...props} value={disposition} onChange={(e) => setDisposition(e.target.value as Disposition | "")}>
                <option value="">No disposition yet</option>
                {DISPOSITIONS.map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </Select>
            )}
          </Field>
          <Field label="Rationale">{(props) => <TextArea {...props} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Why, citing evidence" />}</Field>
          <Button type="submit" disabled={busy || !notes.trim()}>
            Record decision
          </Button>
        </form>
      ) : role.canInvestigate ? (
        <div className="flex flex-col gap-2">
          <p className="text-[13.5px]">Only a reviewer can accept, reject, escalate or record a disposition. When your case work is ready, send it for review.</p>
          <div>
            <Button variant="ghost" disabled={busy || detail.status === "pending_review"} onClick={requestReview}>
              {detail.status === "pending_review" ? "Waiting for a reviewer" : "Send for review"}
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-[13.5px] text-muted">Your role ({role.label}) can read decisions but not record them.</p>
      )}
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
      {detail.decisions.length > 0 ? (
        <ul className="flex flex-col gap-2 border-t border-line-soft pt-3 text-[13.5px]">
          {detail.decisions.map((d) => (
            <li key={d.id}>
              <b>{words(d.decision)}</b>
              {d.disposition ? ` · ${words(d.disposition)}` : ""} by {d.decided_by_profile?.display_name ?? "unknown"} ({words(d.decided_by_profile?.role)})
              <span className="block text-xs text-muted">{utc(d.decided_at)}</span>
              {d.notes ? <span className="block">{d.notes}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </Panel>
  );
}

function ExportPanel({ caseId, role, onExported }: { caseId: string; role: LiveRole; onExported: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ url: string; path: string } | null>(null);
  if (!role.canInvestigate) return null;
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const exported = await liveApi.exportUrl(caseId);
      setResult(exported);
      onExported();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel>
      <CardHeader title="Evidence bundle" description="JSON export of the case's alerts, transactions, signals, data-quality issues and evidence. A case-support artifact, not a FinCEN filing." />
      <div>
        <Button variant="ghost" disabled={busy} onClick={run}>
          {busy ? "Exporting" : "Export evidence bundle"}
        </Button>
      </div>
      {result ? (
        <KeyValueList
          items={[
            ["Stored at", <span key="p" className="font-mono text-xs">{result.path}</span>],
            [
              "Download",
              <a key="u" href={result.url} target="_blank" rel="noreferrer" className="font-semibold text-brand-link underline underline-offset-2">
                Signed link (expires in 5 minutes)
              </a>,
            ],
          ]}
        />
      ) : null}
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
    </Panel>
  );
}

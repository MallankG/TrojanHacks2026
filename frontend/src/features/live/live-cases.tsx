"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { cn } from "@/lib/cn";
import { call, errorMessage, invalidateLive, liveApi, useLive, type CaseDecision, type CaseDetail, type Disposition } from "@/lib/live/api";
import { BAND_LABEL, BAND_TONE, CASE_TONE, alertLabel, caseLabel, utc, words } from "@/lib/live/format";
import { LIVE_ROLES, type LiveRole } from "@/lib/live/roles";
import { useLiveProfile } from "@/lib/live/session";
import { ROUTES } from "@/lib/routes";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Field, Select, TextArea, TextInput } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";
import { DataTable, Td, Th, Tr } from "@/components/ui/table";
import { casesApi, sha256Hex } from "@/lib/live/api-cases";
import { CaseAgents } from "./live-agents";
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
              <span className="text-xs text-muted">Owner {c.assignee?.display_name ?? (c.assigned_to ? c.assigned_to === profile.id ? "You" : "Assigned colleague" : "unassigned")}</span>
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
              description={`Opened ${utc(c.created_at)} · owner ${c.assignee?.display_name ?? (c.assigned_to ? c.assigned_to === profile.id ? "You" : "Assigned colleague" : "unassigned")}${c.due_at ? ` · review due ${utc(c.due_at)}` : ""}`}
              actions={
                <>
                  <Pill tone={CASE_TONE[c.status]}>{words(c.status)}</Pill>
                  {c.disposition ? <Pill tone="dark">{words(c.disposition)}</Pill> : null}
                </>
              }
            />
          </Panel>
          <CaseAgents key={c.id} caseId={c.id} closed={c.status === "closed"} initialRunId={c.agent_runs.find(r => r.graph_name === "case_workflow" && r.managed)?.id} />
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
              <ChecklistPanel caseId={c.id} version={auditKey} />
              <Panel>
                <CardHeader title="Narratives" description="Drafted by the agent service and checked by its verifier before display." />
                {c.narratives.length === 0 ? (
                  <EmptyState title="No narrative drafted yet." description="Use Investigation agents above to draft and verify this case." />
                ) : (
                  <ul className="flex flex-col gap-1.5 text-[13.5px]">
                    {c.narratives.map((n) => (
                      <li key={n.id} className="flex flex-wrap items-center gap-2">
                        <b>Revision {n.version}</b>
                        <span className="text-muted">{words(n.author_type)}</span>
                        <Pill tone={n.verification_status === "passed" ? "ok" : n.verification_status === "pending" ? "mute" : "warn"}>Verifier: {words(n.verification_status)}</Pill>
                        <NarrativeReceipt narrativeId={n.id}/>
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
      await liveApi.decide(detail.id, { decision, narrative_id: detail.narratives[0]?.id, disposition: disposition || null, notes: notes.trim() || undefined });
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
          {detail.status !== "escalated" ? <EscalationRequest caseId={detail.id} onRequested={onDecided} /> : null}
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

const CHECK_TONE = { complete: "ok", partial: "warn", missing: "bad" } as const;

/** Evidence coverage computed from real case references; nothing is marked present without one. */
function ChecklistPanel({ caseId, version }: { caseId: string; version: number }) {
  const checklist = useLive(() => casesApi.checklist(caseId), `checklist:${caseId}:${version}`);
  return (
    <Panel>
      <CardHeader title="Evidence checklist" description="Complete, partial or missing, from the evidence actually attached to this case." />
      <LiveData query={checklist} label="checklist">
        {(c) => (
          <ul className="flex flex-col gap-1.5">
            {c.items.map((item) => (
              <li key={item.key} className="flex flex-wrap items-start gap-2 text-[13.5px]">
                <Pill tone={CHECK_TONE[item.status]}>{words(item.status)}</Pill>
                <span>
                  <b>{item.label}</b> <span className="text-muted">{item.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </LiveData>
    </Panel>
  );
}

/** Analyst side of four-eyes: ask a reviewer (a different person) to approve an escalation. */
function EscalationRequest({ caseId, onRequested }: { caseId: string; onRequested: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await casesApi.requestEscalation(caseId, reason.trim());
      setReason("");
      onRequested();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="flex flex-col gap-2 border-t border-line-soft pt-3" onSubmit={submit}>
      <Field label="Request escalation" hint="A reviewer other than you must approve it.">
        {(props) => <TextArea {...props} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this needs SAR-support review" />}
      </Field>
      <div>
        <Button type="submit" variant="ghost" size="sm" disabled={busy || !reason.trim()}>
          Request escalation
        </Button>
      </div>
      {error ? <span className="text-xs font-semibold text-danger-ink">{error}</span> : null}
    </form>
  );
}

/** Hashed SAR-support evidence package (case-support artifact, not a FinCEN filing). */
function ExportPanel({ caseId, role, onExported }: { caseId: string; role: LiveRole; onExported: () => void }) {
  const [version, setVersion] = useState(0);
  const exports = useLive(() => casesApi.exports(caseId), `case-exports:${caseId}:${version}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [verified, setVerified] = useState<Record<string, string>>({});
  if (!role.canInvestigate) return null;

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      await casesApi.exportPackage(caseId);
      setVersion((v) => v + 1);
      invalidateLive("exports");
      onExported();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (key: string, url: string, expected: string) => {
    try {
      setVerified((v) => ({ ...v, [key]: "checking" }));
      const actual = await sha256Hex(url);
      setVerified((v) => ({ ...v, [key]: actual === expected ? "ok" : "mismatch" }));
    } catch (problem) {
      setVerified((v) => ({ ...v, [key]: errorMessage(problem) }));
    }
  };

  const label = (state: string) => (state === "ok" ? "Hash matches" : state === "mismatch" ? "Hash does not match" : state === "checking" ? "Checking" : state);

  return (
    <Panel>
      <CardHeader
        title="Evidence package"
        description="Manifest, transaction schedule, entities, detector outputs, data-quality issues, decisions and audit timeline, each with a SHA-256 hash. Tamper-evident, not legally certified."
      />
      <div>
        <Button variant="ghost" disabled={busy} onClick={run}>
          {busy ? "Building package" : "Export evidence package"}
        </Button>
      </div>
      {error ? (
        <Callout tone="bad" role="alert">
          {error}
        </Callout>
      ) : null}
      <LiveData query={exports} label="exports">
        {(list) =>
          list.length === 0 ? (
            <p className="text-xs text-muted">No packages yet.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {list.map((p) => (
                <li key={p.id} className="flex flex-col gap-1 rounded-[10px] border border-line-soft p-2.5 text-[13px]">
                  <span>
                    <b>{utc(p.created_at)}</b> by {p.creator?.display_name ?? "unknown"} · manifest{" "}
                    <span className="font-mono text-xs">{p.manifest_sha256.slice(0, 16)}…</span>
                  </span>
                  {p.files.map((f) => {
                    const key = `${p.id}:${f.name}`;
                    const state = verified[key];
                    return (
                      <span key={f.name} className="flex flex-wrap items-center gap-2 text-xs">
                        {f.url ? (
                          <a href={f.url} target="_blank" rel="noreferrer" className="font-mono font-semibold text-brand-link underline underline-offset-2">
                            {f.name}
                          </a>
                        ) : (
                          <span className="font-mono">{f.name}</span>
                        )}
                        <span className="font-mono text-muted">{f.sha256.slice(0, 12)}…</span>
                        {f.url ? (
                          <Button variant="link" onClick={() => verify(key, f.url!, f.sha256)}>
                            Verify hash
                          </Button>
                        ) : null}
                        {state ? <Pill tone={state === "ok" ? "ok" : state === "checking" ? "mute" : "bad"}>{label(state)}</Pill> : null}
                      </span>
                    );
                  })}
                </li>
              ))}
            </ul>
          )
        }
      </LiveData>
    </Panel>
  );
}


function NarrativeReceipt({narrativeId}:{narrativeId:string}) {
  const [open,setOpen]=useState(false);
  const query=useLive(()=>open?call<{content:string;claims:Array<{claim_index:number;claim_text:string;verification:string;referenced_transaction_ids:string[];cited_chunk_ids:string[]}>;citations:Array<{quoted_text:string;policy_chunks:{id:string;policy_documents:{title:string;source_url:string}}}>}>(`/narratives/${narrativeId}`):Promise.resolve(null),`narrative:${narrativeId}:${open}`);
  return <details className="w-full" onToggle={e=>setOpen(e.currentTarget.open)}><summary className="cursor-pointer text-xs font-semibold">Read saved narrative and claims</summary>
    {open?<LiveData query={query} label="narrative">{n=>n?<div className="mt-2 flex flex-col gap-2"><p>{n.content}</p><ol className="list-decimal pl-5">{n.claims.map(c=><li key={c.claim_index}>{c.claim_text}<p className="break-all text-xs text-muted">{c.verification} · Transactions: {c.referenced_transaction_ids.join(", ")} · Policy: {c.cited_chunk_ids.join(", ")}</p></li>)}</ol>{n.citations.map((c,i)=><blockquote key={i} className="border-l-2 border-line pl-3"><a className="text-brand-link underline" href={c.policy_chunks.policy_documents.source_url} target="_blank" rel="noreferrer">{c.policy_chunks.policy_documents.title}</a><p>{c.quoted_text}</p><span className="break-all text-xs">{c.policy_chunks.id}</span></blockquote>)}</div>:null}</LiveData>:null}
  </details>;
}

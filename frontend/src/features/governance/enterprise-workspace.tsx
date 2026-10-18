"use client";

import { useLayoutEffect, useRef, useState } from "react";
import sample from "@/lib/seed/enterprise-demo.json";
import { ApiError, apiRequest } from "@/lib/api/client";
import { Button, ButtonLink } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { Field, Select, TextArea, TextInput } from "@/components/ui/field";
import { Callout } from "@/components/ui/callout";
import { Pill } from "@/components/ui/pill";

type Kind = "comment" | "assignment" | "bookmark" | "hypothesis" | "vote" | "review_request" | "conflict" | "resolution";
type Payload = { kind: Kind; text: string; evidence_ids: string[]; mentions: string[]; target_id: string | null; target_user_id: string | null; vote: string | null };
type Event = { id: string; revision: number; actor_id: string; kind: Kind; payload: Payload; created_at: string; payload_hash?: string };
type Room = { case: { id: string; title: string }; case_role: string; policy: typeof sample.policy; revision: number; members: typeof sample.members; evidence: typeof sample.evidence; events: Event[] };
const newDemo = (): Room => ({ case: { id: sample.case_id, title: "Synthetic layering investigation" }, case_role: "lead", policy: sample.policy, revision: 0, members: sample.members, evidence: sample.evidence, events: [] });
const labels: Record<Kind, string> = { comment: "Comment", assignment: "Assignment", bookmark: "Evidence bookmark", hypothesis: "Hypothesis", vote: "Hypothesis vote", review_request: "Review request", conflict: "Flag conflict", resolution: "Resolve conflict" };

type Handoff = { verified_developments: { text: string; evidence_refs: string[] }[]; pending_questions: { note: string; kind: string }[]; review_at: string | null; caveats: string[] };
const modes = ["Investigation room", "Operations", "Release and residency"] as const;

export function EnterpriseWorkspace() {
  const [mode, setMode] = useState<(typeof modes)[number]>(modes[0]);
  const [room, setRoom] = useState<Room>(newDemo);
  const [live, setLive] = useState(false);
  const [token, setToken] = useState("");
  const [caseId, setCaseId] = useState(sample.case_id);
  const [kind, setKind] = useState<Kind>("comment");
  const [text, setText] = useState("");
  const [mention, setMention] = useState("");
  const [targetUser, setTargetUser] = useState(sample.reviewer_id);
  const [evidenceId, setEvidenceId] = useState(sample.evidence[0].id);
  const [targetEvent, setTargetEvent] = useState("");
  const [vote, setVote] = useState("support");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [briefing, setBriefing] = useState<Handoff | null>(null);
  const [metrics, setMetrics] = useState(sample.operations);
  const [operationsLive, setOperationsLive] = useState(false);
  const [environment, setEnvironment] = useState<null | { declared_region: string; approved_destinations: string[]; locations: Record<string, string>; caveat: string }>(null);
  const [release, setRelease] = useState<null | { status: string; signature: string; caveat?: string; verified_files?: number }>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);
  useLayoutEffect(() => () => { setToken(""); setLive(false); setRoom(newDemo()); setBriefing(null); setEnvironment(null); setRelease(null); setMetrics(sample.operations); setOperationsLive(false); }, []);
  const headers = { Authorization: `Bearer ${token}` };
  const writable = room.case_role !== "observer";
  const targets = room.events.filter(e => e.kind === (kind === "vote" ? "hypothesis" : "conflict"));

  async function work(task: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await task(); }
    catch (error) {
      const status = error instanceof ApiError ? error.status : 0;
      setMessage(status === 409 ? "This room changed or the retry key conflicts. Reload before resolving and trying again. Your note is preserved." : status === 403 ? "Access denied. Check case role, permitted purpose, region, clearance and step-up authentication." : error instanceof Error ? error.message : "Request failed.");
    } finally { setBusy(false); }
  }
  async function load() {
    const result = await apiRequest<Room>(`/enterprise/cases/${caseId}/room`, { headers });
    setRoom(result); setLive(true); setBriefing(null); setTargetEvent("");
    setTargetUser(result.members[0]?.user_id ?? ""); setEvidenceId(result.evidence[0]?.id ?? ""); setMention("");
    setMessage("Case room loaded. Only authorized members can read or contribute.");
  }
  function reset() { setRoom(newDemo()); setCaseId(sample.case_id); setLive(false); setBriefing(null); setMetrics(sample.operations); setOperationsLive(false); setRelease(null); setEnvironment(null); setMessage("Synthetic room reset. Tenant records were not changed."); pending.current = null; }
  async function addEvent() {
    const payload: Payload = { kind, text, mentions: mention ? [mention] : [], evidence_ids: kind === "bookmark" && evidenceId ? [evidenceId] : [], target_id: ["vote", "resolution"].includes(kind) ? targetEvent || null : null, target_user_id: kind === "assignment" ? targetUser || null : null, vote: kind === "vote" ? vote : null };
    const signature = JSON.stringify(payload);
    if (pending.current?.signature !== signature) pending.current = { signature, key: crypto.randomUUID() };
    if (live) {
      await apiRequest(`/enterprise/cases/${room.case.id}/room/events`, { method: "POST", headers, body: { ...payload, client_id: pending.current.key, expected_revision: room.revision } });
      await load();
    } else {
      const event: Event = { id: crypto.randomUUID(), revision: room.revision + 1, actor_id: sample.lead_id, kind, payload, created_at: new Date().toISOString() };
      setRoom(r => ({ ...r, revision: r.revision + 1, events: [event, ...r.events] }));
      setMessage("Synthetic action recorded for this page session only.");
    }
    pending.current = null; setText(""); setBriefing(null);
  }
  const canPost = writable && !busy && (!(["comment", "hypothesis", "review_request", "conflict", "resolution"] as Kind[]).includes(kind) || text.trim().length >= 5) && (kind !== "bookmark" || Boolean(evidenceId)) && (!["vote", "resolution"].includes(kind) || Boolean(targetEvent)) && (kind !== "assignment" || Boolean(targetUser));
  async function handoff() {
    if (live) {
      setBriefing(await apiRequest<Handoff>(`/enterprise/cases/${room.case.id}/handoff`, { method: "POST", headers, body: { recipient_id: targetUser } }));
    } else {
      const unresolved = room.events.filter(e => ["hypothesis", "review_request", "conflict"].includes(e.kind) && !room.events.some(r => r.kind === "resolution" && r.payload.target_id === e.id));
      setBriefing({ verified_developments: [], pending_questions: unresolved.map(e => ({ note: e.payload.text, kind: e.kind })), review_at: room.policy.review_at, caveats: ["No verified claims are loaded in this synthetic room. Collaborator notes remain unverified."] });
    }
  }
  const assigned = room.events.find(e => e.kind === "assignment")?.payload.target_user_id;
  return <div className="flex flex-col gap-4">
    <Panel>
      <CardHeader title="Enterprise investigation workspace" description="Coordinate case work, inspect service health and verify release boundaries." actions={<Pill tone={live ? "ok" : "info"}>{live ? "Authenticated case room" : "Synthetic session demo"}</Pill>} />
      <Callout tone="mute">{live ? "Case access is enforced by the backend. Sensitive exports require stronger authentication." : "Demo actions stay in this page session. No messages are sent and no tenant records are changed."}</Callout>
      <details><summary className="cursor-pointer text-sm font-semibold">Connect an authorized case</summary><form className="mt-3 flex flex-col gap-3" onSubmit={e => { e.preventDefault(); void work(load); }}>
        <Field label="Supabase access token" hint="Kept only in page memory; prototype sign-in does not issue a backend token.">{p => <TextInput {...p} type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); setLive(false); setRoom(newDemo()); setBriefing(null); setEnvironment(null); setRelease(null); setMetrics(sample.operations); setOperationsLive(false); }} />}</Field>
        <Field label="Backend case UUID">{p => <TextInput {...p} value={caseId} onChange={e => { setCaseId(e.target.value); setLive(false); setRoom(newDemo()); }} />}</Field>
        <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || !token}>Load case room</Button><Button variant="ghost" disabled={busy} onClick={reset}>Reset synthetic session</Button></div>
      </form></details>
      <p role="status" aria-live="polite" className="text-sm">{busy ? "Working…" : message}</p>
    </Panel>
    <nav aria-label="Enterprise workspace sections" className="flex flex-wrap gap-2">{modes.map(m => <Button key={m} variant={mode === m ? "dark" : "ghost"} size="sm" aria-pressed={mode === m} onClick={() => setMode(m)}>{m}</Button>)}</nav>
    {mode === "Investigation room" && <>
      <Panel><CardHeader title={room.case.title} description={`Revision ${room.revision} · your case role: ${room.case_role}`} /><div className="flex flex-wrap gap-2"><Pill tone="mute">{room.policy.region}</Pill><Pill tone="mute">{room.policy.classification}</Pill><Pill tone="mute">Purpose: {room.policy.purpose}</Pill></div><p className="text-sm">Review date: {room.policy.review_at}{assigned ? ` · Current assignment: ${assigned}` : ""}</p><p className="text-xs text-muted">Permissions combine tenant, case role, purpose, region and clearance. Demo role switching cannot grant backend access.</p><Button variant="ghost" size="sm" className="self-start" disabled={busy || !live} onClick={() => work(load)}>Reload room</Button></Panel>
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel><CardHeader title="Contribute to the investigation" description="Hypotheses and votes are review input; they never become verified evidence automatically." />
          <Field label="Action">{p => <Select {...p} value={kind} onChange={e => { setKind(e.target.value as Kind); setTargetEvent(""); }}>{Object.entries(labels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</Select>}</Field>
          <Field label="Note or rationale">{p => <TextArea {...p} rows={4} maxLength={2000} value={text} onChange={e => setText(e.target.value)} />}</Field>
          <Field label="Mention a case collaborator">{p => <Select {...p} value={mention} onChange={e => setMention(e.target.value)}><option value="">No mention</option>{room.members.map(m => <option key={m.user_id} value={m.user_id}>{m.case_role} · {m.user_id}</option>)}</Select>}</Field>
          {kind === "bookmark" && <Field label="Case evidence">{p => <Select {...p} value={evidenceId} onChange={e => setEvidenceId(e.target.value)}><option value="">Choose evidence</option>{room.evidence.map(e => <option key={e.id} value={e.id}>{e.evidence_type} · {e.explanation}</option>)}</Select>}</Field>}
          {kind === "assignment" && <Field label="Assign to collaborator">{p => <Select {...p} value={targetUser} onChange={e => setTargetUser(e.target.value)}><option value="">Choose assignee</option>{room.members.map(m => <option key={m.user_id} value={m.user_id}>{m.case_role} · {m.user_id}</option>)}</Select>}</Field>}
          {["vote", "resolution"].includes(kind) && <Field label={kind === "vote" ? "Hypothesis to vote on" : "Conflict to resolve"}>{p => <Select {...p} value={targetEvent} onChange={e => setTargetEvent(e.target.value)}><option value="">Choose a recorded item</option>{targets.map(e => <option key={e.id} value={e.id}>#{e.revision} · {e.payload.text}</option>)}</Select>}</Field>}
          {kind === "vote" && <Field label="Vote">{p => <Select {...p} value={vote} onChange={e => setVote(e.target.value)}><option value="support">Support</option><option value="challenge">Challenge</option><option value="abstain">Abstain</option></Select>}</Field>}
          <Button disabled={!canPost} onClick={() => work(addEvent)}>Record {labels[kind].toLowerCase()}</Button>
        </Panel>
        <Panel><CardHeader title="Case activity and evidence bookmarks" description="Live actions have immutable event IDs, payload hashes and request IDs. This view shows the latest 100 events." />
          {room.events.length === 0 && <p className="text-sm text-muted">No collaboration events yet. Add a comment, bookmark or hypothesis to start.</p>}
          <ol className="flex max-h-[650px] flex-col gap-4 overflow-auto">{room.events.map(event => <li key={event.id} className="border-t border-line pt-3 text-sm"><div className="flex justify-between gap-2"><b>#{event.revision} · {labels[event.kind]}</b><span className="text-xs text-muted">{new Date(event.created_at).toLocaleString()}</span></div><p className="mt-2 whitespace-pre-wrap break-words">{event.payload.text || event.payload.vote || "Evidence bookmarked"}</p>{event.payload.mentions.length > 0 && <p className="mt-1 break-all text-xs text-muted">Mentions: {event.payload.mentions.join(", ")}</p>}{event.payload.evidence_ids.map(id => <p className="mt-1 break-all text-xs" key={id}>Evidence: {id}</p>)}{event.payload.target_id && <p className="text-xs text-muted">Refers to {event.payload.target_id}</p>}{event.payload_hash && <details className="mt-1"><summary className="cursor-pointer text-xs">Audit receipt</summary><code className="break-all text-xs">{event.payload_hash}</code></details>}</li>)}</ol>
        </Panel>
      </div>
      <Panel><CardHeader title="Shift briefing and handoff" description="The recipient must already have access. This prepares a briefing; it does not send it." /><Field label="Briefing recipient">{p => <Select {...p} value={targetUser} onChange={e => { setTargetUser(e.target.value); setBriefing(null); }}>{room.members.map(m => <option key={m.user_id} value={m.user_id}>{m.case_role} · {m.user_id}</option>)}</Select>}</Field><Button disabled={busy || !targetUser} className="self-start" onClick={() => work(handoff)}>Prepare permitted handoff</Button>
        {briefing && <div className="flex flex-col gap-3 text-sm"><h3 className="font-semibold">Verified developments</h3>{briefing.verified_developments.length ? briefing.verified_developments.map((d, i) => <p key={i}>{d.text} <span className="text-xs">Evidence: {d.evidence_refs.join(", ")}</span></p>) : <p>No supported factual claims available.</p>}<h3 className="font-semibold">Pending questions and review</h3>{briefing.pending_questions.map((q, i) => <p key={i}>{q.kind}: {q.note} <Pill tone="warn">Unverified note</Pill></p>)}<p>Review date: {briefing.review_at ?? "Not set"}</p><p className="text-xs text-muted">{briefing.caveats.join(" ")}</p></div>}
      </Panel>
      <Panel><CardHeader title="Open evidence and interoperability" description="Versioned JSON preserves canonical IDs, source references, supported claims and dispositions. No vendor integration is implied." /><Button variant="ghost" disabled={busy || !live} className="self-start" onClick={() => work(async () => {
        const result = await apiRequest<unknown>(`/enterprise/cases/${room.case.id}/evidence/v1?purpose=${encodeURIComponent(room.policy.purpose)}`, { headers });
        const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "evidence-bundle-v1.json"; a.click(); URL.revokeObjectURL(url); setMessage("Versioned evidence exported under your case permissions.");
      })}>Export evidence v1 (step-up required)</Button><p className="text-xs text-muted">Graph paths are explicitly unavailable in v1. Live exports require an authorized case lead or reviewer, permitted purpose and an aal2 token.</p></Panel>
    </>}
    {mode === "Operations" && <>
      <Panel><CardHeader title="Operational SLO dashboard" description={operationsLive ? "Last 24 hours of measured operation samples." : `Synthetic operation samples as of ${sample.as_of}. Unmeasured services remain unknown.`} actions={<Button variant="ghost" size="sm" disabled={busy || !live} onClick={() => work(async () => { const response = await apiRequest<{ metrics: typeof metrics }>("/enterprise/operations", { headers }); setMetrics(response.metrics); setOperationsLive(true); setMessage("Measured service samples loaded. Window: last 24 hours, capped at 5,000 samples."); })}>Refresh measured operations</Button>} /><p className="text-sm">At least ten samples are required for a health status. Samples older than one hour are stale. Targets are prototype settings.</p></Panel>
      <div className="grid gap-4 md:grid-cols-2">{metrics.map(m => <Panel key={m.operation}><CardHeader title={m.operation.replaceAll("_", " ")} actions={<Pill tone={m.status === "within_target" ? "ok" : m.status === "unknown" ? "mute" : "warn"}>{m.status.replaceAll("_", " ")}</Pill>} /><p className="text-sm">{m.samples} observations · p95 {m.p95_ms === null ? "unknown" : `${m.p95_ms.toFixed(0)} ms`}</p><p className="text-sm">Good operations: {m.good_ratio === null ? "unknown" : `${(m.good_ratio * 100).toFixed(1)}%`} · target {(m.success_target * 100).toFixed(1)}% within {m.max_ms} ms</p><p className="text-xs text-muted">Fallback: {m.fallback}</p></Panel>)}</div>
      <Panel><CardHeader title="Data drift, model drift and quality" description="Keep input changes separate from actual model performance and service failures." /><ButtonLink href="/governance/drift" className="self-start">Open monitoring checklist and comparisons</ButtonLink></Panel>
    </>}
    {mode === "Release and residency" && <>
      <Panel><CardHeader title="Signed configuration and reproducible releases" description="Pin source, prompts, schemas, rules, artifacts and policies. Verify against a separately trusted Ed25519 public key." /><Pill tone="mute">{release ? `${release.status} · ${release.signature}` : "No verified release loaded"}</Pill><p className="text-sm">The release tool hashes pinned files and can sign the manifest. An unsigned hash is labeled unsigned. Missing dataset or hosted-policy snapshots stay visible.</p><Button variant="ghost" disabled={busy || !live} className="self-start" onClick={() => work(async () => setRelease(await apiRequest("/enterprise/release", { headers })))}>Verify deployed release manifest</Button>{release?.caveat && <p className="text-xs text-muted">{release.caveat}</p>}<p className="text-xs text-muted">One-command reset/smoke instructions are in the enterprise runbook. Reset regenerates synthetic fixtures and never deletes tenant records.</p></Panel>
      <Panel><CardHeader title="Data residency and environment boundaries" description={environment ? `Declared region: ${environment.declared_region}. Approved destinations: ${environment.approved_destinations.join(", ")}.` : "Default policy: local host and Supabase only. Deployment regions must be verified separately."} actions={<Button variant="ghost" size="sm" disabled={busy || !live} onClick={() => work(async () => setEnvironment(await apiRequest("/enterprise/environment", { headers })))}>Load declared environment</Button>} />{(environment ? Object.entries(environment.locations).map(([asset, location]) => ({ asset, location })) : sample.residency).map(r => <div className="flex flex-wrap justify-between gap-2 border-t border-line py-2 text-sm" key={r.asset}><b>{r.asset.replaceAll("_", " ")}</b><span>{r.location}</span></div>)}<p className="text-xs text-muted">{environment?.caveat ?? "This is a declared deployment policy, not a cloud residency attestation. Enterprise export routes enforce it; independent agent processes require their own egress controls."}</p></Panel>
    </>}
  </div>;
}

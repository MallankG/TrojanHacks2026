"use client";

import { useLayoutEffect, useState } from "react";
import samples from "@/lib/seed/governance-demo.json";
import { ApiError, apiRequest } from "@/lib/api/client";
import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/card";
import { Field, Select, TextArea, TextInput } from "@/components/ui/field";
import { Pill } from "@/components/ui/pill";

type Sample = typeof samples.drift;
type Assessment = Sample["assessment"];
type Product = Sample["product"];
type Event = { id: string; kind: string; created_at: string; payload: Record<string, unknown> };
type Catalog = { id: string; definition: Product };
const sections = ["Contract and trust", "Metadata and impact", "Controls and catalog", "Steward and incidents"] as const;

function Json({ value }: { value: unknown }) {
  return <pre className="max-h-72 overflow-auto rounded-lg bg-subtle p-3 text-xs whitespace-pre-wrap break-words">{JSON.stringify(value, null, 2)}</pre>;
}

export function GovernanceSuperpowers() {
  const [section, setSection] = useState<(typeof sections)[number]>(sections[0]);
  const [scenario, setScenario] = useState<"drift" | "healthy">("drift");
  const [assessment, setAssessment] = useState<Assessment>(samples.drift.assessment);
  const [product, setProduct] = useState<Product>(samples.drift.product);
  const [input, setInput] = useState(JSON.stringify(samples.drift.input, null, 2));
  const [token, setToken] = useState("");
  const [catalog, setCatalog] = useState<Catalog[]>([]);
  const [productId, setProductId] = useState("");
  const [assessmentId, setAssessmentId] = useState("");
  const [events, setEvents] = useState<Event[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [field, setField] = useState("destination_account");
  const [question, setQuestion] = useState("which alerts depend on destination_account?");
  const [row, setRow] = useState("1");
  const [plan, setPlan] = useState<unknown>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [queryResult, setQueryResult] = useState<unknown>(null);
  const [reviewKind, setReviewKind] = useState("access_request");
  const [reviewDecision, setReviewDecision] = useState("requested");
  const [reviewRequestId, setReviewRequestId] = useState("");
  const [note, setNote] = useState("");
  const [owner, setOwner] = useState("");
  const [expiry, setExpiry] = useState("");
  // Next.js may preserve hidden pages; clear credentials when this page is deactivated.
  useLayoutEffect(() => () => { setToken(""); setProductId(""); setCatalog([]); setEvents([]); setAssessmentId(""); setProduct(samples.drift.product); setAssessment(samples.drift.assessment); setScenario("drift"); setPlan(null); setQueryResult(null); setPreviewKey(""); }, []);
  const live = Boolean(productId);
  const requestHeaders = { Authorization: `Bearer ${token}` };
  const currentQueryKey = JSON.stringify({ question, field, row, productId, assessmentId });

  async function work(task: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await task(); } catch (error) {
      const explanations: Record<number, string> = { 0: "Set NEXT_PUBLIC_API_BASE_URL to connect the backend.", 401: "Access token is missing, invalid or expired.", 403: "Your backend role or registered purpose does not permit this action.", 404: "The selected record was not found in your tenant.", 409: "This product or review decision already exists. Load the catalog or history.", 422: "Check the input, supported question, request selection and expiry (within 90 days).", 502: "The governance database is unavailable. Apply the governance migration and check the backend connection." };
      setMessage(error instanceof ApiError ? (explanations[error.status] ?? error.message) : error instanceof Error ? error.message : "Request failed");
    }
    finally { setBusy(false); }
  }
  function changeScenario(next: "drift" | "healthy") {
    setScenario(next); setProductId(""); setAssessmentId(""); setEvents([]);
    setProduct(samples[next].product); setAssessment(samples[next].assessment as Assessment);
    setInput(JSON.stringify(samples[next].input, null, 2)); setPlan(null); setPreviewKey(""); setQueryResult(null);
    setMessage("Showing a fixed synthetic example. No tenant records were changed.");
  }
  async function refresh(id: string) {
    const detail = await apiRequest<{ product: Catalog; events: Event[] }>(`/governance/products/${id}`, { headers: requestHeaders });
    setProduct(detail.product.definition); setProductId(id); setEvents(detail.events);
    const latest = detail.events.find(e => e.kind === "assessment");
    setAssessmentId(latest?.id ?? "");
    setReviewRequestId("");
    if (latest) setAssessment(latest.payload as unknown as Assessment);
    setPlan(null); setPreviewKey(""); setQueryResult(null);
  }
  async function query(execute: boolean) {
    if (!live) {
      const lower = question.toLowerCase().trim().replace(/\?$/, "");
      const parsedField = lower.startsWith("which alerts depend on ") ? lower.slice("which alerts depend on ".length) : null;
      const intent = parsedField ? "field_impact" : lower === "why was this row quarantined" ? "quarantine_reason" : null;
      if (!intent || (parsedField && !(parsedField in product.contract.fields))) throw new Error("Use one of the two supported questions with a contract field.");
      const preview = { intent, field: parsedField, row: Number(row), row_cap: 50, scope: "Fixed synthetic example", read_only: true };
      setPlan(preview); setPreviewKey(currentQueryKey);
      if (execute) setQueryResult(intent === "quarantine_reason" ? assessment.rows.filter(r => r.row === Number(row)) : downstream(parsedField!).filter(n => n.kind === "alert"));
      return;
    }
    const response = await apiRequest<{ plan: unknown; items?: unknown }>(`/governance/products/${productId}/steward-query`, {
      method: "POST", headers: requestHeaders,
      body: { question, row: Number(row), assessment_id: assessmentId || null, limit: 50, execute },
    });
    setPlan(response.plan); setPreviewKey(currentQueryKey); setQueryResult(response.items ?? null);
  }
  function downstream(selected: string) {
    const visited = new Set([`field:${selected}`]);
    let changed = true;
    while (changed) { changed = false; for (const edge of product.edges) if (visited.has(edge.source) && !visited.has(edge.target)) { visited.add(edge.target); changed = true; } }
    return product.nodes.filter(n => visited.has(n.id) && n.id !== `field:${selected}`);
  }

  return <div className="flex flex-col gap-4">
    <Panel>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="eyebrow">PRD 20.2 · Data governance</div><h2 className="text-xl font-semibold">Trust the data. Trace the decision.</h2><p className="mt-1 text-sm text-muted">Inspect contracts, dependencies and the receipts behind each quality decision.</p></div><Pill tone={live ? "ok" : "info"}>{live ? "Authenticated catalog" : "Fixed synthetic demo"}</Pill></div>
      <div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" disabled={busy} onClick={() => changeScenario("drift")}>Inspect broken feed</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => changeScenario("healthy")}>Compare healthy feed</Button><Button variant="ghost" size="sm" onClick={() => { const blob = new Blob([JSON.stringify(assessment, null, 2)], { type: "application/json" }); const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "governance-receipt.json"; a.click(); URL.revokeObjectURL(url); }}>Download receipt</Button></div>
      <p className="text-xs text-muted">{live ? "Tenant-scoped records. Raw source values are excluded from saved assessments." : `Precomputed ${scenario} example · evaluated ${assessment.trust.evaluated_at}. Freshness is evaluated at that time. Demo actions do not persist.`}</p>
      <details><summary className="cursor-pointer text-sm font-semibold">Connect a governed catalog</summary><div className="mt-3 flex flex-col gap-3">
        <Field label="Supabase access token" hint="Used in memory for this page only. The prototype sign-in does not supply a backend token.">{p => <TextInput {...p} type="password" autoComplete="off" value={token} onChange={e => { setToken(e.target.value); changeScenario("drift"); setCatalog([]); }} />}</Field>
        <div className="flex flex-wrap gap-2"><Button disabled={busy || !token} onClick={() => work(async () => { setCatalog(await apiRequest<Catalog[]>("/governance/products", { headers: requestHeaders })); setMessage("Catalog loaded. Select a product or register the synthetic example."); })}>Load catalog</Button><Button variant="ghost" disabled={busy || !token} onClick={() => work(async () => { const body = { ...samples.healthy.product, review_at: new Date(Date.now() + 30 * 86400000).toISOString() }; const created = await apiRequest<Catalog>("/governance/products", { method: "POST", headers: requestHeaders, body }); setCatalog(prev => [...prev, created]); await refresh(created.id); setMessage("Synthetic product registered. Run an assessment to create its first receipt."); })}>Register synthetic product</Button></div>
        {catalog.length > 0 && <Field label="Registered product">{p => <Select {...p} value={productId} onChange={e => { if (e.target.value) void work(() => refresh(e.target.value)); }}><option value="">Select product</option>{catalog.map(c => <option key={c.id} value={c.id}>{c.definition.name}</option>)}</Select>}</Field>}
      </div></details>
      <div role="status" aria-live="polite" className="text-sm">{busy ? "Working…" : message}</div>
    </Panel>
    <nav aria-label="Data governance capabilities" className="flex flex-wrap gap-2">{sections.map(s => <Button key={s} variant={section === s ? "dark" : "ghost"} size="sm" aria-pressed={section === s} onClick={() => setSection(s)}>{s}</Button>)}</nav>
    {live && !assessmentId && <Panel><p className="text-sm">This product has no saved assessment. Run checks below to create its first receipt. Select a synthetic scenario above to explore an example.</p></Panel>}
    {section === "Contract and trust" && <>
      {(!live || assessmentId) && <>
      <div className="grid gap-4 lg:grid-cols-2"><Panel><h2 className="text-base font-semibold">Executable contract · {product.contract.version}</h2><p className="text-sm">{assessment.summary.accepted} accepted / {assessment.summary.quarantined} quarantined / {assessment.summary.total} inspected</p><div className="overflow-x-auto"><table className="w-full text-left text-sm"><caption className="sr-only">Contract fields and validation rules</caption><thead><tr className="border-b border-line"><th className="py-2 pr-3">Field</th><th className="py-2 pr-3">Type</th><th className="py-2">Checks</th></tr></thead><tbody>{Object.entries(product.contract.fields).map(([name, rule]) => <tr key={name} className="border-b border-line"><td className="py-3 pr-3">{name}</td><td className="py-3 pr-3">{rule.kind}</td><td className="py-3 text-xs">{[rule.required ? "Required" : "Optional", rule.minimum !== null ? `Minimum ${rule.minimum}` : "", rule.maximum !== null ? `Maximum ${rule.maximum}` : "", rule.allowed.length ? `Accepted: ${rule.allowed.join(", ")}` : "", rule.kind === "timestamp" ? "Explicit timezone; within freshness window" : "", rule.sensitive ? "Sensitive; mask in display" : ""].filter(Boolean).join(" · ")}</td></tr>)}</tbody></table></div><p className="text-xs text-muted">Freshness window: {product.contract.freshness_hours} hours. Quality SLA: {product.contract.valid_sla * 100}% valid.</p><details><summary className="cursor-pointer text-xs">Inspect full contract definition</summary><Json value={product.contract} /></details><details><summary className="cursor-pointer text-sm font-semibold">Inspect quarantine reasons</summary><div className="mt-3 flex flex-col gap-2">{assessment.rows.filter(r => r.failures.length).map(r => <div className="border-t border-line pt-2 text-sm" key={r.row}><b>Row {r.row}</b>{r.failures.map((f, i) => <p key={i}>{f.field}: {f.code} → {f.action}</p>)}</div>)}{!assessment.summary.quarantined && <p className="text-sm">All inspected rows passed.</p>}</div></details></Panel>
      <Panel><h2 className="text-base font-semibold">Data trust score with a receipt</h2><p className="text-4xl font-semibold">{assessment.trust.score ?? "Unknown"}<span className="text-sm text-muted"> / 100</span></p><p className="text-xs text-muted">{assessment.trust.policy}</p><p className="text-xs text-muted">{assessment.trust.provenance_note}</p>{Object.entries(assessment.trust.components).map(([k, v]) => <div key={k} className="flex justify-between border-t border-line py-2 text-sm"><span className="capitalize">{k}</span><b>{v === null ? "Unknown" : `${Math.round(v * 100)}%`}</b></div>)}<p className="text-xs text-muted">Coverage {Math.round(assessment.trust.coverage * 100)}%. {assessment.trust.caveats.join(" · ")}</p><code className="break-all text-xs">SHA-256 {assessment.receipt_sha256}</code></Panel></div>
      <Panel><h2 className="text-base font-semibold">Schema and semantic drift</h2>{assessment.drift.length ? assessment.drift.map((d, i) => <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3 text-sm" key={i}><span><b>{d.field}</b> · {d.kind}{"proposed_mapping" in d && d.proposed_mapping ? ` · suggested source: ${d.proposed_mapping}` : ""}</span><Pill tone="warn">Review required</Pill></div>) : <p className="text-sm">No drift detected in this sample.</p>}<p className="text-xs text-muted">Mapping suggestions do not change canonical semantics. Distribution shifts use a median ratio heuristic with at least ten observations.</p></Panel>
      </>}
      {live && <Panel><h2 className="text-base font-semibold">Evaluate a bounded feed</h2><Field label="Assessment input (JSON)" hint="Up to 500 rows and 500 baseline rows. Source values stay out of saved receipts.">{p => <TextArea {...p} rows={8} value={input} onChange={e => setInput(e.target.value)} className="font-mono text-xs" />}</Field><Button disabled={busy} onClick={() => work(async () => { const response = await apiRequest<{ id: string; assessment: Assessment }>(`/governance/products/${productId}/assessments`, { method: "POST", headers: requestHeaders, body: { ...JSON.parse(input), purpose: product.purpose } }); setAssessment(response.assessment); setAssessmentId(response.id); setMessage("Assessment and correlated incident saved with an audit event."); })}>Run checks and save receipt</Button></Panel>}
    </>}
    {section === "Metadata and impact" && <>
      <Panel><h2 className="text-base font-semibold">Active metadata knowledge graph</h2><Field label="Changed source field">{p => <Select {...p} value={field} onChange={e => setField(e.target.value)}>{Object.keys(product.contract.fields).map(f => <option key={f}>{f}</option>)}</Select>}</Field><div className="flex flex-wrap items-center gap-2"><Pill tone="warn">{field}</Pill><span aria-hidden="true">→</span>{downstream(field).map(n => <Pill key={n.id} tone="mute">{n.kind}: {n.label}</Pill>)}</div><details><summary className="cursor-pointer text-sm">All registered nodes and relationships</summary><Json value={{ nodes: product.nodes, edges: product.edges }} /></details><p className="text-xs text-muted">{assessment.impact.coverage}</p></Panel>
      {(!live || assessmentId) && <><Panel><h2 className="text-base font-semibold">Lineage-aware remediation planner</h2><p className="text-sm">Current incident blast radius: {assessment.impact.total} registered dependencies.</p><ol className="list-decimal pl-5 text-sm">{assessment.remediation.steps.map(s => <li className="py-1" key={s}>{s}</li>)}</ol><Pill tone="warn">Human approval required</Pill><p className="text-xs text-muted">{assessment.remediation.execution}</p></Panel>
      <Panel><h2 className="text-base font-semibold">Retention and deletion lineage</h2><p className="text-sm">Retention class: {product.retention_days} days. A new record at assessment time expires {assessment.retention.new_record_expiry}.</p><p className="text-sm">{assessment.retention.downstream.total} registered downstream artifacts require expiry or rebuild review.</p><p className="text-xs text-muted">{assessment.retention.action} Demonstration only; external erasure is unverified.</p></Panel></>}
    </>}
    {section === "Controls and catalog" && <>
      <div className="grid gap-4 lg:grid-cols-2"><Panel><h2 className="text-base font-semibold">Governed data product catalog</h2>{[["Product", product.name], ["Owner", product.owner], ["Steward", product.steward], ["Purpose", product.purpose], ["Classification", product.classification], ["Access conditions", product.access_conditions], ["Review date", product.review_at], ["Example question", product.example_query]].map(([k,v]) => <div className="border-t border-line py-2 text-sm" key={k}><b>{k}</b><p>{v}</p></div>)}</Panel>{(!live || assessmentId) && <Panel><h2 className="text-base font-semibold">Sensitive-data discovery and minimization</h2>{assessment.sensitive.fields.map(f => <div className="flex justify-between gap-3 border-t border-line py-2 text-sm" key={f.field}><span>{f.field}</span><code>{f.display}</code></div>)}<p className="text-sm">{assessment.sensitive.document_matches} probable sensitive values found in the example document.</p>{assessment.sensitive.prompt_warning && <Pill tone="warn">Minimize fields before adding to a feature or prompt</Pill>}<p className="text-xs text-muted">{assessment.sensitive.policy}</p></Panel>}</div>
      {(!live || assessmentId) && <Panel><h2 className="text-base font-semibold">Reconciliation and source of truth</h2><p className="text-xs text-muted">{assessment.reconciliation.precedence}</p>{assessment.reconciliation.rows.map(r => <div className="flex flex-wrap justify-between gap-3 border-t border-line py-3 text-sm" key={r.currency}><b>{r.currency}</b><span>Accepted {r.incoming} · ledger {r.ledger} · difference {r.difference}</span><Pill tone={r.status === "matched" ? "ok" : "warn"}>{r.status}</Pill></div>)}<p className="text-xs text-muted">{assessment.reconciliation.caveats}</p></Panel>}
      <Panel><h2 className="text-base font-semibold">Governance control center</h2><p className="text-sm">Requests, exceptions, waivers and reviews are owner-assigned and expire within 90 days. Review approval records a decision; it does not grant database access or execute repairs.</p>
        <div className="grid gap-3 md:grid-cols-2"><Field label="Record type">{p => <Select {...p} value={reviewKind} onChange={e => setReviewKind(e.target.value)}>{["access_request", "exception", "waiver", "mapping", "remediation", "incident_note", "incident_resolution"].map(k => <option key={k}>{k}</option>)}</Select>}</Field><Field label="Decision">{p => <Select {...p} value={reviewDecision} onChange={e => setReviewDecision(e.target.value)}><option value="requested">Request review</option><option value="approved">Approve (reviewer)</option><option value="rejected">Reject (reviewer)</option></Select>}</Field><Field label="Assigned owner">{p => <TextInput {...p} value={owner} onChange={e => setOwner(e.target.value)} />}</Field><Field label="Expires at">{p => <TextInput {...p} type="datetime-local" value={expiry} onChange={e => setExpiry(e.target.value)} />}</Field></div>{reviewDecision !== "requested" && <Field label="Saved request to review">{p => <Select {...p} value={reviewRequestId} onChange={e => setReviewRequestId(e.target.value)}><option value="">Choose a saved request</option>{events.filter(e => e.kind === reviewKind && e.payload.decision === "requested").map(e => <option key={e.id} value={e.id}>{e.created_at} · {String(e.payload.owner)}</option>)}</Select>}</Field>}<Field label="Rationale or remediation note">{p => <TextArea {...p} rows={3} value={note} onChange={e => setNote(e.target.value)} />}</Field><Button disabled={busy || !live || !owner || note.length < 10 || !expiry || (reviewDecision !== "requested" && !reviewRequestId)} onClick={() => work(async () => { await apiRequest(`/governance/products/${productId}/reviews`, { method: "POST", headers: requestHeaders, body: { kind: reviewKind, decision: reviewDecision, request_id: reviewDecision === "requested" ? null : reviewRequestId, note, owner, expires_at: new Date(expiry).toISOString() } }); await refresh(productId); setMessage("Governance review recorded in the immutable event history."); })}>Save review record</Button>{!live && <p className="text-xs text-muted">Connect a catalog to save review records.</p>}</Panel>
    </>}
    {section === "Steward and incidents" && <>
      <Panel><h2 className="text-base font-semibold">Natural-language data steward</h2><p className="text-sm">Supported: “which alerts depend on destination_account?” and “why was this row quarantined?”</p><Field label="Question">{p => <TextInput {...p} value={question} onChange={e => setQuestion(e.target.value)} />}</Field><Field label="Row number (for quarantine questions)">{p => <TextInput {...p} type="number" min={1} max={500} value={row} onChange={e => setRow(e.target.value)} />}</Field><div className="flex flex-wrap gap-2"><Button variant="ghost" disabled={busy} onClick={() => work(() => query(false))}>Preview query plan</Button><Button disabled={busy || previewKey !== currentQueryKey} onClick={() => work(() => query(true))}>Execute previewed query</Button></div>{plan !== null && <Json value={plan} />}{queryResult !== null && <Json value={queryResult} />}<p className="text-xs text-muted">Typed, read-only operations. Up to 50 results. Live execution is tenant scoped and audited.</p></Panel>
      {(!live || assessmentId) && <Panel><h2 className="text-base font-semibold">Data-quality incident center</h2>{assessment.incidents.length ? assessment.incidents.map((incident, i) => <div className="flex flex-col gap-3" key={i}><h3 className="font-semibold">{incident.title}</h3><p className="text-sm">Assigned to {incident.owner} · {incident.status} · SLA due {incident.due_at}</p><ol className="border-l-2 border-line pl-4">{incident.timeline.map((entry, j) => <li key={j} className="py-2 text-sm"><b>{entry.event}</b><p className="text-xs text-muted">{entry.at}</p><p>{entry.rejected} quarantined rows · {entry.drift_signals} drift signals · {entry.affected_dependencies} affected dependencies</p></li>)}</ol><p className="text-sm">{incident.root_cause}</p></div>) : <p className="text-sm">No incident triggered by these checks.</p>}<p className="text-xs text-muted">Add remediation notes or resolution records through the control center. Notes preserve the original assessment.</p></Panel>}
      <Panel><h2 className="text-base font-semibold">Quality and decision history</h2>{events.length ? events.map(e => <details className="border-t border-line py-2" key={e.id}><summary className="cursor-pointer text-sm">{e.kind} · {e.created_at}{typeof e.payload.expires_at === "string" ? ` · expires ${e.payload.expires_at}` : ""}</summary><Json value={e.payload} /></details>) : <p className="text-sm">Connect a catalog to inspect saved assessments, reviews and incident notes.</p>}</Panel>
    </>}
  </div>;
}

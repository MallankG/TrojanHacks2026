"use client";

import { useEffect, useMemo, useState } from "react";
import { call, errorMessage, invalidateLive, liveApi, useLive } from "@/lib/live/api";
import { useLiveProfile } from "@/lib/live/session";
import { usePiiGuard } from "@/lib/privacy/use-pii-guard";
import { Button } from "@/components/ui/button";
import { Panel, CardHeader } from "@/components/ui/card";
import { Callout } from "@/components/ui/callout";
import { Field, Select, TextArea } from "@/components/ui/field";
import { Pill } from "@/components/ui/pill";
import { PiiWarning } from "@/components/ui/pii-warning";
import { SensitiveText } from "@/components/ui/sensitive";
import { friendlyModel } from "@/lib/live/format";

export interface Draft {
  summary: string;
  observed_facts: Array<{ text: string; claim_type: string; transaction_ids: string[]; account_ids: string[]; amounts: string[]; dates: string[]; citation_chunk_ids: string[] }>;
  citations: string[]; evidence_transaction_ids: string[]; prompt_version: string;
  uncertainties: string[];
  follow_up_questions: string[];
  provider: string;
  model: string;
  fallback_used: boolean;
}
export interface AgentRun {
  id: string;
  status: string;
  error_message: string | null;
  steps: Array<{ node: string; status: string; duration_ms: number }>;
  result: null | {
    draft?: Draft;
    governance_status?: string;
    halted?: boolean;
    alerts?: Array<{ alert_id: string }>;
    case_status?: string;
    verifier_errors?: Array<{ code: string; detail: string; claim_index: number | null }>;
    answer?: { answer: string; no_answer: boolean; provider: string; model: string; fallback_used: boolean; unknowns: string[] };
    sources?: Array<{ chunk_id: string; doc_title: string; excerpt: string; source_uri: string }>;
  };
}

function useAgentRun(initialId?: string) {
  const [run, setRun] = useState<AgentRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = run?.id ?? initialId;
  const active = !run || ["queued", "running"].includes(run.status);
  useEffect(() => {
    if (!id) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    let failures = 0;
    const load = async () => {
      try {
        const next = await call<AgentRun>(`/agents/runs/${id}`);
        if (stopped) return;
        setRun(next); setError(null); failures = 0;
        if (["queued", "running"].includes(next.status)) timer = setTimeout(load, 1500);
        else {
          for (const key of ["case:", "cases", "alerts", "dashboard", "pipelines", "audit:"]) invalidateLive(key);
        }
      } catch (e) { if (!stopped) { setError(errorMessage(e)); failures++; timer = setTimeout(load, Math.min(1500 * 2 ** failures, 15000)); } }
    };
    timer = setTimeout(load, 1000);
    return () => { stopped = true; clearTimeout(timer); };
  }, [id, active]);
  return { run, setRun, error, setError };
}

function RunReceipt({ run }: { run: AgentRun | null }) {
  const [auditMessage,setAuditMessage]=useState<string | null>(null);
  const [checking,setChecking]=useState(false);
  // Callers key this component by run id and status, so a new run or status starts with no audit message.
  if (!run) return null;
  const verify = async () => {
    setChecking(true);
    try {
      const receipt=await call<{ok:boolean;checked:number;reason:string | null}>(`/agents/runs/${run.id}/audit-verification`);
      setAuditMessage(receipt.checked===0 && receipt.ok ? "No audit receipts saved." : `${receipt.ok ? "Verified" : "Verification failed"}: ${receipt.checked} receipt(s). ${receipt.reason ?? ""}`);
    } catch(e){setAuditMessage(errorMessage(e));}finally{setChecking(false);}
  };
  const r = run.result;
  return <div className="mt-3 flex flex-col gap-3" aria-live="polite">
    <Pill tone={run.status === "failed" ? "warn" : "ok"}>{run.status.replaceAll("_", " ")}</Pill>
    {run.error_message ? <Callout tone="bad">{run.error_message}</Callout> : null}
    {r?.draft ? <>
      <p className="text-sm"><SensitiveText value={r.draft.summary} /></p>
      <p className="text-xs text-muted">{r.draft.fallback_used ? "Rule-based analysis engine · human review required" : "AI Agent Synthesis"} · {friendlyModel(r.draft.model)}</p>
      <ol className="flex list-decimal flex-col gap-3 pl-5 text-sm">{r.draft.observed_facts.map((c,i)=><li key={i}>
        <p><SensitiveText value={c.text} /></p><details><summary className="cursor-pointer text-xs font-semibold">Claim {i+1} · {c.claim_type} · Evidence references</summary>
          <p className="break-all text-xs">Transactions: {c.transaction_ids.join(", ") || "None"}</p>
          <p className="break-all text-xs">Accounts: {c.account_ids.join(", ") || "None"}</p>
          <p className="break-all text-xs">Amounts: {c.amounts.join(", ") || "None"} · Dates: {c.dates.join(", ") || "None"}</p>
          <p className="break-all text-xs">Policy chunks: {c.citation_chunk_ids.join(", ") || "None"}</p>
        </details></li>)}</ol>
      <p className="text-sm">Unknowns: <SensitiveText value={r.draft.uncertainties.join(" ")} /></p>
      <p className="text-sm">Follow up: <SensitiveText value={r.draft.follow_up_questions.join(" ")} /></p>
    </> : null}
    {r?.answer ? <>
      <p className="whitespace-pre-wrap text-sm"><SensitiveText value={r.answer.answer} /></p>
      <p className="text-xs text-muted">{r.answer.no_answer ? "No supported policy answer" : "Policy passages retrieved"} · {friendlyModel(r.answer.model)}{r.answer.fallback_used ? " · rule-based engine" : ""}</p>
      {r.answer.unknowns.map((u,i)=><p key={i} className="text-xs text-muted"><SensitiveText value={u} /></p>)}
      {r.sources?.map(s=><blockquote key={s.chunk_id} className="border-l-2 border-line pl-3 text-sm"><b>{s.doc_title}</b><p>{s.excerpt}</p><span className="break-all text-xs text-muted">{s.source_uri} · {s.chunk_id}</span></blockquote>)}
    </> : null}
    {r?.governance_status ? <Callout tone={r.halted ? "warn" : "ok"}>
      Governance: {r.governance_status}. {r.halted ? "Strict mode stopped analysis. Resolve quality issues or explicitly choose exploratory analysis." : `${r.alerts?.length ?? 0} alerts published from this batch. Inspect saved model runs for versions, availability and research-model limitations.`}
    </Callout> : null}
    {r?.verifier_errors?.length ? <Callout tone="warn"><b>Verifier flagged {r.verifier_errors.length} issue(s).</b><ul>{r.verifier_errors.map((e,i)=><li key={i}>{e.claim_index != null && e.claim_index >= 0 ? `Claim ${e.claim_index+1}` : "Narrative text"} · {e.code}: {e.detail}</li>)}</ul></Callout> : null}
    <details><summary className="cursor-pointer text-xs font-semibold">Saved workflow steps ({run.steps.length})</summary>
      <ol className="mt-2 text-xs">{run.steps.map((s,i)=><li key={i}>{s.node.replaceAll("_", " ")} · {s.status} · {Math.round(s.duration_ms)} ms</li>)}</ol>
      <p className="mt-1 break-all text-xs text-muted">Run {run.id}</p>
      <Button variant="ghost" size="sm" onClick={verify} disabled={checking || ["queued","running"].includes(run.status)}>Verify saved audit chain</Button>
      {auditMessage ? <p className="mt-2 text-xs" role="status">{auditMessage}</p> : null}
    </details>
  </div>;
}

export function CaseAgents({ caseId, initialRunId, closed }: { caseId: string; initialRunId?: string; closed: boolean }) {
  const profile = useLiveProfile();
  const { run, setRun, error, setError } = useAgentRun(initialRunId);
  const [busy,setBusy] = useState(false);
  const [action,setAction] = useState("request_more_data");
  const [reason,setReason] = useState("");
  const active = busy || !!run && ["queued","running","awaiting_review"].includes(run.status);
  const start = async () => {
    setBusy(true); setError(null);
    try { setRun(await call<AgentRun>(`/agents/cases/${caseId}/draft`,{method:"POST",body:{request_key:crypto.randomUUID()}})); }
    catch(e){setError(errorMessage(e));} finally {setBusy(false);}
  };
  const decide = async () => {
    if (!run) return;
    setBusy(true);setError(null);
    try {
      await call(`/agents/runs/${run.id}/decision`,{method:"POST",body:{action,reason:reason.trim()}});
      setRun({...run,status:"running"});
    } catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  const recover = async () => {
    if (!run) return;
    setBusy(true);setError(null);
    try {setRun(await call<AgentRun>(`/agents/runs/${run.id}/recover`,{method:"POST"}));}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  const reviewer = profile?.role === "admin" || profile?.role === "reviewer";
  return <Panel><CardHeader title="Investigation agents" description="Load evidence → retrieve policy → draft → verify → human review. Drafts, receipts and steps are saved with the case." />
    <Button onClick={start} disabled={closed || active || !profile || !["admin","analyst","investigator","reviewer"].includes(profile.role)}>Draft with investigation agents</Button>
    {error ? <Callout tone="bad" role="alert">{error}</Callout> : null}
    <RunReceipt key={`${run?.id}:${run?.status}`} run={run}/>
    {run?.result?.draft ? <DraftReviewEditor key={run.id} run={run} canSave={!closed && run.status==="awaiting_review"} onSaved={()=>setRun({...run,status:"completed"})}/> : null}
    {run && ["queued","running","failed"].includes(run.status) ? <Button variant="ghost" onClick={recover} disabled={busy}>Recover interrupted run</Button> : null}
    {run?.status === "awaiting_review" && !closed ? <div className="mt-4 flex flex-col gap-3">
      <Field label="Agent review decision">{p=><Select {...p} value={action} onChange={e=>setAction(e.target.value)}>
        <option value="request_more_data">Request more data</option>
        {reviewer ? <><option value="escalate">Escalate for review</option><option value="dismiss">Dismiss as false positive</option></> : null}
      </Select>}</Field>
      <Field label="Agent decision rationale">{p=><TextArea {...p} value={reason} onChange={e=>setReason(e.target.value)} rows={3}/>}</Field>
      <Button disabled={busy || reason.trim().length<10} onClick={decide}>Submit human decision</Button>
    </div> : null}
  </Panel>;
}

export function BatchAgents() {
  const profile=useLiveProfile();
  const batches=useLive(()=>liveApi.runs(),`agent-batches:${profile?.id ?? "demo"}`);
  const [batch,setBatch]=useState("");
  const [mode,setMode]=useState("strict");
  const {run,setRun,error,setError}=useAgentRun();
  const [busy,setBusy]=useState(false);
  const chosen=batch || batches.data?.items.find(r=>["completed","completed_with_errors"].includes(r.status))?.id || "";
  if (!profile) return null;
  const start=async()=>{
    setBusy(true);setError(null);
    try {setRun(await call<AgentRun>("/agents/analysis",{method:"POST",body:{request_key:crypto.randomUUID(),ingestion_run_id:chosen,mode}}));}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  return <div className="flex max-w-[520px] flex-col gap-2">
    <Field label="Analysis batch">{p=><Select {...p} value={chosen} onChange={e=>setBatch(e.target.value)}><option value="">Choose an uploaded batch</option>{batches.data?.items.filter(r=>["completed","completed_with_errors"].includes(r.status)).map(r=><option key={r.id} value={r.id}>{r.source_files?.filename ?? r.id} · {r.accepted_count} accepted</option>)}</Select>}</Field>
    <Field label="Governance mode">{p=><Select {...p} value={mode} onChange={e=>setMode(e.target.value)}><option value="strict">Strict — stop on quality failures</option><option value="exploratory">Exploratory — retain quality caveats</option></Select>}</Field>
    <Button onClick={start} disabled={!chosen || busy || !!run && ["queued","running"].includes(run.status) || !["admin","analyst"].includes(profile.role)}>Run analysis agents</Button>
    {error || batches.error ? <Callout tone="bad">{error || batches.error}</Callout> : null}<RunReceipt key={`${run?.id}:${run?.status}`} run={run}/>
  </div>;
}

export function LiveAgentCopilot() {
  const profile=useLiveProfile();
  const cases=useLive(()=>liveApi.cases(),`copilot-cases:${profile?.id}`);
  const [cid,setCid]=useState("");
  const [question,setQuestion]=useState("");
  const {run,setRun,error,setError}=useAgentRun();
  const [busy,setBusy]=useState(false);
  const chosen=cid || cases.data?.items.find(c=>c.status!=="closed")?.id || "";
  // Questions reach the language model, so personal data is held back until edited or redacted.
  const pii=usePiiGuard();
  const running=!!run && ["queued","running"].includes(run.status);
  const ask=async(text:string)=>{
    setBusy(true);setError(null);pii.clear();
    try{setRun(await call<AgentRun>(`/agents/cases/${chosen}/copilot`,{method:"POST",body:{request_key:crypto.randomUUID(),question:text}}));}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  const send=()=>{ const text=question.trim(); if(pii.check(text)) void ask(text); };
  const redactedBlocked=!chosen || busy || running ? "The copilot is unavailable right now." : pii.held && pii.held.substance<3 ? "Nothing would be left to ask once redacted; edit the question." : null;
  return <Panel><CardHeader title="Investigation copilot" description="Ask about policy for an authorized case. Answers cite retrieved guidance; transaction facts are verified in the case draft." />
    <div className="flex max-w-[800px] flex-col gap-4">
      <Field label="Copilot case">{p=><Select {...p} value={chosen} onChange={e=>setCid(e.target.value)}><option value="">Choose an open case</option>{cases.data?.items.filter(c=>c.status!=="closed").map(c=><option value={c.id} key={c.id}>{c.title}</option>)}</Select>}</Field>
      <Field label="Policy question">{p=><TextArea {...p} rows={3} value={question} onChange={e=>{setQuestion(e.target.value);pii.clear();}} placeholder="What should a reviewer document before escalating an alert?"/>}</Field>
      {pii.held ? <PiiWarning held={pii.held} sendBlocked={redactedBlocked} onSendRedacted={()=>{ if(pii.held && !redactedBlocked) void ask(pii.held.redacted); }} onEdit={pii.clear}/> : null}
      <Button onClick={send} disabled={!chosen || question.trim().length<3 || busy || running}>Ask investigation copilot</Button>
      {error || cases.error ? <Callout tone="bad">{error || cases.error}</Callout> : null}<RunReceipt key={`${run?.id}:${run?.status}`} run={run}/>
    </div>
  </Panel>;
}


function DraftReviewEditor({run,canSave,onSaved}:{run:AgentRun;canSave:boolean;onSaved:()=>void}) {
  const [draft,setDraft]=useState<Draft>(()=>structuredClone(run.result!.draft!));
  const [result,setResult]=useState<{passed:boolean;verifier_errors:Array<{code:string;detail:string;claim_index:number|null}>}|null>(null);
  const [message,setMessage]=useState(""); const [busy,setBusy]=useState(false);
  // One key per draft content: a retry of the same draft replays, an edited draft is a new save.
  const revisionKey=useMemo(()=>crypto.randomUUID(),[draft]);
  const check=async(candidate:Draft)=>{
    setBusy(true);setMessage("");
    try {setResult(await call(`/agents/runs/${run.id}/verify-draft`,{method:"POST",body:{draft:candidate}}));}
    catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}
  };
  const inject=()=>{
    const candidate=structuredClone(draft);
    candidate.observed_facts.unshift({text:"Transaction TX-UNSUPPORTED-DEMO moved $987654321.09.",claim_type:"fact",transaction_ids:["TX-UNSUPPORTED-DEMO"],account_ids:[],amounts:["987654321.09"],dates:[],citation_chunk_ids:[]});
    check(candidate);
    setMessage("Unsaved verification preview with an unsupported claim. The saved case and editor text are unchanged.");
  };
  const save=async()=>{
    setBusy(true);setMessage("");
    try {const r=await call<{version:number}>(`/agents/runs/${run.id}/revisions`,{method:"POST",body:{draft,request_key:revisionKey}});
      setMessage(`Human revision ${r.version} saved with a passed verifier receipt. Review it in Narratives, then use the case decision panel to accept, reject or escalate.`);
      for(const key of ["case:","cases","audit:"])invalidateLive(key);onSaved();
    }catch(e){setMessage(errorMessage(e));}finally{setBusy(false);}
  };
  return <details className="mt-4"><summary className="cursor-pointer font-semibold">Review draft and check evidence</summary>
    <div className="mt-3 flex flex-col gap-3">
      <p className="text-xs text-muted">Edit the summary or claim wording. Evidence references remain visible above. Checks run against actual case evidence; an unsupported preview is never saved.</p>
      <Field label="Edited narrative summary">{p=><TextArea {...p} rows={4} value={draft.summary} onChange={e=>{setDraft({...draft,summary:e.target.value});setResult(null);}}/>}</Field>
      {draft.observed_facts.map((c,i)=><Field key={i} label={`Edited claim ${i+1}`}>{p=><TextArea {...p} rows={2} value={c.text} onChange={e=>{setDraft({...draft,observed_facts:draft.observed_facts.map((v,j)=>j===i?{...v,text:e.target.value}:v)});setResult(null);}}/>}</Field>)}
      <div className="flex flex-wrap gap-2"><Button variant="ghost" onClick={()=>check(draft)} disabled={busy}>Check edited draft</Button>
      <Button variant="ghost" onClick={inject} disabled={busy}>Preview unsupported claim</Button>
      <Button onClick={save} disabled={busy || !canSave}>Save verified human revision</Button></div>
      {result ? <Callout tone={result.passed?"ok":"warn"}>{result.passed?"All evidence checks passed.":"Unsupported claims blocked."}<ul>{result.verifier_errors.map((e,i)=><li key={i}>{e.claim_index!=null && e.claim_index>=0?`Claim ${e.claim_index+1}`:"Narrative text"} · {e.code}: {e.detail}</li>)}</ul></Callout>:null}
      {message?<p role="status" className="text-sm">{message}</p>:null}
    </div>
  </details>;
}

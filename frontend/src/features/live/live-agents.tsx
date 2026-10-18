"use client";

import { useEffect, useState } from "react";
import { call, errorMessage, invalidateLive, liveApi, useLive } from "@/lib/live/api";
import { useLiveProfile } from "@/lib/live/session";
import { Button } from "@/components/ui/button";
import { Panel, CardHeader } from "@/components/ui/card";
import { Callout } from "@/components/ui/callout";
import { Field, Select, TextArea } from "@/components/ui/field";
import { Pill } from "@/components/ui/pill";

interface Draft {
  summary: string;
  observed_facts: Array<{ text: string }>;
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
    verifier_errors?: Array<{ code: string; message: string }>;
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
  useEffect(()=>setAuditMessage(null),[run?.id,run?.status]);
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
      <p className="text-sm">{r.draft.summary}</p>
      <p className="text-xs text-muted">{r.draft.fallback_used ? "Deterministic fallback · human review required" : "Model synthesis"} · {r.draft.provider} / {r.draft.model}</p>
      <ul className="list-disc pl-5 text-sm">{r.draft.observed_facts.map((c,i)=><li key={i}>{c.text}</li>)}</ul>
      <p className="text-sm">Unknowns: {r.draft.uncertainties.join(" ")}</p>
      <p className="text-sm">Follow up: {r.draft.follow_up_questions.join(" ")}</p>
    </> : null}
    {r?.answer ? <>
      <p className="whitespace-pre-wrap text-sm">{r.answer.answer}</p>
      <p className="text-xs text-muted">{r.answer.no_answer ? "No supported policy answer" : "Policy passages retrieved"} · {r.answer.provider} / {r.answer.model}{r.answer.fallback_used ? " · deterministic fallback" : ""}</p>
      {r.answer.unknowns.map((u,i)=><p key={i} className="text-xs text-muted">{u}</p>)}
      {r.sources?.map(s=><blockquote key={s.chunk_id} className="border-l-2 border-line pl-3 text-sm"><b>{s.doc_title}</b><p>{s.excerpt}</p><span className="break-all text-xs text-muted">{s.source_uri} · {s.chunk_id}</span></blockquote>)}
    </> : null}
    {r?.governance_status ? <Callout tone={r.halted ? "warn" : "ok"}>
      Governance: {r.governance_status}. {r.halted ? "Strict mode stopped analysis. Resolve quality issues or explicitly choose exploratory analysis." : `${r.alerts?.length ?? 0} alerts published from this batch. Graph risk is unavailable until a graph model is deployed.`}
    </Callout> : null}
    {r?.verifier_errors?.length ? <Callout tone="warn">Verifier flagged {r.verifier_errors.length} issue(s). Review the saved receipt before making a decision.</Callout> : null}
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
  const send=async()=>{
    setBusy(true);setError(null);
    try{setRun(await call<AgentRun>(`/agents/cases/${chosen}/copilot`,{method:"POST",body:{request_key:crypto.randomUUID(),question:question.trim()}}));}
    catch(e){setError(errorMessage(e));}finally{setBusy(false);}
  };
  return <Panel><CardHeader title="Investigation copilot" description="Ask about policy for an authorized case. Answers cite retrieved guidance; transaction facts are verified in the case draft." />
    <div className="flex max-w-[800px] flex-col gap-4">
      <Field label="Copilot case">{p=><Select {...p} value={chosen} onChange={e=>setCid(e.target.value)}><option value="">Choose an open case</option>{cases.data?.items.filter(c=>c.status!=="closed").map(c=><option value={c.id} key={c.id}>{c.title}</option>)}</Select>}</Field>
      <Field label="Policy question">{p=><TextArea {...p} rows={3} value={question} onChange={e=>setQuestion(e.target.value)} placeholder="What should a reviewer document before escalating an alert?"/>}</Field>
      <Button onClick={send} disabled={!chosen || question.trim().length<3 || busy || !!run && ["queued","running"].includes(run.status)}>Ask investigation copilot</Button>
      {error || cases.error ? <Callout tone="bad">{error || cases.error}</Callout> : null}<RunReceipt key={`${run?.id}:${run?.status}`} run={run}/>
    </div>
  </Panel>;
}

"""Run-local service adapters. Only canonical tenant data enters the agents package."""
import os
from urllib.parse import urlparse
from datetime import UTC, datetime
from decimal import Decimal
from threading import RLock
from uuid import uuid5, NAMESPACE_URL

from tracelayer_agents import api

from app.core.db import fetch_by_ids, get_db, get_one, insert_many, with_actor
from app.schemas import AlertBatch
from ml.graph import GraphLimits, NetworkXGraphAdapter
from app.services import alerts, cases
from app.services.detection import anomaly, config, supervised
from app.services.detection.detectors import run_rules
from app.services.detection.fusion import fuse
from app.services.detection.ledger import Ledger, Txn
from app.services.detection.run import _start_run, _finish_run, _write_signals

C = api.contracts


class Context:
    def __init__(self, user, run, token):
        if urlparse(os.getenv('OLLAMA_HOST','http://localhost:11434')).hostname not in ('localhost','127.0.0.1','::1'):
            raise ValueError('Live case synthesis requires an approved local Ollama endpoint')
        self.user, self.run, self.token = user, run, token
        self.tid, self.rid = user.tenant_id, run['id']
        self.rows = None
        self.case_bundle = None
        self.rules, self.anomalies, self.sup = [], [], []
        self.pending_alerts = {}
        self.excluded_no_usd = 0
        self.engine = None
        self.lock = RLock()

    def rpc(self, name, **params):
        return with_actor(get_db().rpc(name, {'p_tenant': self.tid, 'p_run': self.rid,
                                             'p_token': self.token, **params}), self.user).execute().data

    def transactions(self):
        if self.rows is None:
            if self.run.get('case_id'):
                bundle = cases.evidence_bundle(self.tid, self.run['case_id'])
                self.case_bundle = bundle
                self.rows = bundle['transactions']
            else:
                rows = get_db().table('transactions').select(cases.TXN_FIELDS).eq('tenant_id', self.tid).eq(
                    'ingestion_run_id', self.run['ingestion_run_id']).order('occurred_at').order('id').limit(501).execute().data
                if len(rows) > 500:
                    raise ValueError('Select a batch with at most 500 transactions for interactive analysis')
                self.rows = rows
        if not self.rows:
            raise ValueError('No canonical transactions are available in this scope')
        return self.rows

    def graph_engine(self):
        if self.engine is None:
            engine=NetworkXGraphAdapter(GraphLimits(max_hops=3,max_nodes=1000,max_edges=500,
                max_range_days=400,timeout_s=3,max_results=10))
            rows=self.transactions()
            engine.upsert_entities(self.tid,[{'entity_id':a,'type':'Account','attrs':{}} for a in sorted({r[k] for r in rows for k in ('origin_account_id','destination_account_id')})])
            loaded=engine.upsert_transactions(self.tid,[{**r,'transaction_id':r['id']} for r in rows
                if r['currency']=='USD' or r.get('amount_usd') is not None])
            if loaded['rejected']:raise ValueError('Canonical graph transactions were rejected')
            self.engine=engine
        return self.engine

    def snapshot(self):
        return 'db_' + api.canonical_hash(self.transactions())


class Ingestion:
    def __init__(self, ctx): self.ctx = ctx

    def ingest(self, payload, source_asset_id):
        if payload != self.ctx.run['ingestion_run_id']:
            raise ValueError('Batch does not belong to this run')
        r = get_one('ingestion_runs', self.ctx.tid, payload)
        if not r or r['status'] not in ('completed', 'completed_with_errors'):
            raise ValueError('A completed ingestion run is required')
        return C.IngestionResult(ingestion_run_id=r['id'], transaction_batch_id=r['id'],
            source_asset_id=r.get('governance_product_id') or source_asset_id, file_id=r['file_id'],
            status='partial' if r['rejected_count'] else 'completed', row_count=r['row_count'],
            accepted_count=r['accepted_count'], rejected_count=r['rejected_count'],
            started_at=r['started_at'], completed_at=r['completed_at'])


class Quality:
    def __init__(self, ctx): self.ctx = ctx

    def run_contract(self, batch_id):
        r = get_one('ingestion_runs', self.ctx.tid, batch_id)
        if not r: raise ValueError('Unknown batch')
        rows = get_db().table('data_quality_issues').select('*').eq('tenant_id', self.ctx.tid).eq('run_id', batch_id).limit(501).execute().data
        if len(rows) > 500: raise ValueError('Quality issue cap exceeded; review the batch before analysis')
        issues = [C.QualityIssue(check_id=x['id'], rule_id=x['rule_id'], asset_id=batch_id,
            severity='warning' if x['status']=='waived' else 'critical' if x['severity'] in ('critical','error') else x['severity'], affected_rows=1,
            sample_record_ids=[x['source_row_id']] if x['source_row_id'] else [], owner_role='data_owner',
            steward_role='data_steward', field_name=x['field_name'], issue_type=x['issue_type'],
            remediation_status={'open':'open','acknowledged':'in_progress','remediated':'resolved','waived':'accepted_risk'}[x['status']],
            remediation_note=x.get('remediation_note')) for x in rows if x['status']!='remediated']
        if r['rejected_count'] and not any(x.severity=='critical' for x in issues):
            issues.append(C.QualityIssue(check_id='rejected:'+batch_id,rule_id='excluded_source_rows',asset_id=batch_id,
                severity='warning',affected_rows=r['rejected_count'],owner_role='data_owner',steward_role='data_steward',
                remediation_note='Historical rejected rows remain outside canonical scoring; review replacement batches.'))
        status = 'fail' if any(x.severity == 'critical' for x in issues) else 'qualified' if issues else 'pass'
        product=get_one('governance_products',self.ctx.tid,r['governance_product_id']) if r.get('governance_product_id') else None
        version=(product or {}).get('definition',{}).get('contract',{}).get('version') or r['schema_version']
        return C.QualityReport(transaction_batch_id=batch_id, contract_version=version,
            status=status, issues=issues, rows_checked=r['row_count'], checked_at=datetime.now(UTC))


class Graph:
    def __init__(self, ctx): self.ctx = ctx

    def build_snapshot(self, batch_id):
        rows = self.ctx.transactions()
        self.ctx.graph_engine()
        return C.GraphSnapshot(graph_snapshot_id=self.ctx.snapshot(), transaction_batch_id=batch_id,
            feature_schema_version=config.ENGINE_VERSION,
            node_count=len({x[k] for x in rows for k in ('origin_account_id','destination_account_id')}),
            edge_count=len(rows), window_start=min(x['occurred_at'] for x in rows),
            window_end=max(x['occurred_at'] for x in rows), built_at=datetime.now(UTC))

    def get_transactions(self, tx_ids):
        rows = {r['id']: r for r in self.ctx.transactions()}
        if set(tx_ids) - rows.keys(): raise ValueError('Transaction is outside this workflow evidence scope')
        return [C.EvidenceTransaction(transaction_id=r['id'], source_row_ref=r['source_row_id'],
            timestamp=r['occurred_at'], amount=str(r['amount']), currency=r['currency'], channel=r['channel'],
            origin_account_id=r['origin_account_id'], destination_account_id=r['destination_account_id'],
            transaction_status=r['transaction_status']) for r in (rows[x] for x in tx_ids)]

    def subgraph(self, tx_ids, hops):
        if not 1 <= hops <= 3: raise ValueError('Hops must be 1 to 3')
        seed = self.get_transactions(tx_ids)
        accounts = {x for t in seed for x in (t.origin_account_id,t.destination_account_id)}
        selected = {}
        for _ in range(hops):
            for row in self.ctx.transactions():
                if accounts & {row['origin_account_id'],row['destination_account_id']}:
                    selected[row['id']] = row
            accounts |= {r[k] for r in selected.values() for k in ('origin_account_id','destination_account_id')}
        return C.SubgraphJSON(graph_snapshot_id=self.ctx.snapshot(), nodes=[C.SubgraphNode(account_id=a) for a in sorted(accounts)],
            edges=[C.SubgraphEdge(transaction_id=r['id'], origin_account_id=r['origin_account_id'],
                destination_account_id=r['destination_account_id'], amount=str(r['amount']), currency=r['currency'],
                timestamp=r['occurred_at']) for r in selected.values()])


class GraphRisk:
    def score(self, snapshot_id):
        return C.DetectorOutput(detector_name='graph_risk', model_version='unavailable',
                                status='unavailable', feature_snapshot_id=snapshot_id)


class Behavior:
    def __init__(self, ctx): self.ctx = ctx

    def score(self, snapshot_id):
        ctx = self.ctx
        rows = ctx.transactions()
        eligible = [r for r in rows if r['currency'] == 'USD' or r.get('amount_usd') is not None]
        ledger = Ledger([Txn.from_row(r) for r in eligible], excluded_no_usd=len(rows)-len(eligible))
        ctx.excluded_no_usd = ledger.excluded_no_usd
        ctx.rules = run_rules(ledger)
        ctx.anomalies = anomaly.amount_deviation(ledger)
        scored = supervised.score(ledger, {t for f in ctx.rules for t in f.evidence})
        ctx.sup = scored.findings
        groups = [('typology-rules', 'rule_engine', config.RULES_VERSION, ctx.rules),
                  ('robust-amount-baseline','anomaly',config.ANOMALY_VERSION,ctx.anomalies)]
        if scored.skipped_reason is None:
            groups.append(('xgboost','supervised',str(scored.model_card.get('model_version','unknown')),ctx.sup))
        outputs = []
        for name, kind, version, findings in groups:
            model = _start_run(ctx.user, name, kind, version, snapshot_id, {'agent_run_id':ctx.rid})
            try:
                _write_signals(ctx.user, model['id'], findings)
                _finish_run(ctx.user, model['id'], 'completed', {'signals':len(findings), 'excluded_no_usd':ledger.excluded_no_usd})
            except Exception:
                _finish_run(ctx.user, model['id'], 'failed', {}, 'Agent detector stage failed')
                raise
            scores, flags = {}, {}
            for f in findings:
                for t in f.evidence:
                    scores[t] = max(scores.get(t, 0), f.score)
                    if f.triggered: flags.setdefault(t, []).append(f.typology)
            outputs.append(C.DetectorOutput(detector_name=name, model_version=version, scores=scores,
                typology_flags={t:sorted(set(tags)) for t,tags in flags.items()}, feature_snapshot_id=snapshot_id))
        if scored.skipped_reason:
            outputs.append(C.DetectorOutput(detector_name='xgboost', model_version='unavailable', status='unavailable', feature_snapshot_id=snapshot_id))
        return outputs


class Fusion:
    def __init__(self, ctx): self.ctx = ctx

    def fuse(self, outputs):
        ctx = self.ctx
        model = _start_run(ctx.user, 'alert-fusion', 'ranker', config.POLICY_VERSION, ctx.snapshot(), {**config.snapshot(), 'agent_run_id':ctx.rid})
        try:
            rows = fuse(ctx.rules, ctx.anomalies, ctx.sup)
            if ctx.excluded_no_usd:
                for row in rows:
                    row['limitations'] += f' Data-quality caveat: {ctx.excluded_no_usd} non-USD transactions excluded because USD conversion is missing.'
            ctx.pending_alerts = {str(uuid5(NAMESPACE_URL, ctx.rid + ':' + str(i))):row for i,row in enumerate(rows)}
            _finish_run(ctx.user, model['id'], 'completed', {'alerts':len(rows), 'agent_run_id':ctx.rid})
        except Exception:
            _finish_run(ctx.user, model['id'], 'failed', {}, 'Agent fusion stage failed')
            raise
        return C.FusionResult(method=config.POLICY_VERSION, coefficients={},
            composite={t['transaction_id']:r['rank_score'] for r in rows for t in r['transactions']}, model_run_id=model['id'])

    def build_alerts(self, fusion, model_scores, state, cfg):
        return [C.Alert(alert_id=aid, tx_ids=[t['transaction_id'] for t in r['transactions']],
            composite_score=r['rank_score'], component_scores={c['signal_id']:c['score'] for c in r['component_signals']},
            typology_tags=[r['typology']], tier='high' if r['priority_band']=='critical' else r['priority_band'],
            quality_status=state['governance_status'], confidence_caveat=None if state['governance_status']=='pass' and not self.ctx.excluded_no_usd else 'incomplete_data',
            graph_snapshot_id=state['graph_snapshot_id'], model_versions={n:o.model_version for n,o in model_scores.items()},
            rank=r['priority_rank'], model_run_id=fusion.model_run_id) for aid,r in self.ctx.pending_alerts.items()]


class Flows:
    def __init__(self, ctx): self.ctx = ctx

    def investigate(self, alert, snapshot_id):
        engine=self.ctx.graph_engine()
        rows=self.ctx.transactions()
        by_id={r['id']:r for r in rows}
        seeds=sorted({by_id[t]['origin_account_id'] for t in alert.tx_ids if t in by_id})[:3]
        paths,cycles=[],[]
        caveats=[]
        truncated=False
        evidence=set(alert.tx_ids)
        for seed in seeds:
            result=engine.find_time_respecting_paths(self.ctx.tid,seed,hops=3,max_gap_hours=72,limit=10)
            caveats.extend(result.get('caveats',[]))
            truncated |= result.get('truncated',False)
            if result.get('truncation_reason'):caveats.append('Path search limited by '+result['truncation_reason'])
            for item in result['items']:
                hops=item['hops']
                evidence.update(h['transaction_id'] for h in hops)
                paths.append(C.FlowPattern(account_ids=[hops[0]['from']]+[h['to'] for h in hops],
                    transaction_ids=[h['transaction_id'] for h in hops],window_start=hops[0]['occurred_at'],window_end=hops[-1]['occurred_at']))
            result=engine.find_cycles_bounded(self.ctx.tid,account_id=seed,min_hops=3,max_hops=3,max_gap_hours=72)
            caveats.extend(result.get('caveats',[]))
            truncated |= result.get('truncated',False)
            if result.get('truncation_reason'):caveats.append('Cycle search limited by '+result['truncation_reason'])
            for item in result['items']:
                evidence.update(item['edge_ids'])
                cycles.append(C.FlowPattern(account_ids=item['accounts'],transaction_ids=item['edge_ids'],
                    window_start=item['first_at'],window_end=item['last_at']))
        return C.FlowFindings(alert_id=alert.alert_id,graph_snapshot_id=snapshot_id,
            paths=paths[:10],cycles=cycles[:10],evidence_tx_ids=sorted(evidence),truncated=truncated,
            query_caveats=list(dict.fromkeys(caveats+['Searches cover a bounded evidence graph; an empty result does not establish absence of a pattern.'])))



class Policy:
    def __init__(self, ctx): self.ctx = ctx

    def search(self, query, filters, top_k):
        rows = get_db().rpc('match_policy_chunks', {'query_embedding':None, 'query_text':query,
            'match_count':min(top_k, 8), 'filter_tenant_id':self.ctx.tid}).execute().data or []
        return [C.RetrievedChunk(chunk_id=r['chunk_id'], doc_title=r['title'], section=r['section'],
            source_uri=r['source_url'], excerpt=r['content'], score=r['rrf_score']) for r in rows]


class Audit:
    def __init__(self, ctx): self.ctx = ctx

    def record_trace(self, event):
        step = event.model_dump(mode='json')
        if step.get('error') and step['status'] == 'error':
            step['error'] = 'Node failed; see server logs with the run ID'
        self.ctx.rpc('append_workflow_step', p_step=step)

    def list_events(self, object_id=None):
        q = get_db().table('agent_chain_receipts').select('event').eq('tenant_id',self.ctx.tid).eq('run_id',self.ctx.rid).order('sequence')
        events = [C.AuditEvent.model_validate(r['event']) for r in q.limit(1000).execute().data]
        return [e for e in events if object_id is None or e.object_id==object_id]

    def last_hash(self):
        rows = get_db().table('agent_chain_receipts').select('hash').eq('tenant_id',self.ctx.tid).eq('run_id',self.ctx.rid).order('sequence',desc=True).limit(1).execute().data
        return rows[0]['hash'] if rows else None

    def append(self, event):
        # Parallel detector nodes share this adapter; serialize head read + append per run.
        with self.ctx.lock:
            prev = self.last_hash()
            event = event.model_copy(update={'prev_hash':prev, 'hash':api.hash_event(event,prev)})
            return C.AuditEvent.model_validate(self.ctx.rpc('append_agent_receipt', p_event=event.model_dump(mode='json')))


class Cases:
    def __init__(self, ctx): self.ctx = ctx

    def get_alert(self, alert_id):
        r = get_one('alerts',self.ctx.tid,alert_id)
        if not r: return None
        links = get_db().table('alert_transactions').select('transaction_id').eq('tenant_id',self.ctx.tid).eq('alert_id',alert_id).execute().data
        components = r.get('component_signals') or []
        return C.Alert(alert_id=r['id'], tx_ids=[x['transaction_id'] for x in links],
            composite_score=float(r['rank_score'] or 0), component_scores={str(x.get('signal_id',i)):float(x.get('score') or 0) for i,x in enumerate(components)} or {'unavailable':0},
            typology_tags=[r['typology']], tier='high' if r['priority_band']=='critical' else r['priority_band'],
            quality_status='qualified' if r['has_dq_caveat'] else 'pass', confidence_caveat='incomplete_data' if r['has_dq_caveat'] else None,
            graph_snapshot_id=self.ctx.snapshot(), model_versions={'ranking_policy':r['ranking_strategy'] or 'unknown'}, model_run_id=r['model_run_id'],rank=r['priority_rank'])

    def save_alerts(self, agent_alerts):
        batch = AlertBatch.model_validate({'model_run_id':agent_alerts[0].model_run_id,
            'alerts':[self.ctx.pending_alerts[a.alert_id] for a in agent_alerts]}) if agent_alerts else None
        if batch:
            alerts.validate_batch(self.ctx.user,batch)
            caveats = []
            if any(a.quality_status!='pass' for a in agent_alerts):
                caveats.append('Batch governance did not pass cleanly; findings require review with source-quality caveats')
            if self.ctx.excluded_no_usd:
                caveats.append(f'{self.ctx.excluded_no_usd} non-USD transactions excluded because USD conversion is missing')
            alerts.publish(self.ctx.user,batch,server_ids=[a.alert_id for a in agent_alerts],run_caveat='; '.join(caveats) or None)

    def save_evidence(self, bundle):
        # Analysis evidence is saved as a run-scoped artifact, not process memory.
        insert_many('agent_evidence_bundles',[{'tenant_id':self.ctx.tid,'run_id':self.ctx.rid,
            'alert_id':bundle.alert_id,'bundle':bundle.model_dump(mode='json')}],actor=self.ctx.user)

    def get_evidence(self, alert_id):
        alert = self.get_alert(alert_id)
        if not alert: return None
        allowed = {r['id'] for r in self.ctx.transactions()}
        ids = sorted(allowed) if self.ctx.run.get('case_id') else [x for x in alert.tx_ids if x in allowed]
        transactions = Graph(self.ctx).get_transactions(ids)
        if not transactions: raise ValueError('Alert has no evidence in this case')
        saved = get_db().table('agent_evidence_bundles').select('bundle').eq('tenant_id',self.ctx.tid).eq('alert_id',alert_id).limit(1).execute().data
        caveats = {q['check_id']:C.QualityIssue.model_validate(q) for r in saved for q in r['bundle'].get('quality_caveats',[])}
        for batch in {r['ingestion_run_id'] for r in self.ctx.transactions()}:
            for issue in Quality(self.ctx).run_contract(batch).issues:
                caveats[issue.check_id]=issue
        findings=Flows(self.ctx).investigate(alert.model_copy(update={'tx_ids':ids}),self.ctx.snapshot())
        limitations=list(findings.query_caveats)
        for a in (self.ctx.case_bundle or {}).get('alerts',[]):
            limitations.extend(x for x in (a.get('dq_caveat_notes'),a.get('limitations')) if x)
        fields = {'alert_id':alert_id, 'transactions':transactions,'quality_caveats':list(caveats.values()),
            'flow_findings':findings,'limitations':list(dict.fromkeys(limitations)),
            'lineage_refs':sorted({f"ingestion_run:{r['ingestion_run_id']}" for r in self.ctx.transactions()})}
        return C.EvidenceBundle(**fields,bundle_hash=api.canonical_hash(fields))

    def get_case(self, case_id):
        r=get_one('cases',self.ctx.tid,case_id)
        if not r:return None
        return C.CaseRecord(case_id=r['id'],alert_id=self.ctx.run['alert_id'],title=r['title'],
            status={'open':C.CaseStatus.OPEN,'in_review':C.CaseStatus.AWAITING_REVIEW,'pending_review':C.CaseStatus.AWAITING_REVIEW,
                'escalated':C.CaseStatus.ESCALATED,'closed':C.CaseStatus.DISMISSED}.get(r['status'],C.CaseStatus.OPEN),
            disposition=r.get('disposition'),assigned_to=r['assigned_to'],created_at=r['created_at'],updated_at=r['updated_at'])

    def create_case(self, case):
        raise ValueError('Open a case through the case workspace first')

    def update_case(self, case):
        raise ValueError('Case changes must use the atomic draft or human decision operation')

    def save_draft(self, case_id, draft):
        raise ValueError('A complete verifier receipt is required')

    def save_draft_receipt(self, case_id, draft, state):
        known = {t.transaction_id for t in state['evidence_bundle'].transactions}
        accounts = {a for t in state['evidence_bundle'].transactions for a in (t.origin_account_id,t.destination_account_id)}
        sources = {s.chunk_id:s for s in state.get('retrieved_sources',[])}
        claims=[]
        for i,c in enumerate(draft.observed_facts):
            if set(c.transaction_ids)-known or set(c.account_ids)-accounts or set(c.citation_chunk_ids)-sources.keys():
                raise ValueError('Draft references data outside its evidence bundle')
            claims.append({'text':c.text,'kind':'hypothesis' if c.claim_type=='hypothesis' else 'fact',
                'transactions':c.transaction_ids,'accounts':c.account_ids,'citations':c.citation_chunk_ids,
                'values':{'amounts':[str(x) for x in c.amounts],'dates':[str(x) for x in c.dates]},
                'verification':'unsupported' if i in state.get('flagged_claims',[]) else 'supported', 'notes':None})
        receipt={'attempt':state['draft_attempts'],'content':draft.summary,'provider':draft.provider,'model':draft.model,
            'prompt_version':draft.prompt_version,'errors':[e.model_dump(mode='json') for e in state.get('verifier_errors',[])],
            'claims':claims,'sources':[s.model_dump(mode='json') for s in sources.values()],
            'draft':draft.model_dump(mode='json'),'llm_meta':state['llm_meta'].model_dump(mode='json'),
            'bundle_hash':state['evidence_bundle'].bundle_hash,'flagged_claims':state.get('flagged_claims',[])}
        self.ctx.rpc('save_workflow_draft',p_receipt=receipt)

    def apply_decision(self, case_id, decision):
        if decision.actor_id != self.ctx.user.id:
            raise ValueError('Only the original reviewer can recover a pending decision')
        if decision.edited_summary:
            raise ValueError('Save human edits as a separate narrative before deciding')
        receipt=self.ctx.rpc('record_workflow_decision',p_actor=self.ctx.user.id,p_action=decision.action,p_reason=decision.reason)
        return {'before_hash':api.canonical_hash(receipt['before']),'after_hash':api.canonical_hash(receipt['after'])}


def services(ctx):
    return api.Services(ingestion=Ingestion(ctx),quality=Quality(ctx),graph=Graph(ctx),graph_risk=GraphRisk(),
        behavior=Behavior(ctx),fusion=Fusion(ctx),flows=Flows(ctx),policy=Policy(ctx),cases=Cases(ctx),audit=Audit(ctx))

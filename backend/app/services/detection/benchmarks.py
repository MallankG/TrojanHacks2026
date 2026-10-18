"""Serve temporal IBM artifacts on canonical, tenant-scoped edges, with explicit domain-shift caveats.

No identifiers are mapped onto benchmark accounts. Unseen-account static features are zeros.
Only observed history is supplied; no labels, future-window features or test-set thresholds are fitted.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from threading import RLock
import hashlib
import json
import time

from .detectors import Finding, _roles

ART = Path(__file__).resolve().parents[3] / 'ml/artifacts/ibm'
LOCK = RLock()  # GNN sampling uses the torch RNG; keep inference deterministic across requests.
CAVEAT = ('IBM HI-Small research model applied to a different feed: entity types and training-account history '
          'are unavailable; unknown static features are zero. Only history in the selected scope is visible. '
          'Dates, currencies and payment rails may differ from training. Scores are review context, not '
          'calibrated probabilities or the offline benchmark performance of this feed.')


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    card: dict = field(default_factory=dict)
    skipped_reason: str | None = None
    scored: int = 0


@lru_cache(maxsize=2)
def _scorer(name):
    if name == 'xgb_temporal':
        from ml.ibm.scorer import IBMScorer
        return IBMScorer(ART)
    from ml.ibm.gnn import IBMGNNScorer
    scorer = IBMGNNScorer(ART, name='gnn_temporal')
    if not scorer.ck.get('temporal'):
        raise ValueError('Only the strictly temporal GNN may be served')
    return scorer


def _frame(ledger):
    import pandas as pd
    rails = {'ach':'ACH','wire':'Wire','cash':'Cash','check':'Cheque','card':'Credit Card','crypto':'Bitcoin'}
    # Preserve original amount currency, rather than mislabelling converted amounts as training currencies.
    return pd.DataFrame({'ts':pd.to_datetime([t.at for t in ledger.txns],utc=True).tz_localize(None),
        'src':[t.origin for t in ledger.txns], 'dst':[t.destination for t in ledger.txns],
        'amt_paid':[float(t.amount) for t in ledger.txns], 'amt_recv':[float(t.amount) for t in ledger.txns],
        'cur_paid':[t.currency for t in ledger.txns], 'cur_recv':[t.currency for t in ledger.txns],
        'fmt':[rails.get(t.channel.lower(),'Unknown') for t in ledger.txns]})


def score(ledger, keep_ids, names=('xgb_temporal','gnn_temporal')):
    """Return separate model results. GNN is bounded to the same 500-edge interactive scope."""
    if not ledger.txns: return {name:Result(skipped_reason='No transactions') for name in ('xgb_temporal','gnn_temporal')}
    import pandas as pd
    e = _frame(ledger)
    accounts = pd.DataFrame({'Account Number':sorted(ledger.accounts()),'Entity Name':'Unknown'})
    results = {}
    for name in names:
        started = time.perf_counter()
        try:
            if name == 'gnn_temporal' and len(e)>500:
                raise ValueError('GNN interactive scoring cap is 500 edges; no sampled prefix was scored')
            with LOCK:
                scorer = _scorer(name)
                scored = scorer.score(e) if name=='xgb_temporal' else scorer.score(e,accounts).reindex(e.index)
            column = 'risk_score' if name=='xgb_temporal' else 'gnn_score'
            suffix = '.json' if name=='xgb_temporal' else '.pt'
            artifact = ART / f'ibm_{name}{suffix}'
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            version = f'ibm-{name}-{checksum[:12]}'
            card = {'model_name':name,'model_version':version,'feature_version':'ibm-strict-temporal-v1',
                'artifact_sha256':checksum,'threshold':float(scorer.threshold),
                'inference_policy':'per-target strictly prior graph context' if name=='gnn_temporal' else 'frozen training nodes and strictly prior scope features',
                'inference_ms':round(1000*(time.perf_counter()-started),2),'caveat':CAVEAT}
            findings=[]
            for i,t in enumerate(ledger.txns):
                value=float(scored.iloc[i][column])
                findings.append(Finding(detector=name,typology='model_anomaly',score=value,threshold=float(scorer.threshold),
                    strength=value,evidence=[t.id],accounts=_roles(t.origin,counterparties=[t.destination]),
                    window_start=t.at,window_end=t.at,amount_usd=t.usd,triggered=value>=scorer.threshold,
                    subject=t.origin,transaction_id=t.id,source='graph_model' if name.startswith('gnn') else 'supervised_model',
                    version=version,details={'reason':f'{name} score against validation-selected threshold',
                    'top_features':scored.iloc[i].get('top_features',[]),**card}))
            # Keep all corroborating evidence, then the highest scoring rows within the write cap.
            needed=[f for f in findings if f.transaction_id in keep_ids]
            extra=sorted((f for f in findings if f.transaction_id not in keep_ids),key=lambda f:-f.score)
            results[name]=Result(needed+extra[:max(0,300-len(needed))],card,scored=len(e))
        except (ImportError, OSError, ValueError, KeyError, RuntimeError) as exc:
            results[name]=Result(skipped_reason=f'{name} unavailable: {type(exc).__name__}')
    if all(n in results and results[n].skipped_reason is None for n in ('xgb_temporal','gnn_temporal')):
        try:
            from ml.ibm.ensemble import EnsembleScorer
            name='ensemble_gnn_temporal_xgb_temporal'
            card=json.loads((ART / f'ibm_{name}_results.json').read_text())
            first,second=card['models']
            scorer=EnsembleScorer(first,second,ART)
            manifest={'reference_sha256':hashlib.sha256((ART/f'ibm_{name}_ref.npz').read_bytes()).hexdigest(),
                'calibration_sha256':hashlib.sha256((ART/f'ibm_{name}_results.json').read_bytes()).hexdigest(),
                'component_versions':{n:results[n].card['model_version'] for n in card['models']},
                'weights':{first:scorer.w,second:1-scorer.w},'threshold':scorer.threshold,
                'reference_mode':'fixed_validation','calibration':scorer.calibration}
            version='ibm-ensemble-'+hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:12]
            by_model={n:{f.transaction_id:f for f in results[n].findings} for n in results}
            ids=sorted(by_model[first].keys() & by_model[second].keys())
            values=scorer.blend([by_model[first][t].score for t in ids],[by_model[second][t].score for t in ids])
            from dataclasses import replace
            findings=[replace(by_model[first][t],detector=name,source='supervised_model',score=float(v),strength=float(v),
                threshold=scorer.threshold,triggered=bool(v>=scorer.threshold),version=version,
                details={**manifest,'reason':'Validation-selected ensemble against fixed validation percentiles','caveat':CAVEAT})
                for t,v in zip(ids,values)]
            results['ensemble']=Result(findings,{**manifest,'model_version':version,'model_name':name},scored=len(e))
        except (OSError,KeyError,ValueError):
            pass
    return results

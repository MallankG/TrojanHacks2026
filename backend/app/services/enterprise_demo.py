"""Fixed synthetic examples used by the offline workspace and smoke check."""
from datetime import UTC, datetime, timedelta

from app.services.monitoring import DriftCheck, drift_report, slo_report


AS_OF = datetime(2026, 10, 18, 0, tzinfo=UTC)
CASE = '11111111-1111-4111-8111-111111111111'
LEAD = '22222222-2222-4222-8222-222222222222'
REVIEWER = '33333333-3333-4333-8333-333333333333'
EVIDENCE = '44444444-4444-4444-8444-444444444444'


def enterprise_demo():
    reference = []
    shifted = []
    for i in range(100):
        label = 1 if i % 5 == 0 else 0
        base = {'features': {'amount': float(100 + i * 10), 'channel': 'ach' if i < 80 else 'wire'}, 'score': .85 if label else .15, 'label': label}
        reference.append(base)
        shifted.append({'features': {'amount': None if i % 10 == 0 else float(1500 + i * 20), 'channel': 'crypto' if i < 40 else 'wire'},
                        'score': .1 if label else .9, 'label': label})
    request = {'reference_snapshot': 'synthetic-baseline-v1', 'current_snapshot': 'synthetic-shifted-v1', 'model_version': 'demo-ranker-v1',
               'feature_version': 'demo-features-v1', 'reference': reference, 'current': shifted, 'feature_types': {'amount': 'numeric', 'channel': 'categorical'}, 'top_k': 20}
    def report(current, name):
        body = dict(request, current=current, current_snapshot=name)
        result = drift_report(DriftCheck.model_validate(body))
        result['as_of'] = AS_OF.isoformat()
        return result
    delayed = [{**r, 'label': None} for r in shifted]
    metrics = [{'operation': op, 'duration_ms': 200 if i != 9 else 5000, 'success': i != 9 or op != 'policy_retrieval', 'observed_at': AS_OF.isoformat()}
               for op in ('graph_query','policy_retrieval','evidence_export','copilot_verification') for i in range(20)]
    return {'as_of': AS_OF.isoformat(), 'case_id': CASE, 'lead_id': LEAD, 'reviewer_id': REVIEWER,
            'members': [{'user_id': LEAD, 'case_role': 'lead'}, {'user_id': REVIEWER, 'case_role': 'reviewer'}],
            'evidence': [{'id': EVIDENCE, 'evidence_type': 'transaction', 'explanation': 'Synthetic transaction path; source rows retained'}],
            'policy': {'region': 'US', 'classification': 'confidential', 'purpose': 'investigation', 'review_at': (AS_OF + timedelta(days=7)).isoformat()},
            'events': [], 'drift_input': request,
            'drift': {'stable': report(reference, 'synthetic-stable-v1'), 'shifted': report(shifted, 'synthetic-shifted-v1'), 'delayed': report(delayed, 'synthetic-labels-pending-v1')},
            'operations': slo_report(metrics, AS_OF),
            'residency': [{'asset': 'Raw records', 'location': 'Supabase Storage and Postgres'}, {'asset': 'Embeddings', 'location': 'Supabase pgvector'},
                          {'asset': 'Prompts and schemas', 'location': 'Versioned repository'}, {'asset': 'Logs', 'location': 'Backend host; request bodies excluded'},
                          {'asset': 'Model artifacts', 'location': 'Backend storage / approved Supabase bucket'}, {'asset': 'Evidence exports', 'location': 'Local response / private case-exports bucket'}]}

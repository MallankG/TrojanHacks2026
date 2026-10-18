from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from app.api.routes import lineage
from app.core.security import CurrentUser, _apply_case_boundary
from app.services.case_visibility import authorized_case_ids

USER = CurrentUser('user', 'tenant', 'investigator', aal='aal1')
SETTINGS = SimpleNamespace(enterprise_enabled=True, case_authorization_enabled=True)
CASE = '11111111-1111-4111-8111-111111111111'


@pytest.mark.parametrize('method,suffix', [('POST', 'evidence-export'), ('GET', 'exports'), ('GET', 'export-url')])
@pytest.mark.parametrize('trailing', ['', '/'])
def test_every_signed_export_route_requires_export_access(method, suffix, trailing):
    request = Request({'type': 'http', 'method': method, 'path': f'/cases/{CASE}/{suffix}{trailing}', 'headers': []})
    with patch('app.core.security.get_settings', return_value=SETTINGS), patch('app.services.enterprise.require_case', side_effect=HTTPException(403, 'Step-up required')) as access:
        with pytest.raises(HTTPException):
            _apply_case_boundary(request, USER)
    access.assert_called_once_with(USER, CASE, 'export')


def test_signed_export_checks_residency_after_authorization():
    request = Request({'type': 'http', 'method': 'GET', 'path': f'/cases/{CASE}/exports', 'headers': []})
    with patch('app.core.security.get_settings', return_value=SETTINGS), patch('app.services.enterprise.require_case', return_value=({}, {'region': 'EU', 'classification': 'restricted'}, 'lead')), patch('app.services.enterprise.check_egress', side_effect=HTTPException(403, 'Region denied')) as egress:
        with pytest.raises(HTTPException):
            _apply_case_boundary(request, USER)
    egress.assert_called_once_with('supabase', 'EU', 'restricted', False)


def test_case_filter_propagates_unexpected_failures():
    with patch('app.services.case_visibility.get_settings', return_value=SETTINGS), patch('app.services.enterprise.require_case', side_effect=RuntimeError('Database unavailable')):
        with pytest.raises(RuntimeError):
            authorized_case_ids(USER, {'case'})


def test_case_filter_is_compatible_with_disabled_feature():
    with patch('app.services.case_visibility.get_settings', return_value=SimpleNamespace(enterprise_enabled=False, case_authorization_enabled=False)), patch('app.services.enterprise.require_case') as access:
        assert authorized_case_ids(USER, {'a', 'b'}) == {'a', 'b'}
        access.assert_not_called()


def test_lineage_hides_case_evidence_claims_and_decisions_from_nonmember():
    rows = {
        'risk_signals': [],
        'alert_transactions': [{'alert_id': 'alert', 'transaction_id': 'txn'}],
        'case_alerts': [{'case_id': 'allowed', 'alert_id': 'alert'}, {'case_id': 'denied', 'alert_id': 'alert'}],
        'case_evidence': [{'id': 'e1', 'case_id': 'allowed', 'explanation': 'public note'}, {'id': 'e2', 'case_id': 'denied', 'explanation': 'secret note'}],
        'narrative_claims': [{'id': 'c1', 'narrative_id': 'n1', 'claim_text': 'permitted claim'}, {'id': 'c2', 'narrative_id': 'n2', 'claim_text': 'secret claim'}, {'id': 'c3', 'narrative_id': 'n3', 'claim_text': 'unlinked secret claim'}],
        'case_narratives': [{'id': 'n1', 'case_id': 'allowed', 'version': 1}, {'id': 'n2', 'case_id': 'denied', 'version': 1}],
        'case_decisions': [{'id': 'd1', 'case_id': 'allowed', 'decided_at': '2026-10-18'}, {'id': 'd2', 'case_id': 'denied', 'decided_at': '2026-10-18'}],
        'cases': [{'id': 'allowed', 'title': 'Visible'}, {'id': 'denied', 'title': 'Secret'}],
        'alerts': [{'id': 'alert', 'model_run_id': 'model'}],
        'model_runs': [{'id': 'model'}],
    }

    class Query:
        def __init__(self, table): self.rows = list(rows[table])
        def select(self, *args): return self
        def eq(self, *args): return self
        def or_(self, *args): return self
        def ov(self, *args): return self
        def in_(self, key, values):
            self.rows = [r for r in self.rows if r[key] in values]
            return self
        def execute(self): return SimpleNamespace(data=self.rows)

    def fetch(table, tenant, ids, columns):
        return [r for r in rows[table] if r['id'] in ids]

    def authorize(user, case_id):
        if case_id == 'denied': raise HTTPException(403, 'Case membership required')

    db = SimpleNamespace(table=Query)
    with patch.object(lineage, 'get_db', return_value=db), patch.object(lineage, 'fetch_by_ids', side_effect=fetch), patch('app.services.case_visibility.get_settings', return_value=SETTINGS), patch('app.services.enterprise.require_case', side_effect=authorize):
        result = lineage._downstream_of_transactions(USER, ['txn'])
    assert [r['id'] for r in result['cases']] == ['allowed']
    assert [r['id'] for r in result['case_evidence']] == ['e1']
    assert [r['id'] for r in result['narratives']] == ['n1']
    assert [r['id'] for r in result['narrative_claims']] == ['c1']
    assert [r['id'] for r in result['decisions']] == ['d1']
    assert result['alerts'] == rows['alerts']
    assert result['model_runs'] == rows['model_runs']

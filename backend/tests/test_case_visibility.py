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


def test_nested_case_metadata_is_filtered_on_alert_and_account_profiles():
    from unittest.mock import MagicMock
    from uuid import UUID
    from app.api.routes import alerts, entity_profile
    visible = {'id': 'allowed', 'case_number': 1, 'title': 'Visible'}
    hidden = {'id': 'denied', 'case_number': 2, 'title': 'Secret'}
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [{'cases': visible}, {'cases': hidden}]
    with patch.object(alerts, 'get_db', return_value=db), patch.object(alerts, 'get_one', return_value={'id': CASE}), patch.object(alerts, 'authorized_case_ids', return_value={'allowed'}):
        result = alerts.get_alert(UUID(CASE), USER)
    assert result['cases'] == [{'cases': visible}]
    with patch.object(entity_profile, 'get_one', return_value={'id': CASE}), patch.object(entity_profile.entity_profile, 'account_profile', return_value={'related': {'cases': [visible, hidden]}}), patch.object(entity_profile, 'authorized_case_ids', return_value={'allowed'}):
        result = entity_profile.account_profile(UUID(CASE), as_of=None, observed_days=7, baseline_days=30, user=USER)
    assert result['related']['cases'] == [visible]


def test_global_search_excludes_inaccessible_case_matches():
    from app.api.routes import dashboard
    class Query:
        def __init__(self, table): self.table = table
        def __getattr__(self, name): return lambda *a, **k: self
        def execute(self):
            return SimpleNamespace(data=[{'id': 'allowed', 'case_number': 1, 'title': 'Visible', 'status': 'open'}, {'id': 'denied', 'case_number': 2, 'title': 'Secret', 'status': 'open'}] if self.table == 'cases' else [])
    with patch.object(dashboard, 'get_db', return_value=SimpleNamespace(table=Query)), patch.object(dashboard, 'authorized_case_ids', return_value={'allowed'}):
        result = dashboard.global_search('case', USER)
    assert [r['id'] for r in result['results']['cases']] == ['allowed']


def test_authorized_summary_paginates_using_only_authorized_total():
    from unittest.mock import MagicMock
    from app.services.case_visibility import authorized_case_rows
    db = MagicMock()
    db.rpc.return_value.execute.side_effect = [SimpleNamespace(data={'items': [{'id': str(i), 'status': 'open'} for i in range(500)], 'total': 501}), SimpleNamespace(data={'items': [{'id': 'last', 'status': 'closed'}], 'total': 501})]
    with patch('app.core.db.get_db', return_value=db):
        rows, total = authorized_case_rows(USER, 5000)
    assert len(rows) == total == 501
    assert db.rpc.call_args_list[1].args[1]['p_offset'] == 500
    assert db.rpc.call_args_list[1].args[1]['p_actor'] == USER.id


def test_notifications_use_authorized_case_count():
    from unittest.mock import MagicMock
    from app.api.routes import dashboard
    reviewer = CurrentUser('user', 'tenant', 'reviewer')
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.in_.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = []
    critical = db.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value
    critical.count = 0
    critical.data = []
    with patch.object(dashboard, 'get_db', return_value=db), patch.object(dashboard, 'case_authorization_enabled', return_value=True), patch.object(dashboard, 'authorized_case_rows', return_value=([], 0)) as visible, patch.object(dashboard.dash, 'latest_ranker', return_value=None):
        result = dashboard.notifications(reviewer)
    visible.assert_called_once_with(reviewer, dashboard.dash.NOTIFICATION_LIMIT, 'pending_review')
    assert result['items'] == []


def test_dashboard_case_summary_uses_authorized_statuses_only():
    from app.api.routes import dashboard
    def sections(*tasks):
        return {}, tasks[1](), ([], 0), {}, ({}, None)
    with patch.object(dashboard, 'get_settings', return_value=SimpleNamespace(review_sla_days=7)), patch.object(dashboard, 'case_authorization_enabled', return_value=True), patch.object(dashboard, 'authorized_case_rows', return_value=([{'status': 'open'}], 1)) as visible, patch.object(dashboard.dash, 'load_case_statuses', side_effect=AssertionError('Tenant-wide aggregate forbidden')), patch.object(dashboard.dash, 'parallel', side_effect=sections), patch.object(dashboard.dash, 'pipeline_flags', return_value={}):
        result = dashboard.dashboard_summary(None, USER)
    visible.assert_called_once_with(USER, dashboard.dash.MAX_SUMMARY_CASES)
    assert result['cases']['total'] == 1
    assert result['cases']['open_total'] == 1
    assert result['cases']['truncated'] is False

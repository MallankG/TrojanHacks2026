from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from app.main import app
from app.core.security import CurrentUser, get_current_user, _apply_case_boundary
from app.services.enterprise import check_access, RoomAction, canonical_hash, check_egress, handoff, room_event
from app.services.enterprise_demo import enterprise_demo, CASE, LEAD, REVIEWER, EVIDENCE
from app.services.monitoring import DriftCheck, drift_report, psi, slo_report, ServiceMetric
from scripts.release import create, verify

TENANT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
NOW = datetime.now(UTC)
USER = CurrentUser(LEAD, TENANT, 'investigator', aal='aal2')
CASE_ROW = {'id': CASE, 'tenant_id': TENANT, 'assigned_to': LEAD, 'created_by': LEAD, 'title': 'Case', 'status': 'open'}
ATTRS = {'region': 'US', 'clearance': 'restricted', 'purposes': ['investigation']}
POLICY = {'region': 'US', 'classification': 'confidential', 'purpose': 'investigation', 'review_at': NOW.isoformat()}


def test_abac_positive_and_step_up_export():
    assert check_access(USER, CASE_ROW, ATTRS, POLICY, None, 'export', 'investigation') == 'lead'


@pytest.mark.parametrize('change', ['tenant','region','clearance','purpose','member','service','aal','observer'])
def test_abac_denial_paths(change):
    user, case, attrs, policy, member = USER, dict(CASE_ROW), dict(ATTRS), dict(POLICY), None
    if change == 'tenant': case['tenant_id'] = CASE
    if change == 'region': attrs['region'] = 'EU'
    if change == 'clearance': attrs['clearance'] = 'internal'
    if change == 'purpose': attrs['purposes'] = ['marketing']
    if change == 'member': user = CurrentUser(REVIEWER, TENANT, 'reviewer', aal='aal2')
    if change == 'service': user = CurrentUser(None, TENANT, 'service')
    if change == 'aal': user = CurrentUser(LEAD, TENANT, 'investigator', aal='aal1')
    if change == 'observer': member = {'case_role': 'observer'}
    with pytest.raises(HTTPException) as error:
        check_access(user, case, attrs, policy, member, 'export', 'investigation')
    assert error.value.status_code == 403


def test_purpose_is_checked_against_recipient_and_export():
    with pytest.raises(HTTPException): check_access(USER, CASE_ROW, ATTRS, POLICY, None, 'export', 'marketing')
    with pytest.raises(HTTPException): check_access(USER, CASE_ROW, None, POLICY, None)
    assert check_access(USER, CASE_ROW, ATTRS, POLICY, {'case_role':'observer'}) == 'observer'
    with pytest.raises(HTTPException): check_access(USER, CASE_ROW, ATTRS, POLICY, {'case_role':'observer'}, 'write')


@pytest.mark.parametrize('body', [dict(kind='bookmark'),dict(kind='assignment'),dict(kind='vote',vote='support'),dict(kind='resolution',text='resolve'),dict(kind='comment',text='x')])
def test_room_action_semantics(body):
    with pytest.raises(ValidationError): RoomAction(client_id=EVIDENCE, expected_revision=0, **body)


def test_room_rejects_cross_case_evidence_before_rpc():
    body = RoomAction(client_id=EVIDENCE, expected_revision=0, kind='bookmark', evidence_ids=[EVIDENCE])
    db = MagicMock()
    with patch('app.services.enterprise.require_case'), patch('app.services.enterprise.get_db',return_value=db), patch('app.services.enterprise.get_one',return_value={'case_id': 'different-case'}):
        with pytest.raises(HTTPException): room_event(USER, CASE, body)
        db.rpc.assert_not_called()


def test_room_rejects_wrong_hypothesis_target():
    body = RoomAction(client_id=EVIDENCE, expected_revision=0, kind='vote', target_id=REVIEWER, vote='challenge')
    with patch('app.services.enterprise.require_case'), patch('app.services.enterprise.get_one',return_value={'case_id': CASE, 'kind':'comment'}):
        with pytest.raises(HTTPException): room_event(USER, CASE, body)


def test_room_rpc_has_idempotency_revision_actor_and_hash():
    body = RoomAction(client_id=EVIDENCE, expected_revision=7, kind='hypothesis', text='Consider a benign treasury sweep')
    db = MagicMock()
    with patch('app.services.enterprise.require_case'), patch('app.services.enterprise.get_db',return_value=db):
        room_event(USER, CASE, body)
    name, params = db.rpc.call_args.args
    assert name == 'append_case_room_event'
    assert params['p_actor'] == USER.id and params['p_tenant'] == TENANT
    assert params['p_expected'] == 7 and params['p_client'] == EVIDENCE
    assert params['p_hash'] == canonical_hash(params['p_payload'])


def test_handoff_omits_unverified_or_cross_case_claims_and_preserves_questions():
    claims = [{'id':'claim1','claim_text':'Observed transfer','claim_kind':'fact','verification':'supported','referenced_transaction_ids':[EVIDENCE]},
              {'id':'claim2','claim_text':'Unverified assertion','claim_kind':'fact','verification':'pending','referenced_transaction_ids':[EVIDENCE]},
              {'id':'claim3','claim_text':'Other case','claim_kind':'fact','verification':'supported','referenced_transaction_ids':[CASE]}]
    events = [{'id':'hyp1','kind':'hypothesis','payload':{'text':'May be payroll'}}, {'id':'conf1','kind':'conflict','payload':{'text':'Amounts differ'}}, {'id':'res1','kind':'resolution','payload':{'target_id':'conf1'}}]
    result = handoff(CASE_ROW, POLICY, events, claims, REVIEWER, {EVIDENCE})
    assert len(result['verified_developments']) == 1
    assert result['pending_questions'][0]['verification'] == 'unverified_collaborator_note'
    assert len(result['pending_questions']) == 1
    assert 'Unverified assertion' not in json.dumps(result)


def test_external_egress_region_and_real_data_denied():
    settings = SimpleNamespace(approved_data_destinations=['supabase','local','approved-model'], deployment_region='US')
    with patch('app.services.enterprise.get_settings',return_value=settings):
        assert check_egress('local','US','restricted',False)['allowed']
        for destination,region,classification,synthetic in [('random-model','US','internal',True),('supabase','EU','internal',True),('approved-model','US','internal',False),('approved-model','US','restricted',True)]:
            with pytest.raises(HTTPException): check_egress(destination,region,classification,synthetic)


def test_feature_routes_disabled_by_default():
    client = TestClient(app)
    assert client.get('/enterprise/operations').status_code == 404


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: USER
    with patch('app.api.routes.enterprise.get_settings',return_value=SimpleNamespace(enterprise_enabled=True)):
        yield TestClient(app)
    app.dependency_overrides.clear()


def test_case_room_denial_and_tenant_scoped_read(client):
    with patch('app.api.routes.enterprise.service.require_case',side_effect=HTTPException(403,'Denied')):
        assert client.get(f'/enterprise/cases/{CASE}/room').status_code == 403
    db = MagicMock()
    # Chain-independent empty page results keep the test focused on scope parameters.
    def chain():
        node=MagicMock(); node.select.return_value=node;node.eq.return_value=node;node.order.return_value=node;node.limit.return_value=node;node.execute.return_value.data=[]
        return node
    event_query, member_query, evidence_query = chain(), chain(), chain()
    db.table.side_effect=[event_query,member_query,evidence_query]
    with patch('app.api.routes.enterprise.service.require_case',return_value=(CASE_ROW,POLICY,'lead')), patch('app.api.routes.enterprise.get_db',return_value=db),patch('app.api.routes.enterprise.audit.record'):
        assert client.get(f'/enterprise/cases/{CASE}/room').status_code == 200
    assert ('tenant_id',TENANT) in [c.args for c in event_query.eq.call_args_list]
    assert ('case_id',CASE) in [c.args for c in event_query.eq.call_args_list]


def test_handoff_denies_unpermitted_recipient_before_evidence_read(client):
    with patch('app.api.routes.enterprise.service.require_case',side_effect=[(CASE_ROW,POLICY,'lead'), HTTPException(403,'Recipient denied')]), patch('app.api.routes.enterprise.get_one',return_value={'role':'reviewer'}), patch('app.api.routes.enterprise.get_db') as db:
        assert client.post(f'/enterprise/cases/{CASE}/handoff',json={'recipient_id': REVIEWER}).status_code == 403
        db.assert_not_called()


def test_existing_case_route_enforces_access_when_workspace_flag_is_off():
    from starlette.requests import Request
    request = Request({'type':'http','method':'GET','path':f'/cases/{CASE}/evidence-bundle','headers':[]})
    db=MagicMock(); db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data=[{'case_id':CASE}]
    settings=SimpleNamespace(enterprise_enabled=False,case_authorization_enabled=True)
    with patch('app.core.security.get_settings',return_value=settings),patch('app.core.security.get_db',return_value=db),patch('app.services.enterprise.require_case',side_effect=HTTPException(403,'Denied')):
        with pytest.raises(HTTPException): _apply_case_boundary(request,USER)


def test_stable_shifted_and_delayed_label_reports():
    demo=enterprise_demo()
    assert all(f['status']=='stable' for f in demo['drift']['stable']['features'])
    shifted=demo['drift']['shifted']
    assert shifted['features'][0]['status']=='quality_issue'
    assert shifted['performance']['status']=='review_degradation'
    delayed=demo['drift']['delayed']
    assert delayed['performance']['status']=='unknown_without_labels'
    assert delayed['performance']['current']['average_precision'] is None
    assert delayed['performance']['current']['brier'] is None


def test_reference_constant_and_support_changes_detected():
    assert psi([1.] * 50,[1.] * 50) == 0
    assert psi([1.] * 50,[10.] * 50) > .25
    assert psi([1.] * 50,[-10.] * 50) > .25


def test_sparse_and_single_class_labels_are_not_healthy():
    data=enterprise_demo()['drift_input']; data['current']=data['current'][:5]
    result=drift_report(DriftCheck.model_validate(data))
    assert result['prediction']['psi'] is None
    assert result['performance']['status']=='unknown_without_labels'
    for row in data['reference']: row['label']=0
    assert drift_report(DriftCheck.model_validate(data))['performance']['reference']['average_precision'] is None


def test_drift_bounds_types_and_nonfinite_values():
    data=enterprise_demo()['drift_input']
    data['current'][0]['features']['amount']='wrong type'
    with pytest.raises(ValidationError): DriftCheck.model_validate(data)
    data=enterprise_demo()['drift_input'];data['current'][0]['score']=float('nan')
    with pytest.raises(ValidationError): DriftCheck.model_validate(data)
    data=enterprise_demo()['drift_input'];data['current']*=11
    with pytest.raises(ValidationError): DriftCheck.model_validate(data)


def test_calibration_only_when_scores_are_declared_probabilities():
    data=enterprise_demo()['drift_input']
    assert drift_report(DriftCheck.model_validate(data))['performance']['current']['brier'] is None
    data['score_kind']='calibrated_probability'
    assert drift_report(DriftCheck.model_validate(data))['performance']['current']['brier'] > 0


def test_slo_unknown_breach_stale_and_p95():
    assert all(r['status']=='unknown' for r in slo_report([]))
    rows=[dict(operation='graph_query',duration_ms=100,success=True,observed_at=NOW.isoformat()) for _ in range(20)]
    assert next(r for r in slo_report(rows,NOW) if r['operation']=='graph_query')['status']=='within_target'
    rows[-1]['success']=False
    assert next(r for r in slo_report(rows,NOW) if r['operation']=='graph_query')['status']=='breach'
    assert next(r for r in slo_report(rows,NOW+timedelta(hours=2)) if r['operation']=='graph_query')['status']=='stale'


def test_signed_release_trusted_key_and_tamper_detection(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=Ed25519PrivateKey.generate()
    private=tmp_path/'key.pem'; public=tmp_path/'public.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
    root=tmp_path/'project'; (root/'frontend').mkdir(parents=True);(root/'frontend/package-lock.json').write_text('{}')
    package=create(root,private)
    assert verify(package,root,public)['signature']=='verified'
    with pytest.raises(ValueError): verify(package,root)
    changed=deepcopy(package);changed['manifest']['created_at']='tampered'
    with pytest.raises(ValueError): verify(changed,root,public)
    (root/'frontend/package-lock.json').write_text('{"changed":true}')
    with pytest.raises(ValueError): verify(package,root,public)


def test_manifest_path_traversal_and_unsigned_trust_rejected(tmp_path):
    package=create(tmp_path)
    with pytest.raises(ValueError): verify(package,tmp_path,tmp_path/'key.pem')
    package['manifest']['files']['../outside']='bad'
    import hashlib
    from scripts.release import canonical
    package['manifest_sha256']=hashlib.sha256(canonical(package['manifest'])).hexdigest()
    with pytest.raises(ValueError): verify(package,tmp_path)


def test_versioned_export_contract_hash_and_case_claim_filtering(client):
    from app.api.routes import enterprise as routes
    txn={'id':EVIDENCE,'occurred_at':NOW.isoformat(),'amount':'100.00','currency':'USD','origin_account_id':LEAD,'destination_account_id':REVIEWER,'source_row_id':CASE}
    evidence={'id':EVIDENCE,'evidence_type':'transaction','transaction_ids':[EVIDENCE],'account_id':None,'alert_id':None,'risk_signal_id':None}
    claims=[dict(id=LEAD,claim_text='Supported case transfer',claim_kind='fact',verification='supported',referenced_transaction_ids=[EVIDENCE]),
            dict(id=REVIEWER,claim_text='Different case transfer',claim_kind='fact',verification='supported',referenced_transaction_ids=[CASE])]
    class Query:
        def __init__(self, rows): self.rows=rows
        def select(self,*a,**k): return self
        def eq(self,*a,**k): return self
        def order(self,*a,**k): return self
        def limit(self,*a,**k): return self
        def execute(self): return SimpleNamespace(data=self.rows)
    db=MagicMock();db.table.side_effect=lambda table: Query({'case_evidence':[evidence],'case_narratives':[{'id':CASE}],'narrative_claims':claims,'case_decisions':[]}[table])
    def fetch(table,*args):
        return {'transactions':[txn],'alerts':[],'accounts':[{'id':LEAD,'is_internal':True,'status':'active'},{'id':REVIEWER,'is_internal':False,'status':'active'}]}[table]
    with patch.object(routes.service,'require_case',return_value=(CASE_ROW,POLICY,'lead')),patch.object(routes.service,'check_egress'),patch.object(routes,'get_db',return_value=db),patch('app.core.db.fetch_by_ids',side_effect=fetch),patch.object(routes.audit,'record'):
        result=client.get(f'/enterprise/cases/{CASE}/evidence/v1?purpose=investigation')
    assert result.status_code==200
    payload=result.json()
    checksum=payload.pop('manifest_sha256')
    assert canonical_hash(payload)==checksum
    assert payload['schema_version']=='evidence-bundle-v1'
    assert len(payload['claims'])==1
    assert 'Different case transfer' not in json.dumps(payload)


def test_failed_verification_is_measured_as_failure():
    from app.services.telemetry import observe_request
    request=SimpleNamespace(state=SimpleNamespace(actor=USER,operation_success=False),url=SimpleNamespace(path=f'/narratives/{CASE}/verification'))
    db=MagicMock()
    with patch('app.services.telemetry.get_db',return_value=db): observe_request(request,200,25,'request-id')
    payload=db.table.return_value.insert.call_args.args[0]
    assert payload['success'] is False
    assert payload['operation']=='copilot_verification'
    assert 'body' not in payload and 'headers' not in payload


def test_demo_reset_is_deterministic_and_scoped_to_fixtures(tmp_path):
    import scripts.release as release
    directory=tmp_path/'frontend/src/lib/seed';directory.mkdir(parents=True)
    untouched=tmp_path/'tenant-records.json';untouched.write_text('preserve')
    with patch.object(release,'ROOT',tmp_path):
        release.reset_demo()
        before={p.name:p.read_bytes() for p in directory.iterdir()}
        release.reset_demo()
        assert {p.name:p.read_bytes() for p in directory.iterdir()}==before
    assert untouched.read_text()=='preserve'


@pytest.mark.parametrize("resource", [CASE.replace("-", ""), "{" + CASE + "}", "urn:uuid:" + CASE])
def test_case_boundary_normalizes_all_fastapi_uuid_formats(resource):
    from starlette.requests import Request
    request = Request({"type":"http", "method":"GET", "path":f"/cases/{resource}/export-url", "headers":[]})
    settings = SimpleNamespace(enterprise_enabled=True, case_authorization_enabled=True)
    with patch("app.core.security.get_settings",return_value=settings), patch("app.services.enterprise.require_case",side_effect=HTTPException(403,"Denied")) as access:
        with pytest.raises(HTTPException): _apply_case_boundary(request,USER)
    access.assert_called_once_with(USER,CASE,"export")

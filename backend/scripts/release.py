"""Pin release inputs, sign with Ed25519, verify bytes/signature, and reset only synthetic demo outputs."""
import argparse
import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))

PINNED = ["frontend/package-lock.json", "frontend/src/lib/seed/enterprise-demo.json", "frontend/src/lib/seed/governance-demo.json", "backend/requirements.txt", "agents/pyproject.toml", "backend/app/schemas.py",
          "backend/app/services/enterprise.py", "backend/app/services/monitoring.py", "backend/app/schemas_enterprise.py", "backend/app/core/config.py",
          "backend/app/services/canonical.py", "backend/app/services/quality.py", "backend/ml/tracelayer/features.py", "backend/ml/ibm/rules.py", "backend/ml/artifacts/tracelayer/model_card.json",
          "backend/ml/artifacts/tracelayer/xgboost.json", "backend/ml/artifacts/tracelayer/gnn.pt",
          "backend/ml/artifacts/tracelayer/isolation_forest.joblib", "backend/ml/artifacts/ibm/ibm_xgb_temporal.json",
          "docs/trace_layer_prd.md", "backend/ml/data/manifest.json", "backend/policy-manifest.json", "backend/runtime-config-manifest.json"]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def pin_paths(root):
    paths = PINNED + [str(p.relative_to(root)) for p in (root / 'agents/tracelayer_agents/llm/prompts').glob('*.md')]
    paths += [str(p.relative_to(root)) for p in (root / 'supabase/migrations').glob('*.sql')]
    return sorted(set(paths))


def create(root=ROOT, signing_key=None):
    paths = pin_paths(root)
    manifest = {'schema_version': 'release-v1', 'created_at': datetime.now(UTC).isoformat(),
                'files': {p: digest(root / p) for p in sorted(set(paths)) if (root / p).is_file()},
                'unavailable': [p for p in sorted(set(paths)) if not (root / p).is_file()],
                'caveats': ['Pins local source/artifact bytes, not the contents of a hosted database.', 'Dataset snapshots, hosted policy versions and runtime environment must be pinned separately before exact replay.']}
    result = {'manifest': manifest, 'manifest_sha256': hashlib.sha256(canonical(manifest)).hexdigest(), 'signature': None, 'signing': 'unsigned'}
    if signing_key:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = serialization.load_pem_private_key(Path(signing_key).read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError('An Ed25519 private key is required')
        result.update(signature=base64.b64encode(key.sign(canonical(manifest))).decode(), signing='Ed25519')
    return result


def verify(package, root=ROOT, public_key=None):
    manifest = package['manifest']
    if set(manifest['files']) | set(manifest['unavailable']) != set(pin_paths(root)):
        raise ValueError('Release input set changed; regenerate and re-approve the manifest')
    if any((root / name).is_file() for name in manifest['unavailable']):
        raise ValueError('A previously unavailable input is now present; create a new release')
    if hashlib.sha256(canonical(manifest)).hexdigest() != package['manifest_sha256']:
        raise ValueError('Manifest hash mismatch')
    for name, checksum in manifest['files'].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or digest(path) != checksum:
            raise ValueError('Pinned file changed or unavailable: ' + name)
    if package.get('signature'):
        if not public_key:
            raise ValueError('Supply a trusted public key; the manifest cannot choose its own trust anchor')
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = serialization.load_pem_public_key(Path(public_key).read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError('An Ed25519 public key is required')
        key.verify(base64.b64decode(package['signature'], validate=True), canonical(manifest))
    elif public_key:
        raise ValueError('A signed release is required when a trusted key is configured')
    return {'verified_files': len(manifest['files']), 'signature': 'verified' if package.get('signature') else 'unsigned', 'unavailable': manifest['unavailable']}


def reset_demo():
    # Regenerate only checked-in synthetic fixtures. Never truncate/delete tenant data.
    from app.services.governance import demo
    from app.services.enterprise_demo import enterprise_demo, AS_OF
    (ROOT / 'frontend/src/lib/seed/governance-demo.json').write_text(json.dumps({s: demo(s, now=AS_OF) for s in ('healthy','drift')}, indent=2))
    (ROOT / 'frontend/src/lib/seed/enterprise-demo.json').write_text(json.dumps(enterprise_demo(), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'verify', 'reset-demo', 'smoke'])
    parser.add_argument('--manifest', type=Path, default=ROOT / 'backend/release-manifest.json')
    parser.add_argument('--signing-key', type=Path)
    parser.add_argument('--public-key', type=Path)
    args = parser.parse_args()
    if args.action == 'create':
        args.manifest.write_text(json.dumps(create(signing_key=args.signing_key), indent=2))
        print('Release inputs pinned. Signing:', 'Ed25519' if args.signing_key else 'unsigned (no key configured)')
    elif args.action == 'verify':
        print(json.dumps(verify(json.loads(args.manifest.read_text()), public_key=args.public_key)))
    elif args.action == 'reset-demo':
        reset_demo()
        verify(create())
        print('Synthetic fixtures regenerated and release inputs checked; tenant data unchanged.')
    else:
        from app.services.enterprise_demo import enterprise_demo
        from app.services.monitoring import DriftCheck, drift_report
        sample = enterprise_demo()
        assert drift_report(DriftCheck.model_validate(sample['drift_input']))['performance']['status'] == 'review_degradation'
        verify(create())
        print('Release inputs and synthetic monitoring smoke checks passed.')


if __name__ == '__main__':
    main()

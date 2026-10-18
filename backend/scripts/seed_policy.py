"""Index reviewed public excerpts through the authenticated API; never put a service-role key in a client.

python scripts/seed_policy.py --api http://127.0.0.1:8000
Set POLICY_SEED_TOKEN to an admin's access token. Uses tenant documents by default.
Re-running revises chunks in place through the normal indexing endpoint.
"""
import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

import httpx


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--api',default='http://127.0.0.1:8000')
    a=ap.parse_args()
    token=os.environ.get('POLICY_SEED_TOKEN')
    if not token: raise SystemExit('Set POLICY_SEED_TOKEN to the signed-in policy administrator access token')
    docs=json.loads((Path(__file__).resolve().parents[1]/'policy_corpus/public_guidance.json').read_text())
    with httpx.Client(base_url=a.api,headers={'Authorization':'Bearer '+token},timeout=120) as client:
        for doc in docs:
            body={k:v for k,v in doc.items() if k not in ('section','content','scope_note')}
            body.update(shared=False,retrieved_at=datetime.now(UTC).isoformat(),document_type='public-guidance-excerpt',
                embedding_model=os.getenv('POLICY_EMBEDDING_MODEL','nomic-embed-text'),
                chunks=[{'chunk_index':0,'section':doc['section'], 'content':doc['content']+'\n\nScope: '+doc['scope_note']}])
            response=client.post('/policy/documents',json=body)
            if response.is_error: raise SystemExit(f'Indexing failed ({response.status_code}); check API logs and local embedding availability')
            print(doc['publisher']+': indexed')

if __name__=='__main__': main()

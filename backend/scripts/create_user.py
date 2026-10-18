"""Create a confirmed Supabase user and provision their profile in a tenant (bootstrap for local testing).

    cd backend
    python -m scripts.create_user --email analyst@demo.test --password 'long-password' --role analyst
    python -m scripts.create_user --email reviewer@demo.test --password 'long-password' --role reviewer

Once an admin exists, use POST /admin/users instead. Uses the service-role key from backend/.env.
"""

import argparse
import sys

from app.core.db import get_db
from app.core.security import USER_ROLES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", required=True, choices=USER_ROLES)
    parser.add_argument("--name", help="Display name (defaults to the email)")
    parser.add_argument("--tenant", default="demo-bank", help="Tenant slug (default: demo-bank)")
    args = parser.parse_args()

    db = get_db()
    tenant = db.table("tenants").select("id, name").eq("slug", args.tenant).limit(1).execute().data
    if not tenant:
        print(f"Unknown tenant slug '{args.tenant}'", file=sys.stderr)
        return 1

    created = db.auth.admin.create_user({
        "email": args.email, "password": args.password, "email_confirm": True,
        "user_metadata": {"display_name": args.name or args.email},
    })
    # The on_auth_user_created trigger inserted a tenant-less profile; provision it.
    updated = db.table("profiles").update({
        "tenant_id": tenant[0]["id"], "role": args.role, "display_name": args.name or args.email,
    }).eq("id", created.user.id).execute().data
    if not updated:
        # No profile row (trigger missing): undo the auth user so the email can be retried.
        db.auth.admin.delete_user(created.user.id)
        print("Profile was not created for the new user; the auth user was removed. Check the "
              "on_auth_user_created trigger.", file=sys.stderr)
        return 1
    print(f"Created {args.email} as {args.role} in {tenant[0]['name']} ({created.user.id})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

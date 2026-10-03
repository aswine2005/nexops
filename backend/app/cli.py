"""Administrative commands.

    python -m app.cli create-user --email you@example.com --name "You" --role admin
    python -m app.cli sign-webhook payload.json      # prints the X-NexOps-Signature header value
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password, sign_payload
from app.database.session import session_scope
from app.models import User


def create_user(args: argparse.Namespace) -> int:
    password = args.password or getpass.getpass("Password: ")
    if len(password) < 10:
        print("Password must be at least 10 characters.", file=sys.stderr)
        return 1
    with session_scope() as session:
        existing = session.scalars(select(User).where(User.email == args.email.lower())).first()
        if existing:
            existing.password_hash = hash_password(password)
            existing.role = args.role
            existing.display_name = args.name or existing.display_name
            print(f"Updated {args.email}")
        else:
            session.add(User(email=args.email.lower(), display_name=args.name or args.email, password_hash=hash_password(password), role=args.role))
            print(f"Created {args.role} {args.email}")
    return 0


def sign_webhook(args: argparse.Namespace) -> int:
    secret = get_settings().webhook_secret
    if not secret:
        print("NEXOPS_WEBHOOK_SECRET is not set.", file=sys.stderr)
        return 1
    print(sign_payload(secret, Path(args.file).read_bytes()))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    user = sub.add_parser("create-user", help="Create or update a NexOps user")
    user.add_argument("--email", required=True)
    user.add_argument("--name")
    user.add_argument("--role", choices=["admin", "operator", "viewer"], default="operator")
    user.add_argument("--password", help="Omit to be prompted")
    user.set_defaults(func=create_user)
    sign = sub.add_parser("sign-webhook", help="Compute the HMAC signature for a webhook body")
    sign.add_argument("file")
    sign.set_defaults(func=sign_webhook)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

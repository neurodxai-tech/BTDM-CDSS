"""
Administrator command line, run inside the API container:

    docker compose exec api python -m app.manage users
    docker compose exec api python -m app.manage reset-password rad-002
    docker compose exec api python -m app.manage delete-user rad-002
    docker compose exec api python -m app.manage delete-user rad-002 --with-studies
    docker compose exec api python -m app.manage disable rad-002
    docker compose exec api python -m app.manage enable rad-002
    docker compose exec api python -m app.manage registrations
    docker compose exec api python -m app.manage delete-registration REG-20260930-3F2A1C
    docker compose exec api python -m app.manage purge-registrations --status rejected

Generated user IDs continue from the highest remaining number of each role,
so deleting test accounts (e.g. rad-002, rad-003) makes the next approval
reuse rad-002.

Every action is written to the audit log (actor "cli").
"""
from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app import db
from app.services import audit, storage
from app.services import auth as auth_service


def _find(username: str):
    with db.session_scope() as s:
        row = s.scalar(select(db.User).where(db.User.username == username.strip().lower()))
        return (row.id, row.username, row.role) if row else None


def cmd_users(_):
    users = auth_service.list_users()
    print(f"{'USER ID':<14}{'ROLE':<22}{'ACTIVE':<8}{'STUDIES':<9}NAME")
    for u in users:
        print(f"{u.username:<14}{u.role:<22}{'yes' if u.active else 'no':<8}{storage.count_studies(u.id):<9}{u.name}")


def cmd_registrations(_):
    with db.session_scope() as s:
        rows = s.scalars(select(db.RegistrationRequest).order_by(db.RegistrationRequest.submitted_at)).all()
        print(f"{'REFERENCE':<22}{'STATUS':<10}{'USER ID':<10}NAME")
        for r in rows:
            print(f"{r.id:<22}{r.status:<10}{(r.username or '-'):<10}{r.full_name}")


def cmd_reset_password(a):
    found = _find(a.username)
    if not found:
        sys.exit(f"No user '{a.username}'")
    if found[2] == "admin" and found[1] == "admin":
        sys.exit("Refusing to reset the bootstrap admin here; log in and use Change password.")
    username, password = auth_service.reset_password(found[0])
    audit.record("user.password_reset", None, found[0], via="cli")
    print(f"User ID:            {username}")
    print(f"One-time password:  {password}")
    print("Give this to the user in person or by phone. They must change it at next login.")


def cmd_delete_user(a):
    found = _find(a.username)
    if not found:
        sys.exit(f"No user '{a.username}'")
    uid, username, role = found
    if username == "admin":
        sys.exit("Refusing to delete the bootstrap admin account.")
    n = storage.count_studies(uid)
    if n and not a.with_studies:
        sys.exit(f"{username} owns {n} patient stud{'y' if n == 1 else 'ies'}. Disable the account in the Admin "
                 f"panel, or re-run with --with-studies to erase them permanently.")
    if n:
        typed = input(f"This permanently erases {n} studies of {username}. Type the user ID to confirm: ").strip()
        if typed != username:
            sys.exit("Not confirmed; nothing deleted.")
        for sid in storage.study_ids_of(uid):
            storage.delete_study(sid)
        audit.record("user.studies_erased", None, uid, count=n, via="cli")
    auth_service.delete_user(uid)
    audit.record("user.delete", None, uid, via="cli")
    print(f"Deleted {username}" + (f" and {n} studies" if n else "") + ".")


def cmd_set_active(a):
    found = _find(a.username)
    if not found:
        sys.exit(f"No user '{a.username}'")
    if found[1] == "admin" and not a.active:
        sys.exit("Refusing to disable the bootstrap admin account.")
    auth_service.set_active(found[0], a.active)
    audit.record("user.enable" if a.active else "user.disable", None, found[0], via="cli")
    print(f"{found[1]} {'enabled' if a.active else 'disabled (all sessions ended)'}.")


def cmd_delete_registration(a):
    with db.session_scope() as s:
        row = s.get(db.RegistrationRequest, a.reference.strip())
        if row is None:
            sys.exit(f"No registration '{a.reference}'")
        if row.status == "approved" and row.user_id and auth_service.get_user(row.user_id):
            sys.exit(f"This request created the account {row.username}; delete that account instead "
                     f"(its registration record is removed with it).")
        s.delete(row)
    audit.record("registration.delete", None, a.reference, via="cli")
    print(f"Deleted registration {a.reference}.")


def cmd_purge_registrations(a):
    with db.session_scope() as s:
        q = select(db.RegistrationRequest)
        if a.status != "all":
            q = q.where(db.RegistrationRequest.status == a.status)
        rows = [r for r in s.scalars(q).all()
                if not (r.status == "approved" and r.user_id and auth_service.get_user(r.user_id))]
        for r in rows:
            s.delete(r)
    audit.record("registration.purge", None, a.status, count=len(rows), via="cli")
    print(f"Deleted {len(rows)} registration request(s) with status '{a.status}'"
          " (requests that created an existing account are kept).")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m app.manage", description="BTDM-CDSS administration")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="list accounts").set_defaults(fn=cmd_users)
    sub.add_parser("registrations", help="list registration requests").set_defaults(fn=cmd_registrations)
    r = sub.add_parser("reset-password", help="issue a new one-time password")
    r.add_argument("username")
    r.set_defaults(fn=cmd_reset_password)
    d = sub.add_parser("delete-user", help="delete an account")
    d.add_argument("username")
    d.add_argument("--with-studies", action="store_true", help="also erase the account's patient studies")
    d.set_defaults(fn=cmd_delete_user)
    for name, active in (("disable", False), ("enable", True)):
        x = sub.add_parser(name, help=f"{name} an account")
        x.add_argument("username")
        x.set_defaults(fn=cmd_set_active, active=active)
    dr = sub.add_parser("delete-registration", help="delete one registration request")
    dr.add_argument("reference")
    dr.set_defaults(fn=cmd_delete_registration)
    pr = sub.add_parser("purge-registrations", help="delete registration requests by status")
    pr.add_argument("--status", choices=["pending", "rejected", "approved", "all"], required=True)
    pr.set_defaults(fn=cmd_purge_registrations)
    args = p.parse_args(argv)
    db.init_db()
    args.fn(args)


if __name__ == "__main__":
    main()

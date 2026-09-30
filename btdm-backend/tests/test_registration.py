"""
Doctor registration → admin approval → generated credentials → forced
password change at first login.

    PYTHONPATH=. python tests/test_registration.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

TMP = Path(tempfile.mkdtemp())
(TMP / "weights").mkdir()
os.environ.update(
    DATABASE_URL=f"sqlite:///{TMP / 'test.db'}", OBJECT_STORE_DIR=str(TMP / "objects"),
    WEIGHTS_DIR=str(TMP / "weights"), ADMIN_BOOTSTRAP_PASSWORD="Long-test-pass2026",
)
for name in ["torch", "torch.nn", "torch.nn.functional", "torchvision", "torchvision.models", "cv2",
             "scipy", "scipy.ndimage", "scipy.spatial", "nibabel"]:
    sys.modules[name] = mock.MagicMock()

import fakeredis  # noqa: E402

SERVER = fakeredis.FakeServer()
from app.services import events  # noqa: E402

events.client.cache_clear()
events.client = lambda: fakeredis.FakeRedis(server=SERVER, decode_responses=True)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

FORM = dict(fullName="Dr. Sita Sharma", email="Sita.Sharma@example.org", phone="+977 9800000000",
            nmcNumber="nmc-12345", specialty="Radiology", requestedRole="radiologist",
            institution="Province Hospital Surkhet", notes="Part 2 reader")


def main():
    with TestClient(app) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": "admin", "password": "Long-test-pass2026"}).json()["token"]}
        # existing radiologist so numbering continues from rad-001
        c.post("/api/v1/users", headers=admin, json={"name": "Dr. Existing", "username": "rad-001",
                                                     "password": "Mri2026test", "role": "radiologist"})

        # ── public submission ─────────────────────────────────────────
        r = c.post("/api/v1/registrations", json=FORM)
        assert r.status_code == 201, r.text
        ref = r.json()["reference"]
        assert re.match(r"^REG-\d{8}-[0-9A-F]{6}$", ref) and r.json()["status"] == "pending"
        assert c.post("/api/v1/registrations", json=FORM).status_code == 422, "duplicate pending blocked"
        assert c.post("/api/v1/registrations", json={**FORM, "email": "x@y.org", "nmcNumber": "N-2",
                                                      "requestedRole": "admin"}).status_code == 422
        assert c.post("/api/v1/registrations", json={**FORM, "email": "bad-email", "nmcNumber": "N-3"}).status_code == 422
        assert c.post("/api/v1/registrations", json={**FORM, "email": "b@y.org", "nmcNumber": "N-4",
                                                      "website": "http://spam"}).status_code == 400, "honeypot"
        assert c.post("/api/v1/auth/login", json={"username": "rad-002", "password": "x"}).status_code == 401, \
            "no account before approval"
        print("public submission, validation, duplicate + honeypot checks OK")

        # ── admin review ──────────────────────────────────────────────
        assert c.get("/api/v1/registrations").status_code == 401, "listing requires login"
        pending = c.get("/api/v1/registrations?status=pending", headers=admin).json()
        assert len(pending) == 1 and pending[0]["email"] == "sita.sharma@example.org" and pending[0]["nmcNumber"] == "NMC-12345"
        r = c.post(f"/api/v1/registrations/{ref}/approve", headers=admin, json={})
        assert r.status_code == 200, r.text
        creds = r.json()
        assert creds["username"] == "rad-002", creds["username"]
        pw = creds["temporaryPassword"]
        assert re.match(r"^[A-Za-z2-9]{8}$", pw), pw
        assert c.post(f"/api/v1/registrations/{ref}/approve", headers=admin, json={}).status_code == 409, "approve once"
        with db.session_scope() as s:
            row = s.get(db.RegistrationRequest, ref)
            assert row.status == "approved" and row.username == "rad-002"
            assert pw not in repr(vars(row)), "plain password never stored"
            u = s.query(db.User).filter_by(username="rad-002").one()
            assert u.must_change_password and pw not in u.password_hash
        print("approval generated", creds["username"], "+ one-time password OK")

        # ── first login: forced password change ───────────────────────
        r = c.post("/api/v1/auth/login", json={"username": "rad-002", "password": pw})
        assert r.status_code == 200 and r.json()["user"]["mustChangePassword"] is True
        D = {"Authorization": "Bearer " + r.json()["token"]}
        assert c.get("/api/v1/studies", headers=D).status_code == 403, "blocked until password changed"
        assert c.post("/api/v1/auth/change-password", headers=D, json={
            "currentPassword": "wrong", "newPassword": "NewStrong2026pw"}).status_code == 400
        for weak, why in [("short1", "at least 8"), ("onlyletters", "number"), ("12345678", "letter"),
                          ("rad-002abc9", "user ID"), ("password123", "common"), ("a1" * 40, "at most 64")]:
            r = c.post("/api/v1/auth/change-password", headers=D, json={"currentPassword": pw, "newPassword": weak})
            assert r.status_code == 400 and why in r.json()["detail"], (weak, r.text)
        r = c.post("/api/v1/auth/change-password", headers=D, json={
            "currentPassword": pw, "newPassword": "NewStrong2026pw"})
        assert r.status_code == 200 and r.json()["mustChangePassword"] is False
        assert c.get("/api/v1/studies", headers=D).status_code == 200, "full access after change"
        assert c.post("/api/v1/auth/login", json={"username": "rad-002", "password": pw}).status_code == 401, \
            "temporary password no longer works"
        print("forced first-login password change OK")
        pol = c.get("/api/v1/auth/password-policy").json()
        assert pol["min_length"] == 8 and pol["max_length"] == 64 and pol["generated_length"] == 8, pol
        r = c.post("/api/v1/users", headers=admin, json={"name": "X", "username": "weakuser", "password": "abcdefgh", "role": "technologist"})
        assert r.status_code == 409 and "number" in r.json()["detail"], r.text
        r = c.post("/api/v1/users", headers=admin, json={"name": "X", "username": "Bad User!", "password": "Good2026pass", "role": "technologist"})
        assert r.status_code == 409 and "User ID" in r.json()["detail"], r.text
        print("password policy (8-char generated one-time passwords; chosen 8–64, letter+digit) OK")

        # ── rejection + referring-physician numbering ────────────────
        ref2 = c.post("/api/v1/registrations", json={**FORM, "email": "r@y.org", "nmcNumber": "N-9"}).json()["reference"]
        r = c.post(f"/api/v1/registrations/{ref2}/reject", headers=admin, json={"reason": "NMC number not verified"})
        assert r.status_code == 200 and r.json()["status"] == "rejected"
        assert c.post(f"/api/v1/registrations/{ref2}/approve", headers=admin, json={}).status_code == 409
        ref3 = c.post("/api/v1/registrations", json={**FORM, "email": "p@y.org", "nmcNumber": "N-10",
                                                     "requestedRole": "referring_physician"}).json()["reference"]
        assert c.post(f"/api/v1/registrations/{ref3}/approve", headers=admin, json={}).json()["username"] == "ref-001"
        # a rejected applicant may apply again
        assert c.post("/api/v1/registrations", json={**FORM, "email": "r@y.org", "nmcNumber": "N-9"}).status_code == 201
        non_admin = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": "rad-001", "password": "Mri2026test"}).json()["token"]}
        assert c.get("/api/v1/registrations", headers=non_admin).status_code == 403
        with db.session_scope() as s:
            actions = {a.action for a in s.query(db.AuditLog).all()}
        assert {"registration.submit", "registration.approve", "registration.reject", "auth.password_changed"} <= actions
        print("rejection, re-application, ref-001 numbering, admin-only, audit trail OK")
    print("\nALL REGISTRATION TESTS PASSED")


if __name__ == "__main__":
    main()

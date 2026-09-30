"""
Admin command line (python -m app.manage): list, reset password, disable,
delete test accounts (with and without studies), registrations, and user-ID
numbering after deletion.      PYTHONPATH=. python tests/test_manage.py
"""
import builtins
import contextlib
import importlib.util
import io
from pathlib import Path

spec = importlib.util.spec_from_file_location("harness", Path(__file__).with_name("test_platform.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import manage  # noqa: E402
from app.services import registration, storage  # noqa: E402


def run(*argv, answer=None) -> str:
    out = io.StringIO()
    orig = builtins.input
    if answer is not None:
        builtins.input = lambda _prompt="": answer
    try:
        with contextlib.redirect_stdout(out):
            try:
                manage.main(list(argv))
            except SystemExit as e:
                if e.code not in (None, 0):
                    print(e.code)
    finally:
        builtins.input = orig
    return out.getvalue()


def main():
    with TestClient(h.app) as c:
        A = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": "admin", "password": "Long-test-pass2026"}).json()["token"]}
        form = dict(email="t{}@example.org", phone="9800000000", nmcNumber="NMC-{}", specialty="Radiology",
                    requestedRole="radiologist", institution="Test Hospital", notes="")
        ids = []
        for i in (1, 2):
            f = {k: (v.format(i) if isinstance(v, str) else v) for k, v in form.items()}
            ref = c.post("/api/v1/registrations", json={"fullName": f"Dr Test {i}", **f}).json()["reference"]
            ids.append(c.post(f"/api/v1/registrations/{ref}/approve", headers=A, json={}).json()["username"])
        assert ids == ["rad-001", "rad-002"], ids

        listing = run("users")
        assert "rad-001" in listing and "rad-002" in listing and "STUDIES" in listing
        print("users listing OK")

        out = run("reset-password", "rad-002")
        pw = out.split("One-time password:")[1].split()[0]
        assert len(pw) == 8
        r = c.post("/api/v1/auth/login", json={"username": "rad-002", "password": pw}).json()
        assert r["user"]["mustChangePassword"] is True
        print("reset-password: new 8-character one-time password, must change OK")

        assert "disabled" in run("disable", "rad-002")
        assert c.post("/api/v1/auth/login", json={"username": "rad-002", "password": pw}).status_code == 401
        assert "enabled" in run("enable", "rad-002")
        print("disable / enable OK")

        # rad-001 gets a study; plain delete refused, --with-studies needs typed confirmation
        uid1 = next(u.id for u in __import__("app.services.auth", fromlist=["x"]).list_users() if u.username == "rad-001")
        b = io.BytesIO(); Image.new("L", (32, 32), 90).save(b, format="PNG")
        sid = storage.create_study("CLI-TEST", ["T1"], [(b.getvalue(), "standard")], uploaded_by=uid1).id
        assert "owns 1 patient study" in run("delete-user", "rad-001")
        assert "Not confirmed" in run("delete-user", "rad-001", "--with-studies", answer="nope")
        assert storage.count_studies(uid1) == 1
        assert "Deleted rad-001 and 1 studies" in run("delete-user", "rad-001", "--with-studies", answer="rad-001")
        assert storage.count_studies(uid1) == 0 and storage.get_study(sid) is None
        assert not (h.TMP / "objects" / "studies" / sid).exists(), "image files erased too"
        print("delete-user: refuses patient data, erases only after typed confirmation OK")

        assert "Deleted rad-002" in run("delete-user", "rad-002")
        assert "Refusing" in run("delete-user", "admin")
        print("delete-user test account OK; bootstrap admin protected OK")

        # registration records of deleted accounts are gone; numbering restarts
        assert "rad-00" not in run("registrations")
        f = {k: (v.format(9) if isinstance(v, str) else v) for k, v in form.items()}
        ref = c.post("/api/v1/registrations", json={"fullName": "Dr New", **f}).json()["reference"]
        assert registration.next_username("radiologist") == "rad-001", "numbering restarts after test accounts removed"
        c.post(f"/api/v1/registrations/{ref}/reject", headers=A, json={"reason": "test"})
        assert "Deleted 1 registration" in run("purge-registrations", "--status", "rejected")
        print("registrations purged; user-ID numbering restarts at rad-001 OK")
    print("\nALL ADMIN CLI TESTS PASSED")


if __name__ == "__main__":
    main()

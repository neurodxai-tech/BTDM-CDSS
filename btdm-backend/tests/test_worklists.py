"""
Per-doctor worklists: each user sees and acts on only their own studies;
everyone's analyses run on the same shared model.

    PYTHONPATH=. python tests/test_worklists.py
"""
import importlib.util
import io
import json
from pathlib import Path

# Reuse the platform-test harness (temp DB, fakeredis, stubbed ML, pinned seed42).
spec = importlib.util.spec_from_file_location("harness", Path(__file__).with_name("test_platform.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from app import db  # noqa: E402


def png() -> bytes:
    b = io.BytesIO()
    Image.new("L", (64, 64), 100).save(b, format="PNG")
    return b.getvalue()


def main():
    with TestClient(h.app) as c:
        admin = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": "admin", "password": "Long-test-pass2026"}).json()["token"]}
        users = {"radA": "radiologist", "radB": "radiologist", "refC": "referring_physician", "techT": "technologist"}
        for u, role in users.items():
            assert c.post("/api/v1/users", headers=admin, json={
                "name": u, "username": u, "password": "Mri2026test", "role": role}).status_code == 201
        H = {u: {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": u.lower(), "password": "Mri2026test"}).json()["token"]} for u in users}

        sid = {}
        for u in users:
            r = c.post("/api/v1/studies", headers=H[u], files={"files": ("s.png", png(), "image/png")},
                       data={"patient_alias": f"{u}-001"})
            assert r.status_code == 201, (u, r.text)
            sid[u] = r.json()["id"]
        print("all clinical roles can upload (incl. referring physicians) OK")

        for u in users:
            listed = [s["id"] for s in c.get("/api/v1/studies", headers=H[u]).json()]
            assert listed == [sid[u]], (u, listed)
        assert c.get("/api/v1/studies", headers=admin).json() == []
        print("each worklist shows only the owner's studies OK")

        a, b = sid["radA"], H["radB"]
        for method, url in [
            ("get", f"/api/v1/studies/{a}"), ("get", f"/api/v1/studies/{a}/results"),
            ("get", f"/api/v1/studies/{a}/gradcam"), ("get", f"/api/v1/studies/{a}/job"),
            ("get", f"/api/v1/studies/{a}/events"), ("get", f"/api/v1/studies/{a}/report"),
            ("get", f"/api/v1/studies/{a}/report/draft"), ("post", f"/api/v1/studies/{a}/analyze"),
            ("post", f"/api/v1/studies/{a}/report/sign"), ("delete", f"/api/v1/studies/{a}"),
        ]:
            r = getattr(c, method)(url, headers=b)
            assert r.status_code == 404, (method, url, r.status_code)
        assert c.patch(f"/api/v1/studies/{a}/report", headers=b, json={"impression": "x"}).status_code == 404
        assert c.get(f"/api/v1/studies/{a}", headers=H["radA"]).status_code == 200
        print("another doctor gets 404 on every endpoint of someone else's study OK")

        worker = h.make_worker()
        worker.sync_active_model()
        for u in ("radA", "radB", "refC"):
            assert c.post(f"/api/v1/studies/{sid[u]}/analyze", headers=H[u]).status_code == 202
        h.drain(worker)
        with db.session_scope() as s:
            shas = {s.get(db.Analysis, sid[u]).model_sha256 for u in ("radA", "radB", "refC")}
        health = c.get("/health").json()
        assert shas == {health["checkpoint_sha256"]}, shas
        print("all doctors' analyses used the same shared model", health["checkpoint_id"], "OK")

        assert c.get(f"/api/v1/studies/{sid['radB']}/results", headers=H["radB"]).status_code == 200
        assert c.post(f"/api/v1/studies/{sid['refC']}/report/sign", headers=H["refC"]).status_code == 403
        assert c.delete(f"/api/v1/studies/{a}", headers=H["radA"]).status_code == 204
        assert [s["id"] for s in c.get("/api/v1/studies", headers=H["radB"]).json()] == [sid["radB"]]
        print("owners manage their own studies; signing stays radiologist-only OK")

        # Admin: manages accounts, never sees patient data
        b_sid = sid["radB"]
        for url in (f"/api/v1/studies/{b_sid}", f"/api/v1/studies/{b_sid}/results",
                    f"/api/v1/studies/{b_sid}/report", f"/api/v1/studies/{b_sid}/gradcam"):
            assert c.get(url, headers=admin).status_code in (403, 404), url
        print("admin cannot open any doctor's patient data OK")

        # A doctor who owns patient data cannot be deleted, only disabled
        uid = {u["username"]: u["id"] for u in c.get("/api/v1/users", headers=admin).json()}
        r = c.delete(f"/api/v1/users/{uid['radb']}", headers=admin)
        assert r.status_code == 409 and "Disable" in r.json()["detail"], r.text
        assert c.patch(f"/api/v1/users/{uid['radb']}/active?active=false", headers=admin).status_code == 200
        assert c.get("/api/v1/studies", headers=H["radB"]).status_code == 401, "disabled: sessions revoked"
        for u in ("refC", "techT"):
            assert b_sid not in [s["id"] for s in c.get("/api/v1/studies", headers=H[u]).json()]
        assert c.delete(f"/api/v1/users/{uid['radc']}" if "radc" in uid else f"/api/v1/users/{uid['techt']}",
                        headers=admin).status_code == 409
        print("doctors with patient data can only be disabled, workspace stays isolated OK")

        # Sessions: sliding idle timeout and absolute lifetime
        from app.services import events as ev
        tok = H["refC"]["Authorization"].split()[1]
        import hashlib, time
        key = "btdm:session:" + hashlib.sha256(tok.encode()).hexdigest()
        ev.client().expire(key, 5)
        assert c.get("/api/v1/studies", headers=H["refC"]).status_code == 200
        assert ev.client().ttl(key) > 60, "activity renews the idle timeout"
        ev.client().expire(key, 1)
        time.sleep(1.2)
        assert c.get("/api/v1/studies", headers=H["refC"]).status_code == 401, "idle session expired"
        fresh = {"Authorization": "Bearer " + c.post("/api/v1/auth/login", json={
            "username": "refc", "password": "Mri2026test"}).json()["token"]}
        k2 = "btdm:session:" + hashlib.sha256(fresh["Authorization"].split()[1].encode()).hexdigest()
        uid_c, _, _ = ev.client().get(k2).partition("|")
        ev.client().set(k2, f"{uid_c}|{int(time.time()) - 13 * 3600}", ex=1800)
        assert c.get("/api/v1/studies", headers=fresh).status_code == 401, "absolute lifetime enforced"
        print("idle timeout (sliding) and 12-hour absolute session limit OK")
    print("\nALL WORKLIST TESTS PASSED")


if __name__ == "__main__":
    main()

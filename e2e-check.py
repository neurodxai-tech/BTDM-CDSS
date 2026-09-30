import glob, hashlib, json, os, random, secrets, string, struct, subprocess, time, urllib.error, urllib.request, uuid, zlib
API = "http://localhost:8000"
ROOT = os.path.expanduser("~/Documents/Brain Tumour Diagnosis with Domain Alignment and Contrastive Learning")
PROJ = os.path.join(ROOT, "BTDM-CDSS")
CLASSES = ["glioma", "meningioma", "pituitary", "notumor"]
results = []
def ok(m): results.append(True); print("  PASS ", m)
def bad(m): results.append(False); print("  FAIL ", m)
def check(c, m): ok(m) if c else bad(m)
def sh(args): return subprocess.run(args, cwd=PROJ, capture_output=True, text=True)
def in_api(code): r = sh(["docker", "compose", "exec", "-T", "api", "python", "-c", code]); return r.stdout.strip(), r.stderr.strip()
def req(method, path, token=None, body=None, ctype=None):
    h = {}
    if token: h["Authorization"] = "Bearer " + token
    if ctype: h["Content-Type"] = ctype
    r = urllib.request.Request(API + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp: return resp.status, resp.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
def jreq(method, path, token=None, data=None):
    s, b = req(method, path, token, json.dumps(data).encode() if data is not None else None, "application/json" if data is not None else None)
    try: return s, json.loads(b or b"null")
    except Exception: return s, None
def upload(token, name, data, alias):
    bd = uuid.uuid4().hex
    body = (f"--{bd}\r\nContent-Disposition: form-data; name=\"patient_alias\"\r\n\r\n{alias}\r\n"
            f"--{bd}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{bd}--\r\n".encode()
    s, b = req("POST", "/api/v1/studies", token, body, f"multipart/form-data; boundary={bd}")
    return s, json.loads(b) if b else None
def synthetic_png():
    w = h = 128; rows = b"".join(b"\x00" + bytes(int(100 + 80 * ((x - 64) ** 2 + (y - 64) ** 2 < 2500)) for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")

print("\n1. Containers")
ps = sh(["docker", "compose", "ps", "--format", "{{.Service}} {{.State}}"]).stdout.split("\n")
running = {l.split()[0] for l in ps if l.strip().endswith("running")}
for svc in ["postgres", "redis", "api", "worker", "frontend"]: check(svc in running, f"{svc} running")

print("\n2. Health and model")
s, hj = jreq("GET", "/health")
check(s == 200 and hj["status"] == "ok", f"health status ok  (checks: {hj.get('checks') if hj else s})")
check(hj and hj["checkpoint_id"] == "best_model_seed42", "active checkpoint is best_model_seed42")
check(hj and hj["checkpoint_matches_manuscript"], "checkpoint matches manuscript")
check(hj and hj["model_locked"], "model lock is ON")
check(hj and hj["worker_loaded_sha256"] == hj["checkpoint_sha256"], "worker has exactly the active checkpoint in memory")
wfile = glob.glob(os.path.join(ROOT, "Modeltrained", "**", "BTDM-CDSS_model_seed42.pth"), recursive=True)
if wfile:
    sha = hashlib.sha256(open(wfile[0], "rb").read()).hexdigest()
    check(sha == hj["checkpoint_sha256"], f"served model == training file on disk ({sha[:12]}…)")
else: bad("could not find BTDM-CDSS_model_seed42.pth in Modeltrained")
s, fe = req("GET", "/login".replace("/login", "")) if False else (None, None)
try:
    with urllib.request.urlopen("http://localhost:3000/login", timeout=20) as r: check(r.status == 200, "frontend login page responds")
except Exception as e: bad(f"frontend not reachable: {e}")

print("\n3. Temporary test account")
uname = "e2e-" + secrets.token_hex(3)
pw = "E2e" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(10)) + "7"
out, err = in_api(f"from app.services import auth, audit; u = auth.create_user('E2E Smoke Test', '{uname}', '{pw}', 'radiologist'); audit.record('e2e.create', None, u.id); print(u.id)")
uid = out.splitlines()[-1] if out else None
check(bool(uid), f"created {uname}" + ("" if uid else f"  ({err[-200:]})"))
s, lj = jreq("POST", "/api/v1/auth/login", data={"username": uname, "password": pw})
tok = lj.get("token") if lj else None
check(s == 200 and tok, "login works")
s, _ = jreq("GET", "/api/v1/studies"); check(s == 401, "unauthenticated access refused (401)")

print("\n4. Real analyses (upload -> queue -> worker -> model -> Grad-CAM)")
samples = []
for cls in CLASSES:
    cands = [p for p in glob.glob(os.path.join(ROOT, "**", "*"), recursive=True)
             if os.path.basename(os.path.dirname(p)).lower() in (cls, cls.replace("notumor", "no_tumor"), "no tumor" if cls == "notumor" else cls)
             and p.lower().endswith((".jpg", ".jpeg", ".png")) and "BTDM-CDSS" not in p]
    test = [p for p in cands if "test" in p.lower()] or cands
    if test: samples.append((cls, random.choice(test)))
if not samples: print("  (no labelled dataset images found - using one synthetic image: pipeline check only)"); samples = [(None, None)]
study_ids = []
correct = 0
for cls, path in samples:
    data = open(path, "rb").read() if path else synthetic_png()
    name = os.path.basename(path) if path else "synthetic.png"
    s, st = upload(tok, name, data, f"E2E-{cls or 'synthetic'}")
    if s != 201: bad(f"upload {name}: HTTP {s}"); continue
    sid = st["id"]; study_ids.append(sid)
    s, tj = jreq("POST", f"/api/v1/studies/{sid}/analyze", tok)
    t0, status = time.time(), None
    while time.time() - t0 < 240:
        s, jj = jreq("GET", f"/api/v1/studies/{sid}/job", tok)
        status = jj.get("status") if jj else None
        if status in ("done", "failed"): break
        time.sleep(2)
    if status != "done": bad(f"{name}: analysis {status} {jj.get('error') if jj else ''}"); continue
    s, rj = jreq("GET", f"/api/v1/studies/{sid}/results", tok)
    pred, probs = rj["predictedClass"], rj["probabilities"]
    s2, png = req("GET", f"/api/v1/studies/{sid}/gradcam", tok)
    check(abs(sum(probs.values()) - 1) < 0.01, f"{name}: probabilities sum to 1")
    check(rj["model"].get("checkpointSha256") == hj["checkpoint_sha256"], f"{name}: result stamped with the Seed 42 checksum")
    check(s2 == 200 and png[:4] == b"\x89PNG", f"{name}: Grad-CAM image produced")
    msg = f"{name}: predicted {pred} ({probs[pred]:.1%}) in {time.time() - t0:.1f}s"
    if cls: msg += f", true label {cls}"; correct += pred == cls
    print("  INFO ", msg)
if samples[0][0]: print(f"  INFO  {correct}/{len(samples)} sample images classified correctly (small spot check, not an accuracy estimate)")

print("\n5. Isolation")
s, lst = jreq("GET", "/api/v1/studies", tok)
check(s == 200 and sorted(x["id"] for x in lst) == sorted(study_ids), "test account sees only its own studies")
s, _ = jreq("GET", "/api/v1/studies/study-doesnotexist", tok); check(s == 404, "unknown/other study returns 404")

print("\n6. Cleanup")
for sid in study_ids: jreq("DELETE", f"/api/v1/studies/{sid}", tok)
out, err = in_api(f"from app.services import auth, audit, storage; n = storage.count_studies('{uid}'); auth.delete_user('{uid}'); audit.record('e2e.cleanup', None, '{uid}'); print(n)")
check(out.strip().endswith("0"), f"test studies and account {uname} removed")

print(f"\nRESULT: {sum(results)}/{len(results)} checks passed" + ("  -> EVERYTHING WORKING END TO END" if all(results) else "  -> see FAIL lines above"))

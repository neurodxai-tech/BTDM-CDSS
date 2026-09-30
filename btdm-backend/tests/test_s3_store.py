"""S3/MinIO object-store adapter against moto's S3 emulator.
    pip install "moto[s3]" && PYTHONPATH=. python tests/test_s3_store.py"""
import os
import tempfile
from pathlib import Path

os.environ.update(S3_ENDPOINT="", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x", AWS_DEFAULT_REGION="us-east-1")
from moto import mock_aws

with mock_aws():
    from app.services import objectstore

    objectstore.S3_ENDPOINT = None  # moto intercepts the default AWS endpoint
    s = objectstore._S3Store()
    s.ensure_bucket()
    s.put("studies/s1/img1", b"abc")
    s.put("studies/s1/img2", b"def")
    s.put("gradcam/s1/img1/both.png", b"png", "image/png")
    assert s.get("studies/s1/img1") == b"abc" and s.get("missing") is None
    dest = Path(tempfile.mkdtemp()) / "m.pt"
    s.download_to("studies/s1/img2", dest)
    assert dest.read_bytes() == b"def"
    s.delete_prefix("studies/s1/")
    assert s.get("studies/s1/img1") is None and s.get("gradcam/s1/img1/both.png") == b"png"
    assert s.ping()
    print("S3 object store OK")

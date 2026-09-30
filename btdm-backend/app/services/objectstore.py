"""
Object storage for everything binary: uploaded images, Grad-CAM PNGs and
model checkpoints. A directory (a shared Docker volume in docker-compose) by
default; any S3-compatible service when S3_ENDPOINT is set.

Keys are opaque, e.g. "studies/<study_id>/<image_id>", "gradcam/<study_id>/
<image_id>/<mode>.png", "models/<sha256>.pt".
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from app.config import OBJECT_STORE_DIR, S3_ACCESS_KEY, S3_BUCKET, S3_ENDPOINT, S3_SECRET_KEY

logger = logging.getLogger("btdm.services.objectstore")


class _LocalStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise ValueError("invalid object key")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes | None:
        p = self._path(key)
        return p.read_bytes() if p.exists() else None

    def download_to(self, key: str, dest: Path) -> None:
        data = self.get(key)
        if data is None:
            raise FileNotFoundError(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def delete_prefix(self, prefix: str) -> None:
        import shutil

        p = self._path(prefix)
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()

    def ping(self) -> bool:
        return self.root.exists()


class _S3Store:
    def __init__(self):
        import boto3
        from botocore.config import Config

        self.bucket = S3_BUCKET
        self.s3 = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
            region_name="us-east-1",
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.s3.create_bucket(Bucket=self.bucket)
            logger.info("Created bucket %s", self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise

    def download_to(self, key: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        self.s3.download_file(self.bucket, key, str(tmp))
        tmp.replace(dest)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})

    def ping(self) -> bool:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


@lru_cache(maxsize=1)
def store():
    if S3_ENDPOINT:
        s = _S3Store()
        s.ensure_bucket()
        return s
    logger.info("Using file-system object store at %s", OBJECT_STORE_DIR)
    return _LocalStore(OBJECT_STORE_DIR)

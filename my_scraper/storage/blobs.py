"""Write-once blobs, named by the hash of their contents: a local folder, or S3 and compatible stores."""
import gzip
import hashlib
import os
import uuid
import zlib
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit


class DamagedBlob(Exception):
    """A stored blob no longer matches the hash it was saved under."""


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unpack(packed: bytes, digest: str) -> bytes:
    try:
        data = gzip.decompress(packed)
    except (OSError, EOFError, zlib.error) as e:
        raise DamagedBlob(f"{digest}: {e}") from e
    if digest_of(data) != digest:
        raise DamagedBlob(digest)
    return data


class LocalBlobs:
    def __init__(self, root: str):
        self.root = Path(root)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / f"{digest}.gz"

    def put(self, data: bytes) -> str:
        digest = digest_of(data)
        path = self._path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f"{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(gzip.compress(data, mtime=0))
            os.replace(temporary, path)  # the same name means the same bytes, so a race changes nothing
        return digest

    def get(self, digest: str) -> Optional[bytes]:
        try:
            packed = self._path(digest).read_bytes()
        except FileNotFoundError:
            return None
        return _unpack(packed, digest)

    def delete(self, digest: str):
        self._path(digest).unlink(missing_ok=True)


class S3Blobs:
    """Needs boto3. Credentials come from the usual AWS environment, never from the config."""

    def __init__(self, url: str, client=None):
        parts = urlsplit(url)
        self.bucket, self.prefix = parts.netloc, parts.path.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None)
        self.client = client

    def _key(self, digest: str) -> str:
        return "/".join(filter(None, (self.prefix, digest[:2], f"{digest}.gz")))

    def put(self, data: bytes) -> str:
        digest = digest_of(data)
        self.client.put_object(Bucket=self.bucket, Key=self._key(digest), Body=gzip.compress(data, mtime=0))
        return digest

    def get(self, digest: str) -> Optional[bytes]:
        try:
            packed = self.client.get_object(Bucket=self.bucket, Key=self._key(digest))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        return _unpack(packed, digest)

    def delete(self, digest: str):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(digest))


def open_blobs(location: str):
    return S3Blobs(location) if location.startswith("s3://") else LocalBlobs(location)

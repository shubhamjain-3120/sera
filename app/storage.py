import hashlib
from pathlib import Path
from typing import BinaryIO, Protocol

import boto3

from app.config import Settings, get_settings


class ObjectStorage(Protocol):
    def put_immutable(self, stream: BinaryIO, suffix: str) -> tuple[str, str, int]: ...
    def open(self, key: str) -> BinaryIO: ...


class FilesystemStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_immutable(self, stream: BinaryIO, suffix: str) -> tuple[str, str, int]:
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            chunks.append(chunk)
            size += len(chunk)
        sha256 = digest.hexdigest()
        key = f"originals/{sha256[:2]}/{sha256}{suffix.lower()}"
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(b"".join(chunks))
            destination.chmod(0o444)
        return key, sha256, size

    def open(self, key: str) -> BinaryIO:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key")
        return path.open("rb")


class S3Storage:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_immutable(self, stream: BinaryIO, suffix: str) -> tuple[str, str, int]:
        data = stream.read()
        sha256 = hashlib.sha256(data).hexdigest()
        key = f"originals/{sha256[:2]}/{sha256}{suffix.lower()}"
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key, sha256, len(data)

    def open(self, key: str) -> BinaryIO:
        from io import BytesIO

        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return BytesIO(response["Body"].read())


def get_storage(settings: Settings | None = None) -> ObjectStorage:
    current = settings or get_settings()
    if current.storage_backend == "s3":
        return S3Storage(current)
    return FilesystemStorage(current.storage_root)


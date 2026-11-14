import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation


@dataclass(frozen=True)
class StoredArtifact:
    key: str
    sha256: str
    size_bytes: int


class PrivateDocumentStorage:
    """Local artifact storage; callers must authorize the company before use."""

    def __init__(self, company_id, agreement_id, *, root=None):
        self.company_id = company_id
        self.agreement_id = agreement_id
        self.prefix = self.key_prefix(company_id, agreement_id)
        location = Path(root if root is not None else settings.AGREEMENT_STORAGE_ROOT)
        if not location.is_absolute():
            raise ImproperlyConfigured("Agreement storage requires an absolute path.")
        self.root = location.resolve()
        public_roots = [settings.BASE_DIR.parent / "frontend"]
        public_roots.extend(
            entry[1] if isinstance(entry, (list, tuple)) else entry
            for entry in settings.STATICFILES_DIRS
        )
        public_roots.extend(
            path for path in (settings.STATIC_ROOT, settings.MEDIA_ROOT) if path
        )
        for path in public_roots:
            public_root = Path(path).resolve()
            if self.root.is_relative_to(public_root) or public_root.is_relative_to(self.root):
                raise ImproperlyConfigured("Agreement storage must not overlap public file directories.")

    @staticmethod
    def key_prefix(company_id, agreement_id):
        owner = str(company_id)
        if not owner.isascii() or not owner.isdecimal() or int(owner) <= 0 or str(int(owner)) != owner:
            raise ValueError("A saved company account is required.")
        return f"companies/{owner}/agreements/{uuid.UUID(str(agreement_id))}"

    @classmethod
    def validate_key(cls, key, company_id, agreement_id):
        prefix = cls.key_prefix(company_id, agreement_id)
        if not isinstance(key, str) or not key.startswith(f"{prefix}/"):
            raise SuspiciousFileOperation("Artifact does not belong to this agreement.")
        filename = key[len(prefix) + 1:]
        try:
            artifact_id = uuid.UUID(filename.removesuffix(".bin"))
        except ValueError as error:
            raise SuspiciousFileOperation("Invalid private artifact key.") from error
        if filename != f"{artifact_id}.bin":
            raise SuspiciousFileOperation("Invalid private artifact key.")
        return key

    def _path(self, key):
        # Only generated keys in this company/agreement namespace are accepted.
        self.validate_key(key, self.company_id, self.agreement_id)
        path = self.root.joinpath(*key.split("/"))
        if path.resolve() != path:
            raise SuspiciousFileOperation("Private artifact paths cannot use symbolic links.")
        return path

    def write(self, chunks, *, max_bytes):
        """Stream a new artifact with a caller-approved byte cap; never overwrite."""
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("An explicit positive artifact byte limit is required.")
        key = f"{self.prefix}/{uuid.uuid4()}.bin"
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = self.root
        for part in self.prefix.split("/"):
            directory /= part
            if directory.resolve() != directory:
                raise SuspiciousFileOperation("Private artifact paths cannot use symbolic links.")
            directory.mkdir(exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as destination:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ValueError("Artifact chunks must be bytes.")
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("The document exceeds the allowed artifact size.")
                    destination.write(chunk)
                    digest.update(chunk)
                if not size:
                    raise ValueError("The document is empty.")
                destination.flush()
                os.fsync(destination.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return StoredArtifact(key=key, sha256=digest.hexdigest(), size_bytes=size)

    def open(self, key):
        return self._path(key).open("rb")

    def inspect(self, key, *, max_bytes):
        digest, size = hashlib.sha256(), 0
        with self.open(key) as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("The private artifact exceeds its byte limit.")
                digest.update(chunk)
        if not size:
            raise ValueError("The private artifact is empty.")
        return StoredArtifact(key, digest.hexdigest(), size)

    def write_once(self, key, chunks, *, max_bytes):
        """Publish a reserved artifact atomically; recover it after an interrupted DB save.

        The caller must serialize retries using its request's database lock.
        """
        path = self._path(key)
        if path.exists():
            return self.inspect(key, max_bytes=max_bytes)
        # Create/check the same private directory hierarchy as ordinary writes.
        directory = self.root
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for part in self.prefix.split("/"):
            directory /= part
            if directory.resolve() != directory:
                raise SuspiciousFileOperation("Private artifact paths cannot use symbolic links.")
            directory.mkdir(exist_ok=True, mode=0o700)
        staging = path.with_suffix(".tmp")
        if staging.resolve() != staging:
            raise SuspiciousFileOperation("Private staging paths cannot use symbolic links.")
        size = 0
        try:
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ValueError("Artifact chunks must be bytes.")
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("The completed PDF exceeds the artifact size limit.")
                    destination.write(chunk)
                if not size:
                    raise ValueError("The completed PDF is empty.")
                destination.flush()
                os.fsync(destination.fileno())
            # A hard link publishes the entire file and never replaces an existing artifact.
            try:
                os.link(staging, path)
            except FileExistsError:
                pass
        finally:
            staging.unlink(missing_ok=True)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return self.inspect(key, max_bytes=max_bytes)

    def delete(self, key):
        """Remove only an artifact in this agreement's private namespace."""
        self._path(key).unlink(missing_ok=True)

    def url(self, key):
        raise NotImplementedError("Private artifacts are available only through authorized views.")

"""Secrets are injected at run time: a setting names where the secret is, never the secret itself.

    "env:DB_URL"              the environment variable DB_URL
    "file:/run/secrets/db"    the contents of a file, as Docker and Kubernetes mount secrets
"""
import os
from pathlib import Path
from typing import Optional


class SecretMissing(ValueError):
    """A setting points at a secret that is not there."""


def resolve(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return value
    if value.startswith("env:"):
        name = value[4:]
        if name not in os.environ:
            raise SecretMissing(f"environment variable {name} is not set")
        return os.environ[name]
    if value.startswith("file:"):
        try:
            return Path(value[5:]).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise SecretMissing(f"secret file could not be read: {e}") from e
    return value

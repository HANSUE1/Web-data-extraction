"""Which parser produced a record. The hash changes with the code, so a forgotten bump still shows."""
import hashlib
from functools import lru_cache
from pathlib import Path

PARSER_RELEASE = "1.0.0"  # bump when extraction is meant to give different results


@lru_cache(maxsize=1)
def parser_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        # Line endings differ between a Windows checkout and a container; the parser does not.
        digest.update(path.name.encode() + path.read_bytes().replace(b"\r\n", b"\n"))
    return f"{PARSER_RELEASE}+{digest.hexdigest()[:8]}"

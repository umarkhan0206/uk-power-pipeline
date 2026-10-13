"""Landing zone: raw API responses stored exactly as received, never overwritten.

Layout: <root>/<source>/<partition>/<fetched_at>_<sha8>.json, plus a
<same name>.meta.json sidecar describing the fetch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ukpower.ingestion.http_client import FetchResult, Params

logger = logging.getLogger(__name__)

# Names become folder names, so allow only safe characters (no slashes or "..").
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.T:-]*$")
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"
META_SUFFIX = ".meta.json"


@dataclass(frozen=True)
class LandedFile:
    """Paths and fingerprint of one stored response."""

    data_path: Path
    meta_path: Path
    sha256: str


def _check_name(kind: str, value: str) -> None:
    if not SAFE_NAME.fullmatch(value) or ".." in value:
        raise ValueError(f"unsafe {kind} name: {value!r}")


def _partition_folder(partition: str) -> str:
    # Colons are not allowed in Windows folder names, so 2026-10-04T00:00 becomes 2026-10-04T0000.
    return partition.replace(":", "")


def _write_new_file(path: Path, content: bytes) -> None:
    """Write via a temporary file, so a crash never leaves a half written file in place."""
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(content)
    os.replace(tmp_path, path)


class LandingZone:
    """Append only store for raw API responses."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(
        self, source: str, partition: str, result: FetchResult, params: Params | None = None
    ) -> LandedFile:
        """Store one response and its metadata.

        Args:
            source: Dataset name, for example "elexon_system_prices".
            partition: What the data is about, for example a settlement date "2026-10-06"
                or a forecast run "2026-10-04T00:00".
            result: The response from HttpClient.get.
            params: The query parameters used, recorded for traceability.

        Raises:
            ValueError: if source or partition contain unsafe characters.
            FileExistsError: if a file with the same name already exists.
        """
        _check_name("source", source)
        _check_name("partition", partition)

        sha256 = hashlib.sha256(result.content).hexdigest()
        stem = f"{result.fetched_at.strftime(TIMESTAMP_FORMAT)}_{sha256[:8]}"
        folder = self.root / source / _partition_folder(partition)
        folder.mkdir(parents=True, exist_ok=True)

        data_path = folder / f"{stem}.json"
        meta_path = folder / f"{stem}{META_SUFFIX}"
        metadata = {
            "source": source,
            "partition": partition,
            "url": result.url,
            "params": dict(params) if params else {},
            "status_code": result.status_code,
            "fetched_at": result.fetched_at.isoformat(),
            "sha256": sha256,
            "size_bytes": len(result.content),
        }

        # Data first, metadata second: a metadata file always points to complete data.
        _write_new_file(data_path, result.content)
        _write_new_file(meta_path, json.dumps(metadata, indent=2).encode("utf-8"))

        logger.info(
            "Landed %s/%s (%d bytes, sha256 %s)", source, partition, len(result.content), sha256[:8]
        )
        return LandedFile(data_path=data_path, meta_path=meta_path, sha256=sha256)

    def list_data_files(self, source: str, partition: str | None = None) -> list[Path]:
        """Stored data files for a source (optionally one partition), oldest fetch first."""
        _check_name("source", source)
        base = self.root / source
        if partition is not None:
            _check_name("partition", partition)
            base = base / _partition_folder(partition)
        if not base.exists():
            return []
        return sorted(p for p in base.rglob("*.json") if not p.name.endswith(META_SUFFIX))

    def landed_partitions(self, source: str) -> set[str]:
        """Partitions (folder names) holding at least one data file for this source."""
        _check_name("source", source)
        base = self.root / source
        if not base.exists():
            return set()
        return {
            folder.name
            for folder in base.iterdir()
            if folder.is_dir()
            and any(
                f.suffix == ".json" and not f.name.endswith(META_SUFFIX) for f in folder.iterdir()
            )
        }

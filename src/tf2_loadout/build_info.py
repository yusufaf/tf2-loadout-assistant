"""What is deployed, and how old the data baked into it is.

The catalog and prices are build-time snapshots copied into the image, so a bare
"ok" from /healthz can't tell a deploy check which commit is running or how stale
its data is. The version comes from package metadata. The git SHA comes from the
``GIT_SHA`` build arg, because ``.dockerignore`` keeps ``.git`` out of the build
context. Data ages are the cache files' mtimes: the live tests write those files,
and Docker's COPY is relied on to keep the mtimes. /healthz is public, so nothing
path-, env- or secret-shaped goes in the report.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as dist_version
from pathlib import Path

from tf2_loadout.catalog import EQUIP_CACHE, SCHEMA_CACHE
from tf2_loadout.pricing import PRICES_CACHE

DIST_NAME = "tf2-loadout-assistant"
GIT_SHA_ENV = "GIT_SHA"
DATA_FILES = {"schema_items": SCHEMA_CACHE, "equip": EQUIP_CACHE, "prices": PRICES_CACHE}


@dataclass(frozen=True)
class BuildInfo:
    version: str | None = None
    git_sha: str | None = None
    # DATA_FILES key -> the file's mtime, unix seconds.
    data_modified: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def collect(
        cls, cache_dir: str | Path, environ: Mapping[str, str] = os.environ
    ) -> BuildInfo:
        try:
            version = dist_version(DIST_NAME)
        except PackageNotFoundError:
            version = None
        # The Dockerfile's ENV GIT_SHA=${GIT_SHA} sets "" when no build arg is passed.
        git_sha = environ.get(GIT_SHA_ENV, "").strip() or None
        # Boot has already read all three files, so a missing one can't happen here.
        data_modified = {
            name: int((Path(cache_dir) / filename).stat().st_mtime)
            for name, filename in DATA_FILES.items()
        }
        return cls(version, git_sha, data_modified)

    def report(self, now: float) -> dict:
        # No clamping: a negative age shows clock skew rather than hiding it.
        return {
            "version": self.version,
            "git_sha": self.git_sha,
            "data": {
                name: {"modified": modified, "age_seconds": int(now) - modified}
                for name, modified in self.data_modified.items()
            },
        }

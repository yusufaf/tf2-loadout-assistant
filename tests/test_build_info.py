"""BuildInfo: what /healthz says is deployed and how old its baked-in data is."""

from __future__ import annotations

import importlib.metadata
import os

import pytest

from tf2_loadout import build_info
from tf2_loadout.build_info import DATA_FILES, BuildInfo


def _write_data_files(tmp_path, mtime: int = 1_700_000_000) -> None:
    for offset, name in enumerate(DATA_FILES.values()):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (mtime + offset, mtime + offset))


def test_collect_reads_version_sha_and_mtimes(tmp_path):
    _write_data_files(tmp_path)
    info = BuildInfo.collect(tmp_path, environ={"GIT_SHA": "abc123"})
    assert info.version == importlib.metadata.version("tf2-loadout-assistant")
    assert info.git_sha == "abc123"
    assert info.data_modified == {
        "schema_items": 1_700_000_000,
        "equip": 1_700_000_001,
        "prices": 1_700_000_002,
    }


@pytest.mark.parametrize("environ", [{}, {"GIT_SHA": "  "}])
def test_blank_or_missing_git_sha_is_none(tmp_path, environ):
    _write_data_files(tmp_path)
    assert BuildInfo.collect(tmp_path, environ=environ).git_sha is None


def test_report_ages_against_now():
    report = BuildInfo("0.1.0", "abc", {"prices": 1000}).report(now=4600.9)
    assert report == {
        "version": "0.1.0",
        "git_sha": "abc",
        "data": {"prices": {"modified": 1000, "age_seconds": 3600}},
    }


def test_a_missing_data_file_is_left_out(tmp_path):
    (tmp_path / DATA_FILES["prices"]).write_text("{}", encoding="utf-8")
    assert set(BuildInfo.collect(tmp_path, environ={}).data_modified) == {"prices"}


def test_version_is_none_without_package_metadata(tmp_path, monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(build_info, "dist_version", missing)
    assert BuildInfo.collect(tmp_path, environ={}).version is None

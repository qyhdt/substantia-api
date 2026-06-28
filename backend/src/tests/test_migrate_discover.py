# -*- coding: utf-8 -*-
"""migrate._discover 的纯逻辑单测（无需 DB）：排序、跳过非法名、撞号 fail-fast。"""
import importlib

import pytest

from db import migrate


def _write(d, name: str) -> None:
    (d / name).write_text("-- noop\n", encoding="utf-8")


def test_discover_orders_and_skips_bad_names(tmp_path, monkeypatch):
    _write(tmp_path, "0002_b.sql")
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "not_a_migration.sql")   # 非法名 → 跳过
    _write(tmp_path, "README.md")             # 非 .sql → glob 不取
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

    found = migrate._discover()
    assert [v for v, _ in found] == ["0001", "0002"]


def test_discover_raises_on_duplicate_version(tmp_path, monkeypatch):
    # 复现真实事故：两个 0008_*.sql 撞号，必须 fail-fast 而非静默跳过。
    _write(tmp_path, "0008_cache_pricing.sql")
    _write(tmp_path, "0008_signup_grants.sql")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)

    with pytest.raises(migrate.DuplicateMigrationError) as ei:
        migrate._discover()
    # 报错要点出两个冲突文件名，方便定位
    msg = str(ei.value)
    assert "0008" in msg
    assert "0008_cache_pricing.sql" in msg and "0008_signup_grants.sql" in msg


def test_real_migrations_dir_has_no_duplicates():
    # 守卫真实 migrations 目录：当前不应有撞号（防止再次合入冲突编号）。
    importlib.reload(migrate)  # 确保用真实 MIGRATIONS_DIR
    versions = [v for v, _ in migrate._discover()]
    assert len(versions) == len(set(versions)), f"migrations 目录存在撞号: {versions}"

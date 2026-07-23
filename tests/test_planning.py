
from __future__ import annotations

import os

from conftest import needs_rsync
from pardusbackup import (
    DEFAULT_SYSTEM_EXCLUDES,
    build_excludes,
    delete_snapshot,
    list_snapshots,
    plan_snapshot,
    self_backup_exclude,
    take_snapshot,
)


def test_hedef_kaynagin_disindaysa_koruma_yok(tmp_path):
    assert self_backup_exclude(str(tmp_path / "src"), str(tmp_path / "dst")) is None


def test_hedef_kaynagin_icindeyse_koruma_uretilir(tmp_path):
    src = str(tmp_path / "src")
    dst = os.path.join(src, "yedekler")
    assert self_backup_exclude(src, dst) == "yedekler/*"


def test_ic_ice_hedef_derin_yol(tmp_path):
    src = str(tmp_path / "src")
    dst = os.path.join(src, "a", "b")
    assert self_backup_exclude(src, dst) == "a/b/*"


def test_build_excludes_sistem_desenleri_ekler(tmp_path):
    patterns = build_excludes(
        str(tmp_path / "src"), str(tmp_path / "dst"), system_excludes=True
    )
    for pattern in DEFAULT_SYSTEM_EXCLUDES:
        assert pattern in patterns


def test_build_excludes_varsayilan_bos(tmp_path):
    assert build_excludes(str(tmp_path / "src"), str(tmp_path / "dst")) == []


def test_build_excludes_ekstra_ve_bos_atlanir(tmp_path):
    patterns = build_excludes(
        str(tmp_path / "src"), str(tmp_path / "dst"), extra=["*.tmp", "", "*.log"]
    )
    assert patterns == ["*.tmp", "*.log"]


def test_plan_snapshot_ilk_yedekte_link_dest_yok(workspace):
    config = plan_snapshot(workspace.source, workspace.snapshots)
    assert config.link_dest_path is None
    assert config.target_path.startswith(workspace.snapshots)


@needs_rsync
def test_plan_snapshot_sonraki_yedekte_link_dest_bulur(workspace):
    workspace.age()
    first = take_snapshot(plan_snapshot(workspace.source, workspace.snapshots))
    assert first.success

    config = plan_snapshot(workspace.source, workspace.snapshots)
    assert config.link_dest_path == first.snapshot_path


@needs_rsync
def test_delete_snapshot_dizini_ve_metadatayi_siler(workspace):
    workspace.age()
    result = take_snapshot(plan_snapshot(workspace.source, workspace.snapshots))
    assert result.success

    deleted = delete_snapshot(result.snapshot_path)
    assert deleted.success
    assert not os.path.exists(result.snapshot_path)
    assert not os.path.exists(result.info_json_path)
    assert list_snapshots(workspace.snapshots).snapshots == []


@needs_rsync
def test_silinen_snapshot_digerini_bozmaz(workspace):
    """Hardlink paylaşımı: bir yedeği silmek diğerinin verisini yok etmez."""
    import subprocess

    subprocess.run(
        [
            "dd",
            "if=/dev/zero",
            f"of={workspace.source}/big.bin",
            "bs=1024",
            "count=512",
            "status=none",
        ],
        check=True,
    )
    workspace.age()

    first = take_snapshot(plan_snapshot(workspace.source, workspace.snapshots))
    assert first.success
    second = take_snapshot(plan_snapshot(workspace.source, workspace.snapshots))
    assert second.success
    assert second.snapshot_path != first.snapshot_path

    assert delete_snapshot(first.snapshot_path).success

    kalan = os.path.join(second.snapshot_path, "big.bin")
    assert os.path.exists(kalan)
    assert os.path.getsize(kalan) == 512 * 1024


def test_delete_snapshot_olmayan_yol(tmp_path):
    result = delete_snapshot(str(tmp_path / "yok"))
    assert not result.success
    assert "bulunamadı" in result.message
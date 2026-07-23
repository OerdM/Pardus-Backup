
from __future__ import annotations
import json
import os
import subprocess
import time
import pytest
from conftest import itemize_diag, needs_rsync
from pardusbackup import (
    SnapshotConfig,
    compute_dir_stats,
    find_latest_snapshot,
    list_snapshots,
    make_timestamp,
    take_snapshot,
)


def cfg_for(ws, **kwargs) -> SnapshotConfig:
    defaults = dict(source_path=ws.source, target_path=ws.target())
    defaults.update(kwargs)
    return SnapshotConfig(**defaults)


def inode(path: str) -> int:
    return os.stat(path).st_ino


def test_timestamp_bicimi():
    ts = make_timestamp(1751803600)
    assert len(ts) == 19
    assert ts[4] == "-" and ts[7] == "-" and ts[10] == "_"
    assert ts[13] == "-" and ts[16] == "-"


@needs_rsync
def test_tam_snapshot_basarili_ve_metadata_yazilir(workspace):
    result = take_snapshot(cfg_for(workspace))
    assert result.success
    assert os.path.exists(os.path.join(result.snapshot_path, "a.txt"))
    assert os.path.exists(result.info_json_path)
    assert result.info_json_path == result.snapshot_path + ".json"


@needs_rsync
def test_incrementalda_degismeyen_dosya_hardlink_olur(workspace):
    """Mekanizmanın asıl kanıtı: değişmeyen dosya AYNI inode'u paylaşmalı."""
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
    with open(os.path.join(workspace.source, "note.txt"), "w") as f:
        f.write("v1\n")
    workspace.age()
    r1 = take_snapshot(cfg_for(workspace, target_path=workspace.target("snap1")))
    assert r1.success
    with open(os.path.join(workspace.source, "note.txt"), "w") as f:
        f.write("v2\n")
    r2 = take_snapshot(
        cfg_for(
            workspace,
            target_path=workspace.target("snap2"),
            link_dest_path=r1.snapshot_path,
        )
    )
    assert r2.success
    big1 = inode(os.path.join(r1.snapshot_path, "big.bin"))
    big2 = inode(os.path.join(r2.snapshot_path, "big.bin"))
    assert big1 == big2, "big.bin hardlink'lenmedi.\n" + itemize_diag(
        workspace.source, r1.snapshot_path, workspace.root
    )
    note1 = inode(os.path.join(r1.snapshot_path, "note.txt"))
    note2 = inode(os.path.join(r2.snapshot_path, "note.txt"))
    assert note1 != note2


@needs_rsync
def test_zaten_var_olan_hedef_reddedilir(workspace):
    assert take_snapshot(cfg_for(workspace)).success
    result = take_snapshot(cfg_for(workspace))
    assert not result.success
    assert "zaten var" in result.error_output


def test_gecersiz_kaynakta_metadata_yazilmaz_dizin_temizlenir(workspace):
    target = workspace.target("basarisiz")
    result = take_snapshot(
        cfg_for(
            workspace,
            source_path=os.path.join(workspace.root, "olmayan"),
            target_path=target,
        )
    )
    assert not result.success
    assert not os.path.exists(target)
    assert not os.path.exists(target + ".json")


@needs_rsync
def test_metadata_icerigi_dogru(workspace):
    result = take_snapshot(cfg_for(workspace, exclude_patterns=["*.tmp"]))
    assert result.success
    with open(result.info_json_path, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["completed"] is True
    assert meta["incremental"] is False
    assert meta["linkDest"] is None
    assert meta["source"] == workspace.source
    assert meta["excludes"] == ["*.tmp"]


def test_dir_stats_dosya_sayisi_ve_boyut(workspace):
    subprocess.run(
        [
            "dd",
            "if=/dev/zero",
            f"of={workspace.source}/a.bin",
            "bs=1024",
            "count=100",
            "status=none",
        ],
        check=True,
    )
    os.makedirs(os.path.join(workspace.source, "sub"))
    with open(os.path.join(workspace.source, "sub", "c.txt"), "w") as f:
        f.write("x\n")
    stats = compute_dir_stats(workspace.source)
    assert stats.file_count == 3
    assert stats.apparent_bytes >= 100 * 1024
    assert stats.disk_usage_bytes > 0


def test_dir_stats_hardlink_iki_kez_saymaz(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    subprocess.run(
        [
            "dd",
            "if=/dev/zero",
            f"of={d}/orig.bin",
            "bs=1024",
            "count=200",
            "status=none",
        ],
        check=True,
    )
    os.link(d / "orig.bin", d / "link.bin")
    stats = compute_dir_stats(str(d))
    assert stats.file_count == 2
    assert stats.apparent_bytes >= 400 * 1024
    assert stats.disk_usage_bytes < 300 * 1024


def test_dir_stats_olmayan_dizin_bos_doner():
    stats = compute_dir_stats("/kesinlikle/olmayan/dizin")
    assert stats.file_count == 0
    assert stats.disk_usage_bytes == 0


def test_olmayan_kok_bos_sonuc():
    result = list_snapshots("/kesinlikle_yok_12345")
    assert result.snapshots == []
    assert "bulunamadı" in result.message


@needs_rsync
def test_tamamlanmislar_okunur_yarimlar_atlanir(workspace):
    workspace.age()
    assert take_snapshot(cfg_for(workspace)).success
    os.makedirs(os.path.join(workspace.snapshots, "yarim"))
    with open(os.path.join(workspace.snapshots, "yarim", "f"), "w") as f:
        f.write("x")
    result = list_snapshots(workspace.snapshots)
    assert len(result.snapshots) == 1
    snap = result.snapshots[0]
    assert snap.metadata_valid
    assert snap.source == workspace.source
    assert snap.incremental is False
    assert snap.file_count >= 1


def test_yetim_metadata_atlanir(workspace):
    """Dizini olmayan .json sayılmaz ama mesajda bildirilir."""
    with open(os.path.join(workspace.snapshots, "yetim.json"), "w") as f:
        f.write("{}")
    result = list_snapshots(workspace.snapshots)
    assert result.snapshots == []
    assert "atlandı" in result.message


@needs_rsync
def test_en_yeni_once_siralanir(workspace):
    workspace.age()
    r1 = take_snapshot(cfg_for(workspace, target_path=workspace.target("s1")))
    assert r1.success
    time.sleep(1)
    r2 = take_snapshot(
        cfg_for(
            workspace,
            target_path=workspace.target("s2"),
            link_dest_path=r1.snapshot_path,
        )
    )
    assert r2.success
    result = list_snapshots(workspace.snapshots)
    assert len(result.snapshots) == 2
    assert result.snapshots[0].created_unix >= result.snapshots[1].created_unix
    assert result.snapshots[0].snapshot_path.endswith("s2")


def test_bozuk_metadata_listelenir_ama_isaretlenir(workspace):
    os.makedirs(os.path.join(workspace.snapshots, "bozuk"))
    with open(os.path.join(workspace.snapshots, "bozuk", "f.txt"), "w") as f:
        f.write("icerik\n")
    with open(os.path.join(workspace.snapshots, "bozuk.json"), "w") as f:
        f.write("bu gecerli json degil {")
    result = list_snapshots(workspace.snapshots)
    assert len(result.snapshots) == 1
    assert not result.snapshots[0].metadata_valid
    assert result.snapshots[0].file_count >= 1


@needs_rsync
def test_fast_mod_disk_taramasini_atlar(workspace):
    workspace.age()
    assert take_snapshot(cfg_for(workspace)).success
    result = list_snapshots(workspace.snapshots, compute_sizes=False)
    assert len(result.snapshots) == 1
    assert result.snapshots[0].file_count == 0


@needs_rsync
def test_find_latest_snapshot(workspace):
    workspace.age()
    assert take_snapshot(cfg_for(workspace, target_path=workspace.target("s1"))).success
    time.sleep(1)
    assert take_snapshot(cfg_for(workspace, target_path=workspace.target("s2"))).success
    latest = find_latest_snapshot(workspace.snapshots)
    assert latest is not None
    assert latest.snapshot_path.endswith("s2")


def test_find_latest_bos_dizinde_none(workspace):
    assert find_latest_snapshot(workspace.snapshots) is None
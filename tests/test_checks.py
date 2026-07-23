
from __future__ import annotations
import os
import subprocess
import pytest
from conftest import needs_rsync
from pardusbackup import (
    CheckStatus,
    SnapshotConfig,
    check_snapshot,
    estimate_size,
    preflight,
    run_rsync,
    take_snapshot,
)


def cfg_for(ws, **kwargs) -> SnapshotConfig:
    defaults = dict(source_path=ws.source, target_path=ws.target())
    defaults.update(kwargs)
    return SnapshotConfig(**defaults)


def test_gecerli_ilk_snapshot_ok(workspace):
    result = check_snapshot(cfg_for(workspace))
    assert result.status is CheckStatus.OK
    assert result.available_bytes > 0


def test_gecerli_incremental_ayni_fs_ok(workspace):
    prev = os.path.join(workspace.snapshots, "prev")
    os.makedirs(prev)
    result = check_snapshot(cfg_for(workspace, link_dest_path=prev))
    assert result.status is CheckStatus.OK


def test_bos_kaynak(workspace):
    result = check_snapshot(cfg_for(workspace, source_path=""))
    assert result.status is CheckStatus.SOURCE_NOT_ACCESSIBLE


def test_olmayan_kaynak(workspace):
    result = check_snapshot(
        cfg_for(workspace, source_path=os.path.join(workspace.root, "yok"))
    )
    assert result.status is CheckStatus.SOURCE_NOT_ACCESSIBLE


def test_olmayan_hedef_parent(workspace):
    result = check_snapshot(
        cfg_for(workspace, target_path=os.path.join(workspace.root, "yok", "snap"))
    )
    assert result.status is CheckStatus.TARGET_NOT_WRITABLE


def test_olmayan_link_dest(workspace):
    result = check_snapshot(
        cfg_for(workspace, link_dest_path=os.path.join(workspace.snapshots, "yok"))
    )
    assert result.status is CheckStatus.LINK_DEST_MISSING


def test_link_dest_dizin_degil(workspace):
    dosya = os.path.join(workspace.snapshots, "dosya")
    with open(dosya, "w") as f:
        f.write("x")
    result = check_snapshot(cfg_for(workspace, link_dest_path=dosya))
    assert result.status is CheckStatus.LINK_DEST_MISSING


def test_yetersiz_disk_alani(workspace):
    devasa = 10**18
    result = check_snapshot(cfg_for(workspace), devasa)
    assert result.status is CheckStatus.INSUFFICIENT_DISK_SPACE
    assert result.estimated_needed_bytes == devasa


def test_tahmin_sifirsa_alan_karari_atlanir(workspace):
    result = check_snapshot(cfg_for(workspace), 0)
    assert result.status is CheckStatus.OK
    assert result.available_bytes > 0


def test_farkli_filesystem_tespit_edilir(workspace, monkeypatch):
    """KRİTİK kontrol: link-dest farklı fs'teyse hardlink kurulamaz.
    Gerçek iki mount root gerektirdiği için st_dev'i sahteleyerek test ediyoruz;
    mantığın doğruluğu bu şekilde de kanıtlanır.
    """
    prev = os.path.join(workspace.snapshots, "prev")
    os.makedirs(prev)
    gercek_stat = os.stat

    def sahte_stat(path, *args, **kwargs):
        st = gercek_stat(path, *args, **kwargs)
        if os.path.abspath(str(path)) == os.path.abspath(prev):

            class Sahte:
                st_dev = st.st_dev + 1
                st_ino = st.st_ino
                st_mode = st.st_mode

            return Sahte()
        return st

    monkeypatch.setattr(os, "stat", sahte_stat)
    result = check_snapshot(cfg_for(workspace, link_dest_path=prev))
    assert result.status is CheckStatus.NOT_SAME_FILESYSTEM


@needs_rsync
def test_ilk_snapshotta_transferred_esittir_total(workspace):
    subprocess.run(
        [
            "dd",
            "if=/dev/zero",
            f"of={workspace.source}/blob.bin",
            "bs=1024",
            "count=100",
            "status=none",
        ],
        check=True,
    )
    est = estimate_size(cfg_for(workspace))
    assert est.ok
    assert est.transferred_bytes > 0
    assert est.transferred_bytes == est.total_bytes


@needs_rsync
def test_incrementalda_degismeyen_dosyalar_aktarimdan_duser(workspace):
    subprocess.run(
        [
            "dd",
            "if=/dev/zero",
            f"of={workspace.source}/big.bin",
            "bs=1024",
            "count=1024",
            "status=none",
        ],
        check=True,
    )
    (open(os.path.join(workspace.source, "small.txt"), "w")).write("v1\n")
    workspace.age()
    prev = workspace.target("prev")
    assert take_snapshot(cfg_for(workspace, target_path=prev)).success
    with open(os.path.join(workspace.source, "small.txt"), "w") as f:
        f.write("v2_degisti\n")
    est = estimate_size(cfg_for(workspace, link_dest_path=prev))
    assert est.ok
    assert est.total_bytes > 1_000_000
    assert est.transferred_bytes < 100_000


@needs_rsync
def test_preflight_gecerli_config_ok_ve_tahmin_iceriyor(workspace):
    result = preflight(cfg_for(workspace))
    assert result.status is CheckStatus.OK
    assert "Aktarılacak" in result.message


def test_preflight_ucuz_kontrol_basarisizsa_erken_doner(workspace):
    result = preflight(
        cfg_for(workspace, source_path=os.path.join(workspace.root, "yok"))
    )
    assert result.status is CheckStatus.SOURCE_NOT_ACCESSIBLE


@needs_rsync
def test_run_rsync_gecersiz_argumanla_sifirdan_farkli_exit(tmp_path):
    run = run_rsync(["-a", "/kesinlikle/olmayan/yol/", str(tmp_path) + "/"])
    assert run.launched
    assert run.exit_code != 0


def test_run_rsync_bulunamazsa_cokmez(monkeypatch):
    """rsync yoksa exception değil, düzgün bir hata sonucu dönmeli."""

    def sahte_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", sahte_run)
    run = run_rsync(["-a", "/a/", "/b/"])
    assert not run.launched
    assert "rsync" in run.error_message


def test_progress_satiri_ayristirilir():
    from pardusbackup import parse_progress_line

    p = parse_progress_line("   1,234,567  45%   12.34MB/s    0:00:10")
    assert p is not None
    assert p.transferred_bytes == 1234567
    assert p.percent == 45.0
    assert p.speed == "12.34MB/s"
    assert p.eta == "0:00:10"


def test_progress_alakasiz_satir_none_doner():
    from pardusbackup import parse_progress_line

    assert parse_progress_line("sending incremental file list") is None
    assert parse_progress_line("") is None


@needs_rsync
def test_take_snapshot_ilerleme_bildirir(workspace):
    subprocess.run(
        [
            "dd",
            "if=/dev/urandom",
            f"of={workspace.source}/big.bin",
            "bs=1024",
            "count=8192",
            "status=none",
        ],
        check=True,
    )
    updates = []
    result = take_snapshot(cfg_for(workspace), on_progress=updates.append)
    assert result.success
    assert len(updates) >= 1
    assert updates[-1].percent == 100.0


@needs_rsync
def test_iptal_edilince_yarim_snapshot_temizlenir(workspace):
    import threading

    subprocess.run(
        [
            "dd",
            "if=/dev/urandom",
            f"of={workspace.source}/big.bin",
            "bs=1024",
            "count=200000",
            "status=none",
        ],
        check=True,
    )
    cancel = threading.Event()

    def on_progress(p):
        if p.transferred_bytes > 2_000_000:
            cancel.set()

    target = workspace.target("iptal")
    result = take_snapshot(
        cfg_for(workspace, target_path=target),
        on_progress=on_progress,
        cancel_event=cancel,
    )
    assert not result.success
    assert "iptal" in result.error_output.lower()
    assert not os.path.exists(target)
    assert not os.path.exists(target + ".json")
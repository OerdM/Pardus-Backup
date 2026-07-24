
from __future__ import annotations

import json
import os
import subprocess

import pytest

from conftest import needs_rsync
from pardusbackup import list_snapshots, plan_snapshot, take_snapshot

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0, reason="root her dosyayı okuyabildiği için exit 23 tetiklenmez"
)


@needs_rsync
def test_okunamayan_dosya_yedegi_iptal_etmez(tmp_path):
    kaynak = tmp_path / "kaynak"
    hedef = tmp_path / "hedef"
    kaynak.mkdir()
    hedef.mkdir()

    (kaynak / "normal.txt").write_text("veri\n")
    (kaynak / "digeri.txt").write_text("veri2\n")
    gizli = kaynak / "gizli.txt"
    gizli.write_text("gizli\n")
    gizli.chmod(0o000)

    sonuc = take_snapshot(plan_snapshot(str(kaynak), str(hedef)))

    assert sonuc.success
    assert sonuc.partial
    assert sonuc.exit_code == 23
    assert "gizli.txt" in sonuc.warnings
    assert os.path.isdir(sonuc.snapshot_path)
    assert os.path.isfile(os.path.join(sonuc.snapshot_path, "normal.txt"))
    assert os.path.isfile(os.path.join(sonuc.snapshot_path, "digeri.txt"))


@needs_rsync
def test_kismi_yedek_metadataya_islenir(tmp_path):
    kaynak = tmp_path / "kaynak"
    hedef = tmp_path / "hedef"
    kaynak.mkdir()
    hedef.mkdir()
    (kaynak / "normal.txt").write_text("veri\n")
    gizli = kaynak / "gizli.txt"
    gizli.write_text("x\n")
    gizli.chmod(0o000)

    sonuc = take_snapshot(plan_snapshot(str(kaynak), str(hedef)))
    assert sonuc.success

    with open(sonuc.info_json_path, encoding="utf-8") as dosya:
        meta = json.load(dosya)
    assert meta["partial"] is True
    assert meta["rsyncExitCode"] == 23

    kayit = list_snapshots(str(hedef)).snapshots[0]
    assert kayit.partial


@needs_rsync
def test_kismi_yedek_referans_olarak_kullanilabilir(tmp_path):
    """Kısmi bir yedek sonraki artımlı yedeğe temel olabilmeli."""
    kaynak = tmp_path / "kaynak"
    hedef = tmp_path / "hedef"
    kaynak.mkdir()
    hedef.mkdir()
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={kaynak}/buyuk.bin", "bs=1024", "count=256",
         "status=none"], check=True
    )
    gizli = kaynak / "gizli.txt"
    gizli.write_text("x\n")
    gizli.chmod(0o000)
    subprocess.run(
        ["find", str(kaynak), "-exec", "touch", "-d", "2020-01-01 00:00:00", "{}", "+"],
        check=False, capture_output=True,
    )
    subprocess.run(["sync"], check=False)

    ilk = take_snapshot(plan_snapshot(str(kaynak), str(hedef)))
    assert ilk.success and ilk.partial

    ikinci = take_snapshot(plan_snapshot(str(kaynak), str(hedef)))
    assert ikinci.success

    ino1 = os.stat(os.path.join(ilk.snapshot_path, "buyuk.bin")).st_ino
    ino2 = os.stat(os.path.join(ikinci.snapshot_path, "buyuk.bin")).st_ino
    assert ino1 == ino2


@needs_rsync
def test_tamamen_okunabilir_kaynak_kismi_isaretlenmez(tmp_path):
    kaynak = tmp_path / "kaynak"
    hedef = tmp_path / "hedef"
    kaynak.mkdir()
    hedef.mkdir()
    (kaynak / "a.txt").write_text("veri\n")

    sonuc = take_snapshot(plan_snapshot(str(kaynak), str(hedef)))
    assert sonuc.success
    assert not sonuc.partial
    assert sonuc.exit_code == 0
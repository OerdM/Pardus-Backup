
from __future__ import annotations

import os
import subprocess

from conftest import needs_rsync
from pardusbackup import (
    RestoreStatus,
    build_restore_args,
    check_restore,
    plan_snapshot,
    restore_snapshot,
    take_snapshot,
)


def test_restore_argumanlarinda_delete_asla_yok():
    """Bu modun tek güvenlik garantisi: hiçbir şey silinemez."""
    args = build_restore_args("/y/snap", "/hedef")
    assert "--delete" not in args
    assert not any(a.startswith("--delete") for a in args)


def test_restore_kaynak_icerik_kopyala_bicimi():
    args = build_restore_args("/y/snap", "/hedef")
    assert args[-2] == "/y/snap/"
    assert args[-1] == "/hedef"


def test_olmayan_yedek_reddedilir(tmp_path):
    sonuc = check_restore(str(tmp_path / "yok"), str(tmp_path / "hedef"))
    assert sonuc.status is RestoreStatus.SNAPSHOT_NOT_FOUND


def test_yarim_yedek_reddedilir(tmp_path):
    """Bilgi dosyası olmayan dizin tamamlanmamış sayılır."""
    yarim = tmp_path / "yarim"
    yarim.mkdir()
    (yarim / "f.txt").write_text("veri\n")
    sonuc = check_restore(str(yarim), str(tmp_path / "hedef"))
    assert sonuc.status is RestoreStatus.SNAPSHOT_INCOMPLETE


@needs_rsync
def test_dolu_hedef_reddedilir(tmp_path):
    kaynak, hedefkok = tmp_path / "kaynak", tmp_path / "yedekler"
    kaynak.mkdir()
    hedefkok.mkdir()
    (kaynak / "a.txt").write_text("veri\n")
    yedek = take_snapshot(plan_snapshot(str(kaynak), str(hedefkok)))
    assert yedek.success

    dolu = tmp_path / "dolu"
    dolu.mkdir()
    (dolu / "mevcut.txt").write_text("dokunma\n")

    sonuc = check_restore(yedek.snapshot_path, str(dolu))
    assert sonuc.status is RestoreStatus.TARGET_NOT_EMPTY
    assert (dolu / "mevcut.txt").read_text() == "dokunma\n"


@needs_rsync
def test_bos_dizine_geri_yukleme(tmp_path):
    kaynak, hedefkok = tmp_path / "kaynak", tmp_path / "yedekler"
    kaynak.mkdir()
    hedefkok.mkdir()
    (kaynak / "a.txt").write_text("birinci\n")
    (kaynak / "alt").mkdir()
    (kaynak / "alt" / "b.txt").write_text("ikinci\n")

    yedek = take_snapshot(plan_snapshot(str(kaynak), str(hedefkok)))
    assert yedek.success

    hedef = tmp_path / "cikti"
    sonuc = restore_snapshot(yedek.snapshot_path, str(hedef))

    assert sonuc.success
    assert (hedef / "a.txt").read_text() == "birinci\n"
    assert (hedef / "alt" / "b.txt").read_text() == "ikinci\n"


@needs_rsync
def test_geri_yukleme_izin_ve_tarihi_korur(tmp_path):
    kaynak, hedefkok = tmp_path / "kaynak", tmp_path / "yedekler"
    kaynak.mkdir()
    hedefkok.mkdir()
    betik = kaynak / "betik.sh"
    betik.write_text("#!/bin/sh\n")
    betik.chmod(0o755)
    subprocess.run(
        ["touch", "-d", "2019-05-05 10:00:00", str(betik)], check=False
    )

    yedek = take_snapshot(plan_snapshot(str(kaynak), str(hedefkok)))
    assert yedek.success

    hedef = tmp_path / "cikti"
    assert restore_snapshot(yedek.snapshot_path, str(hedef)).success

    geri = hedef / "betik.sh"
    assert oct(geri.stat().st_mode)[-3:] == "755"
    assert int(geri.stat().st_mtime) == int(betik.stat().st_mtime)


@needs_rsync
def test_coklu_kaynakli_yedek_yapisiyla_geri_gelir(tmp_path):
    belgeler, resimler = tmp_path / "Belgeler", tmp_path / "Resimler"
    belgeler.mkdir()
    resimler.mkdir()
    (belgeler / "tez.txt").write_text("tez\n")
    (resimler / "foto.txt").write_text("foto\n")
    hedefkok = tmp_path / "yedekler"
    hedefkok.mkdir()

    yedek = take_snapshot(
        plan_snapshot([str(belgeler), str(resimler)], str(hedefkok))
    )
    assert yedek.success

    hedef = tmp_path / "cikti"
    assert restore_snapshot(yedek.snapshot_path, str(hedef)).success
    assert (hedef / "Belgeler" / "tez.txt").exists()
    assert (hedef / "Resimler" / "foto.txt").exists()


@needs_rsync
def test_olmayan_hedef_dizin_olusturulur(tmp_path):
    kaynak, hedefkok = tmp_path / "kaynak", tmp_path / "yedekler"
    kaynak.mkdir()
    hedefkok.mkdir()
    (kaynak / "a.txt").write_text("veri\n")
    yedek = take_snapshot(plan_snapshot(str(kaynak), str(hedefkok)))

    hedef = tmp_path / "henuz" / "yok"
    sonuc = restore_snapshot(yedek.snapshot_path, str(hedef))
    assert sonuc.success
    assert (hedef / "a.txt").exists()


@needs_rsync
def test_geri_yukleme_yedegi_degistirmez(tmp_path):
    """Geri yükleme kaynak yedeğe dokunmamalı."""
    kaynak, hedefkok = tmp_path / "kaynak", tmp_path / "yedekler"
    kaynak.mkdir()
    hedefkok.mkdir()
    (kaynak / "a.txt").write_text("veri\n")
    yedek = take_snapshot(plan_snapshot(str(kaynak), str(hedefkok)))

    once = sorted(os.listdir(yedek.snapshot_path))
    ino_once = os.stat(os.path.join(yedek.snapshot_path, "a.txt")).st_ino

    assert restore_snapshot(yedek.snapshot_path, str(tmp_path / "cikti")).success

    assert sorted(os.listdir(yedek.snapshot_path)) == once
    assert os.stat(os.path.join(yedek.snapshot_path, "a.txt")).st_ino == ino_once

from __future__ import annotations

import json
import os
import subprocess

from conftest import needs_rsync
from pardusbackup import (
    CheckStatus,
    SnapshotConfig,
    build_rsync_args,
    check_snapshot,
    list_snapshots,
    plan_snapshot,
    take_snapshot,
)


def test_tek_kaynak_icerik_kopyala_bicimi():
    args = build_rsync_args(SnapshotConfig(source_path="/a/Belgeler", target_path="/t"))
    assert args[-2] == "/a/Belgeler/"


def test_coklu_kaynak_sondaki_slash_almaz():
    """Her kaynak snapshot içinde kendi adıyla ayrı girdi olmalı."""
    args = build_rsync_args(
        SnapshotConfig(target_path="/t", sources=["/a/Belgeler", "/a/Resimler"])
    )
    assert args[-3] == "/a/Belgeler"
    assert args[-2] == "/a/Resimler"
    assert args[-1] == "/t"


def test_coklu_kaynakta_bos_yollar_atlanir():
    cfg = SnapshotConfig(target_path="/t", sources=["/a", "", "/b"])
    assert cfg.resolved_sources() == ["/a", "/b"]


def test_sources_bossa_source_patha_dusulur():
    cfg = SnapshotConfig(source_path="/a", target_path="/t")
    assert cfg.resolved_sources() == ["/a"]


def test_ayni_adli_iki_kaynak_reddedilir(tmp_path):
    """/x/Belgeler ile /y/Belgeler tek yedekte birleşir; önceden yakalanmalı."""
    (tmp_path / "x" / "Belgeler").mkdir(parents=True)
    (tmp_path / "y" / "Belgeler").mkdir(parents=True)
    (tmp_path / "hedef").mkdir()

    cfg = SnapshotConfig(
        target_path=str(tmp_path / "hedef" / "snap"),
        sources=[str(tmp_path / "x" / "Belgeler"), str(tmp_path / "y" / "Belgeler")],
    )
    sonuc = check_snapshot(cfg)
    assert sonuc.status is CheckStatus.SOURCE_NAME_COLLISION


def test_farkli_adli_kaynaklar_kabul_edilir(tmp_path):
    (tmp_path / "Belgeler").mkdir()
    (tmp_path / "Resimler").mkdir()
    (tmp_path / "hedef").mkdir()
    cfg = SnapshotConfig(
        target_path=str(tmp_path / "hedef" / "snap"),
        sources=[str(tmp_path / "Belgeler"), str(tmp_path / "Resimler")],
    )
    assert check_snapshot(cfg).status is CheckStatus.OK


def test_okunamayan_kaynak_yakalanir(tmp_path):
    (tmp_path / "var").mkdir()
    (tmp_path / "hedef").mkdir()
    cfg = SnapshotConfig(
        target_path=str(tmp_path / "hedef" / "snap"),
        sources=[str(tmp_path / "var"), str(tmp_path / "yok")],
    )
    assert check_snapshot(cfg).status is CheckStatus.SOURCE_NOT_ACCESSIBLE


@needs_rsync
def test_coklu_kaynak_yedegi_alinir(tmp_path):
    belgeler = tmp_path / "Belgeler"
    resimler = tmp_path / "Resimler"
    belgeler.mkdir()
    resimler.mkdir()
    (belgeler / "tez.txt").write_text("tez\n")
    (resimler / "foto.txt").write_text("foto\n")
    tek_dosya = tmp_path / "notlar.txt"
    tek_dosya.write_text("not\n")
    hedef = tmp_path / "hedef"
    hedef.mkdir()

    cfg = plan_snapshot([str(belgeler), str(resimler), str(tek_dosya)], str(hedef))
    sonuc = take_snapshot(cfg)
    assert sonuc.success

    assert os.path.isdir(os.path.join(sonuc.snapshot_path, "Belgeler"))
    assert os.path.isdir(os.path.join(sonuc.snapshot_path, "Resimler"))
    assert os.path.isfile(os.path.join(sonuc.snapshot_path, "notlar.txt"))


@needs_rsync
def test_coklu_kaynakta_artimli_hardlink_calisir(tmp_path):
    belgeler = tmp_path / "Belgeler"
    resimler = tmp_path / "Resimler"
    belgeler.mkdir()
    resimler.mkdir()
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={resimler}/buyuk.bin", "bs=1024", "count=512",
         "status=none"], check=True
    )
    (belgeler / "not.txt").write_text("v1\n")
    hedef = tmp_path / "hedef"
    hedef.mkdir()
    subprocess.run(
        ["find", str(belgeler), str(resimler), "-exec", "touch", "-d",
         "2020-01-01 00:00:00", "{}", "+"], check=False
    )
    subprocess.run(["sync"], check=False)

    ilk = take_snapshot(plan_snapshot([str(belgeler), str(resimler)], str(hedef)))
    assert ilk.success

    (belgeler / "not.txt").write_text("v2\n")
    ikinci = take_snapshot(plan_snapshot([str(belgeler), str(resimler)], str(hedef)))
    assert ikinci.success

    buyuk_ilk = os.stat(os.path.join(ilk.snapshot_path, "Resimler", "buyuk.bin")).st_ino
    buyuk_iki = os.stat(
        os.path.join(ikinci.snapshot_path, "Resimler", "buyuk.bin")
    ).st_ino
    assert buyuk_ilk == buyuk_iki


@needs_rsync
def test_metadata_tum_kaynaklari_kaydeder(tmp_path):
    a = tmp_path / "A"
    b = tmp_path / "B"
    a.mkdir()
    b.mkdir()
    (a / "x.txt").write_text("x\n")
    (b / "y.txt").write_text("y\n")
    hedef = tmp_path / "hedef"
    hedef.mkdir()

    sonuc = take_snapshot(plan_snapshot([str(a), str(b)], str(hedef)))
    assert sonuc.success

    with open(sonuc.info_json_path, encoding="utf-8") as dosya:
        meta = json.load(dosya)
    assert meta["sources"] == [str(a), str(b)]

    kayit = list_snapshots(str(hedef)).snapshots[0]
    assert kayit.sources == [str(a), str(b)]


def test_eski_metadata_tek_kaynak_olarak_okunur(tmp_path):
    """sources alanı olmayan eski yedekler de listelenebilmeli."""
    hedef = tmp_path / "hedef"
    hedef.mkdir()
    (hedef / "snap").mkdir()
    (hedef / "snap" / "f.txt").write_text("veri\n")
    eski = {
        "version": 1,
        "createdUnix": 1,
        "createdLocal": "2026-01-01_00-00-00",
        "source": "/home/mer/Belgeler",
        "completed": True,
    }
    (hedef / "snap.json").write_text(json.dumps(eski), encoding="utf-8")

    kayit = list_snapshots(str(hedef)).snapshots[0]
    assert kayit.metadata_valid
    assert kayit.sources == ["/home/mer/Belgeler"]
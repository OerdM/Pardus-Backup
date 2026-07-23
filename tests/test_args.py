
from __future__ import annotations
import pytest
from pardusbackup import SnapshotConfig, build_rsync_args, to_command_string


def base_config(**kwargs) -> SnapshotConfig:
    defaults = dict(
        source_path="/home/mer",
        target_path="/snapshots/2026-07-06_11-00-00",
    )
    defaults.update(kwargs)
    return SnapshotConfig(**defaults)


def test_ilk_snapshot_link_dest_eklenmez():
    args = build_rsync_args(base_config())
    assert not any(a.startswith("--link-dest=") for a in args)


def test_incremental_link_dest_normalize_edilir():
    args = build_rsync_args(
        base_config(link_dest_path="/snapshots/2026-07-06_10-00-00/")
    )
    assert "--link-dest=/snapshots/2026-07-06_10-00-00" in args


@pytest.mark.parametrize("source", ["/home/mer", "/home/mer/", "/home/mer//"])
def test_kaynak_daima_tek_slash_ile_biter(source):
    """rsync'te 'src/' içeriği kopyalar, 'src' dizinin kendisini kopyalar."""
    args = build_rsync_args(base_config(source_path=source))
    assert "/home/mer/" in args
    assert "/home/mer//" not in args


def test_root_kaynak_bozulmaz():
    args = build_rsync_args(base_config(source_path="/"))
    assert "/" in args
    assert "//" not in args


def test_bos_kaynak_roota_cevrilmez():
    """Felaket önleme: boş kaynak sessizce tüm sistemi yedeklemeye kalkmamalı."""
    args = build_rsync_args(base_config(source_path=""))
    assert "" in args
    assert "/" not in args


def test_hedef_sondaki_slashlar_temizlenir():
    args = build_rsync_args(base_config(target_path="/snapshots/xyz///"))
    assert "/snapshots/xyz" in args


def test_bos_exclude_listesi_hic_exclude_uretmez():
    args = build_rsync_args(base_config(exclude_patterns=[]))
    assert not any(a.startswith("--exclude=") for a in args)


def test_exclude_sirasi_korunur_boslar_atlanir():
    args = build_rsync_args(
        base_config(exclude_patterns=["/proc/*", "", "/sys/*", "/dev/*"])
    )
    assert "--exclude=/proc/*" in args
    assert "--exclude=/sys/*" in args
    assert "--exclude=/dev/*" in args
    assert "--exclude=" not in args
    assert args.index("--exclude=/proc/*") < args.index("--exclude=/sys/*")


def test_varsayilan_bayraklar():
    args = build_rsync_args(base_config())
    assert "--delete" in args
    assert "--numeric-ids" in args


def test_delete_kapatilabilir():
    args = build_rsync_args(base_config(use_delete=False))
    assert "--delete" not in args


def test_dry_run_stats_progress_bayraklari():
    args = build_rsync_args(base_config(dry_run=True, stats=True, show_progress=True))
    assert "--dry-run" in args
    assert "--stats" in args
    assert "--info=progress2" in args


def test_stats_varsayilan_kapali():
    assert "--stats" not in build_rsync_args(base_config())


def test_kisa_bayraklar_tek_tokende_birlesir():
    assert "-aHAX" in build_rsync_args(base_config())
    assert "-aHAXx" in build_rsync_args(base_config(one_file_system=True))
    args = build_rsync_args(
        base_config(
            preserve_hardlinks=False, preserve_acls=False, preserve_xattrs=False
        )
    )
    assert "-a" in args
    assert "-aHAX" not in args


def test_kaynak_ve_hedef_daima_son_iki_arguman():
    args = build_rsync_args(
        base_config(link_dest_path="/snapshots/prev", exclude_patterns=["/proc/*"])
    )
    assert args[-2] == "/home/mer/"
    assert args[-1] == "/snapshots/2026-07-06_11-00-00"


def test_bosluklu_pathler_tek_arguman_kalir():
    """Argüman listesi exec'e gittiği için shell quoting gerekmez."""
    args = build_rsync_args(
        base_config(
            source_path="/home/mer/My Documents",
            target_path="/snapshots/back up 01",
        )
    )
    assert "/home/mer/My Documents/" in args
    assert "/snapshots/back up 01" in args


def test_to_command_string_sadece_gosterim():
    args = build_rsync_args(base_config(source_path="/home/mer/My Documents"))
    text = to_command_string(args)
    assert text.startswith("rsync ")
    assert '"/home/mer/My Documents/"' in text
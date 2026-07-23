
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .backend import preflight, take_snapshot
from .config import human_bytes
from .listing import SnapshotInfo, delete_snapshot, list_snapshots
from .planning import plan_snapshot

GUI_INSTALL_HINT = "Kurulum: sudo apt install python3-gi gir1.2-gtk-3.0"


def _plan(args: argparse.Namespace):
    return plan_snapshot(
        source=args.source,
        dest=args.dest,
        system_excludes=args.system_excludes,
        extra_excludes=args.exclude,
        one_file_system=args.one_file_system,
    )


def cmd_check(args: argparse.Namespace) -> int:
    config = _plan(args)
    result = preflight(config)
    print(f"Durum : {result.status.value}")
    print(f"Mesaj : {result.message}")
    kaynaklar = config.resolved_sources()
    print(f"Kaynak  : {len(kaynaklar)} yol")
    for yol in kaynaklar:
        print(f"          {yol}")
    if config.link_dest_path:
        print(f"Referans: {config.link_dest_path}")
    else:
        print("Referans: yok -> ilk yedek tam kopya olacak")
    return 0 if result.ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    os.makedirs(args.dest, exist_ok=True)
    config = _plan(args)

    if config.link_dest_path:
        print(f"Artımlı yedek (referans: {os.path.basename(config.link_dest_path)})")
    else:
        print("İlk yedek (tam kopya)")

    if not args.skip_preflight:
        check = preflight(config)
        print(f"Ön kontrol: {check.status.value} — {check.message}")
        if not check.ok:
            print("Yedekleme iptal edildi.", file=sys.stderr)
            return 1

    print(f"Hedef: {config.target_path}")
    print("rsync çalışıyor...")
    result = take_snapshot(config)
    if not result.success:
        print(f"BAŞARISIZ: {result.error_output}", file=sys.stderr)
        return 1

    print(f"Tamamlandı. Diske eklenen: {human_bytes(result.transferred_bytes)}")
    print(f"Metadata: {result.info_json_path}")
    return 0


def _print_snapshot(snapshot: SnapshotInfo, with_sizes: bool) -> None:
    kind = "artımlı" if snapshot.incremental else "tam"
    label = snapshot.created_local or os.path.basename(snapshot.snapshot_path)
    print(f"{label}  [{kind}]")
    print(f"   dizin   : {snapshot.snapshot_path}")
    if snapshot.metadata_valid:
        kaynaklar = snapshot.sources or [snapshot.source]
        print(f"   kaynak  : {kaynaklar[0]}")
        for yol in kaynaklar[1:]:
            print(f"             {yol}")
    else:
        print("   kaynak  : (metadata okunamadı)")
    if with_sizes:
        print(
            f"   içerik  : {snapshot.file_count} dosya, "
            f"{human_bytes(snapshot.apparent_bytes)}"
        )
        print(
            f"   disk    : {human_bytes(snapshot.disk_usage_bytes)} "
            f"(bu yedeğin eklediği: {human_bytes(snapshot.transferred_bytes)})"
        )
    print()


def cmd_list(args: argparse.Namespace) -> int:
    listing = list_snapshots(args.dest, compute_sizes=not args.fast)
    print(listing.message)
    if not listing.snapshots:
        return 0
    print()
    for snapshot in listing.snapshots:
        _print_snapshot(snapshot, with_sizes=not args.fast)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    """Bir yedeği siler. --yes verilmezse onay ister."""
    path = args.snapshot.rstrip("/")
    if not os.path.isdir(path):
        print(f"Yedek bulunamadı: {path}", file=sys.stderr)
        return 1

    if not args.yes:
        answer = input(f"{os.path.basename(path)} silinsin mi? [e/H] ").strip().lower()
        if answer not in ("e", "evet", "y", "yes"):
            print("Vazgeçildi.")
            return 0

    result = delete_snapshot(path)
    if not result.success:
        print(result.message, file=sys.stderr)
        return 1
    print(f"{result.message} · {human_bytes(result.freed_bytes)} boşaldı")
    return 0


def cmd_gui(_args: argparse.Namespace) -> int:
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        print(f"GTK arayüzü başlatılamadı: {exc}\n{GUI_INSTALL_HINT}", file=sys.stderr)
        return 1
    return gui_main([])


def _add_dest(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-d", "--dest", required=True, help="Yedeklerin kök dizini")


def _add_source_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-s",
        "--source",
        required=True,
        action="append",
        help="Yedeklenecek dizin veya dosya (birden çok kez verilebilir)",
    )
    parser.add_argument(
        "-e",
        "--exclude",
        action="append",
        help="Hariç tutulacak desen (birden çok kez verilebilir)",
    )
    parser.add_argument(
        "--system-excludes",
        action="store_true",
        help="/proc, /sys, /dev gibi sanal yolları da hariç tut",
    )
    parser.add_argument(
        "-x", "--one-file-system", action="store_true", help="Mount noktalarını geçme"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pardusbackup", description="rsync tabanlı artımlı yedekleme"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Yeni yedek al")
    _add_source_options(backup)
    _add_dest(backup)
    backup.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Disk alanı tahminini atla (daha hızlı başlar)",
    )
    backup.set_defaults(func=cmd_backup)

    check = subparsers.add_parser("check", help="Yedek almadan ön kontrol yap")
    _add_source_options(check)
    _add_dest(check)
    check.set_defaults(func=cmd_check)

    listing = subparsers.add_parser("list", help="Mevcut yedekleri listele")
    _add_dest(listing)
    listing.add_argument(
        "--fast", action="store_true", help="Disk taramasını atla (boyut gösterme)"
    )
    listing.set_defaults(func=cmd_list)

    remove = subparsers.add_parser("delete", help="Bir yedeği sil")
    remove.add_argument("snapshot", help="Silinecek yedek dizini")
    remove.add_argument("-y", "--yes", action="store_true", help="Onay sorma")
    remove.set_defaults(func=cmd_delete)

    gui = subparsers.add_parser("gui", help="GTK arayüzünü başlat")
    gui.set_defaults(func=cmd_gui)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
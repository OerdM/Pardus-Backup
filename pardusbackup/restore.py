
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from .backend import (
    RSYNC_EXIT_PARTIAL,
    RSYNC_OK_EXIT_CODES,
    ProgressCallback,
    _summarize_skipped,
    run_rsync,
    run_rsync_streaming,
)
from .config import human_bytes, normalize_source, strip_trailing_slashes
from .listing import SIDECAR_SUFFIX, compute_dir_stats


class RestoreStatus(Enum):
    OK = "Ok"
    SNAPSHOT_NOT_FOUND = "SnapshotNotFound"
    SNAPSHOT_INCOMPLETE = "SnapshotIncomplete"
    TARGET_NOT_CREATABLE = "TargetNotCreatable"
    TARGET_NOT_WRITABLE = "TargetNotWritable"
    TARGET_NOT_EMPTY = "TargetNotEmpty"
    INSUFFICIENT_DISK_SPACE = "InsufficientDiskSpace"
    FAILED = "Failed"


@dataclass
class RestoreResult:
    status: RestoreStatus = RestoreStatus.OK
    success: bool = False
    message: str = ""
    snapshot_path: str = ""
    target_path: str = ""
    exit_code: int = -1
    restored_bytes: int = 0
    partial: bool = False
    warnings: str = ""

    @property
    def ok(self) -> bool:
        return self.status is RestoreStatus.OK


def build_restore_args(
    snapshot_path: str,
    target_path: str,
    show_progress: bool = False,
    stats: bool = True,
    dry_run: bool = False,
) -> List[str]:
    """Geri yükleme için rsync argümanlarını üretir.

    SAF fonksiyondur. --delete ASLA eklenmez: bu modun tek güvenlik garantisi,
    hedefte bulunan hiçbir şeyin silinememesidir.
    """
    args = ["-aHAX", "--numeric-ids"]
    if dry_run:
        args.append("--dry-run")
    if stats:
        args.append("--stats")
    if show_progress:
        args.append("--info=progress2")
    args.append(normalize_source(snapshot_path))
    args.append(strip_trailing_slashes(target_path))
    return args


def _is_empty_dir(path: str) -> bool:
    try:
        return not any(os.scandir(path))
    except OSError:
        return False


def check_restore(snapshot_path: str, target_path: str) -> RestoreResult:
    """Geri yükleme ön koşullarını doğrular. Hiçbir şey yazmaz.

    Hedef dizin yoksa oluşturulabilir sayılır; varsa BOŞ olmak zorundadır.
    """
    snapshot = strip_trailing_slashes(snapshot_path)
    target = strip_trailing_slashes(target_path)
    result = RestoreResult(snapshot_path=snapshot, target_path=target)

    if not snapshot or not os.path.isdir(snapshot):
        result.status = RestoreStatus.SNAPSHOT_NOT_FOUND
        result.message = f"Yedek bulunamadı: {snapshot}"
        return result

    if not os.path.isfile(snapshot + SIDECAR_SUFFIX):
        result.status = RestoreStatus.SNAPSHOT_INCOMPLETE
        result.message = (
            "Bu yedek tamamlanmamış (bilgi dosyası yok). Yarıda kesilmiş bir "
            "yedekten geri yükleme yapılamaz."
        )
        return result

    if not target:
        result.status = RestoreStatus.TARGET_NOT_CREATABLE
        result.message = "Hedef dizin seçilmedi."
        return result

    if os.path.exists(target):
        if not os.path.isdir(target):
            result.status = RestoreStatus.TARGET_NOT_CREATABLE
            result.message = f"Hedef bir dizin değil: {target}"
            return result
        if not os.access(target, os.W_OK):
            result.status = RestoreStatus.TARGET_NOT_WRITABLE
            result.message = f"Hedefe yazılamıyor: {target}"
            return result
        if not _is_empty_dir(target):
            result.status = RestoreStatus.TARGET_NOT_EMPTY
            result.message = (
                "Hedef dizin boş değil. Mevcut dosyaların üzerine yazmamak için "
                "geri yükleme yalnızca boş bir dizine yapılabilir."
            )
            return result
        parent_for_space = target
    else:
        ancestor = os.path.dirname(target) or "."
        while ancestor and not os.path.exists(ancestor):
            parent = os.path.dirname(ancestor)
            if parent == ancestor:
                break
            ancestor = parent or "."
        if not os.path.isdir(ancestor) or not os.access(ancestor, os.W_OK):
            result.status = RestoreStatus.TARGET_NOT_CREATABLE
            result.message = f"Hedef dizin oluşturulamaz: {target}"
            return result
        parent_for_space = ancestor

    needed = compute_dir_stats(snapshot).apparent_bytes
    vfs = os.statvfs(parent_for_space)
    available = vfs.f_bavail * vfs.f_frsize
    if needed > available:
        result.status = RestoreStatus.INSUFFICIENT_DISK_SPACE
        result.message = (
            f"Yetersiz disk alanı. Gerekli ~{human_bytes(needed)}, "
            f"kullanılabilir {human_bytes(available)}."
        )
        return result

    result.message = f"Geri yüklenecek: ~{human_bytes(needed)}"
    return result


def restore_snapshot(
    snapshot_path: str,
    target_path: str,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> RestoreResult:
    """Bir yedeğin içeriğini boş bir hedef dizine çıkarır.

    Hedef yoksa oluşturulur. Mevcut hiçbir dosya silinmez veya üzerine
    yazılmaz; ön denetim hedefin boş olmasını şart koşar.
    """
    result = check_restore(snapshot_path, target_path)
    if not result.ok:
        return result

    target = result.target_path
    if not os.path.exists(target):
        try:
            os.makedirs(target, 0o755)
        except OSError as exc:
            result.status = RestoreStatus.TARGET_NOT_CREATABLE
            result.message = f"Hedef dizin oluşturulamadı: {exc}"
            return result

    streaming = on_progress is not None or cancel_event is not None
    args = build_restore_args(
        result.snapshot_path, target, show_progress=streaming, stats=True
    )
    if streaming:
        run = run_rsync_streaming(args, on_progress, cancel_event)
    else:
        run = run_rsync(args)

    if not run.launched:
        result.status = RestoreStatus.FAILED
        result.message = f"rsync başlatılamadı: {run.error_message}"
        return result

    result.exit_code = run.exit_code

    if cancel_event is not None and cancel_event.is_set():
        result.status = RestoreStatus.FAILED
        result.message = "Geri yükleme iptal edildi."
        return result

    if run.exit_code not in RSYNC_OK_EXIT_CODES:
        result.status = RestoreStatus.FAILED
        result.message = (
            f"rsync hata verdi (exit {run.exit_code}): {run.stderr.strip()[:300]}"
        )
        return result

    if run.exit_code == RSYNC_EXIT_PARTIAL:
        result.partial = True
        result.warnings = _summarize_skipped(run.stderr)

    result.restored_bytes = compute_dir_stats(target).apparent_bytes
    result.success = True
    result.message = (
        f"Geri yükleme tamamlandı: {human_bytes(result.restored_bytes)} → {target}"
    )
    return result
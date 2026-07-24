
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Set, Tuple

SIDECAR_SUFFIX = ".json"
_BLOCK_SIZE = 512


@dataclass
class DirStats:
    file_count: int = 0
    apparent_bytes: int = 0
    disk_usage_bytes: int = 0


class _InodeLedger:
    """Aynı inode'u iki kez saymayı önler."""

    def __init__(self) -> None:
        self._seen: Set[Tuple[int, int]] = set()

    def is_new(self, st: os.stat_result) -> bool:
        key = (st.st_dev, st.st_ino)
        if key in self._seen:
            return False
        self._seen.add(key)
        return True


def _lstat_or_none(path: str) -> Optional[os.stat_result]:
    try:
        return os.lstat(path)
    except OSError:
        return None


def _walk_entries(directory: str) -> Iterator[str]:
    for root, dirnames, filenames in os.walk(directory, onerror=lambda _e: None):
        for name in dirnames + filenames:
            yield os.path.join(root, name)


def compute_dir_stats(directory: str) -> DirStats:
    """Dizin ağacındaki dosya sayısını ve boyutları hesaplar.
    disk_usage_bytes her inode'u bir kez sayar; bu değer dizinin tek başına
    `du -s` çıktısına denktir. Diğer snapshot'larla paylaşılan hardlink'ler de
    dahil olduğu için marjinal maliyet değildir; onun için
    SnapshotInfo.transferred_bytes kullanılmalıdır.
    """
    stats = DirStats()
    ledger = _InodeLedger()
    root_st = _lstat_or_none(directory)
    if root_st is None:
        return stats
    if ledger.is_new(root_st):
        stats.disk_usage_bytes += root_st.st_blocks * _BLOCK_SIZE
    for path in _walk_entries(directory):
        st = _lstat_or_none(path)
        if st is None:
            continue
        if ledger.is_new(st):
            stats.disk_usage_bytes += st.st_blocks * _BLOCK_SIZE
        if os.path.isfile(path) and not os.path.islink(path):
            stats.file_count += 1
            stats.apparent_bytes += st.st_size
    return stats


@dataclass
class SnapshotInfo:
    snapshot_path: str
    info_json_path: str
    created_unix: int = 0
    created_local: str = ""
    source: str = ""
    sources: List[str] = field(default_factory=list)
    link_dest: Optional[str] = None
    incremental: bool = False
    excludes: List[str] = field(default_factory=list)
    transferred_bytes: int = 0
    total_bytes: int = 0
    rsync_exit_code: int = 0
    partial: bool = False
    file_count: int = 0
    apparent_bytes: int = 0
    disk_usage_bytes: int = 0
    metadata_valid: bool = True


@dataclass
class ListResult:
    snapshots: List[SnapshotInfo] = field(default_factory=list)
    root_path: str = ""
    message: str = ""


def _read_metadata(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            metadata = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return metadata if isinstance(metadata, dict) else None


def _apply_metadata(info: SnapshotInfo, metadata: Optional[dict]) -> None:
    if metadata is None:
        info.metadata_valid = False
        return
    excludes = metadata.get("excludes", [])
    info.created_unix = int(metadata.get("createdUnix", 0) or 0)
    info.created_local = str(metadata.get("createdLocal", "") or "")
    info.source = str(metadata.get("source", "") or "")
    raw_sources = metadata.get("sources")
    if isinstance(raw_sources, list) and raw_sources:
        info.sources = [str(item) for item in raw_sources]
    elif info.source:
        info.sources = [info.source]
    info.link_dest = metadata.get("linkDest") or None
    info.incremental = bool(metadata.get("incremental", False))
    info.excludes = (
        [str(item) for item in excludes] if isinstance(excludes, list) else []
    )
    info.transferred_bytes = int(metadata.get("transferredBytes", 0) or 0)
    info.total_bytes = int(metadata.get("totalBytes", 0) or 0)
    info.rsync_exit_code = int(metadata.get("rsyncExitCode", 0) or 0)
    info.partial = bool(metadata.get("partial", False))
    info.metadata_valid = True


def _apply_disk_stats(info: SnapshotInfo) -> None:
    stats = compute_dir_stats(info.snapshot_path)
    info.file_count = stats.file_count
    info.apparent_bytes = stats.apparent_bytes
    info.disk_usage_bytes = stats.disk_usage_bytes


def _is_sidecar(entry: os.DirEntry) -> bool:
    if not entry.name.endswith(SIDECAR_SUFFIX):
        return False
    try:
        return entry.is_file()
    except OSError:
        return False


def _snapshot_dir_for(sidecar_path: str) -> str:
    return sidecar_path[: -len(SIDECAR_SUFFIX)]


def list_snapshots(snapshots_root: str, compute_sizes: bool = True) -> ListResult:
    """Kökteki tamamlanmış snapshot'ları en yeni önce listeler.
    Tamamlanmış olmanın ölçütü yanında '<dir>.json' sidecar'ı bulunmasıdır;
    böylece yarıda kesilmiş snapshot'lar elenir.
    """
    result = ListResult(root_path=snapshots_root)
    if not os.path.isdir(snapshots_root):
        result.message = f"Snapshot dizini bulunamadı: {snapshots_root}"
        return result
    try:
        entries = list(os.scandir(snapshots_root))
    except OSError as exc:
        result.message = f"Dizin okunamadı: {snapshots_root} ({exc})"
        return result
    orphan_count = 0
    for entry in entries:
        if not _is_sidecar(entry):
            continue
        snapshot_dir = _snapshot_dir_for(entry.path)
        if not os.path.isdir(snapshot_dir):
            orphan_count += 1
            continue
        info = SnapshotInfo(snapshot_path=snapshot_dir, info_json_path=entry.path)
        _apply_metadata(info, _read_metadata(entry.path))
        if compute_sizes:
            _apply_disk_stats(info)
        result.snapshots.append(info)
    result.snapshots.sort(key=lambda snapshot: snapshot.created_unix, reverse=True)
    result.message = f"Bulunan snapshot: {len(result.snapshots)}"
    if orphan_count:
        result.message += f" (dizini olmayan {orphan_count} metadata atlandı)"
    return result


def find_latest_snapshot(snapshots_root: str) -> Optional[SnapshotInfo]:
    """En son tamamlanmış snapshot'ı döndürür (link-dest adayı)."""
    listing = list_snapshots(snapshots_root, compute_sizes=False)
    for snapshot in listing.snapshots:
        if snapshot.metadata_valid:
            return snapshot
    return listing.snapshots[0] if listing.snapshots else None


@dataclass
class DeleteResult:
    success: bool = False
    snapshot_path: str = ""
    freed_bytes: int = 0
    message: str = ""


def delete_snapshot(snapshot_path: str) -> DeleteResult:
    """Bir snapshot'ı ve yanındaki metadata dosyasını siler.

    Hardlink tabanlı snapshot'larda silme güvenlidir: bir dosya başka
    snapshot'larla inode paylaşıyorsa veri, son bağlantı da silinene kadar
    diskte kalır. Yani bir snapshot'ı silmek diğerlerini bozmaz; sadece
    yalnızca ona ait olan bloklar boşa çıkar.

    Metadata önce silinir. Böylece dizin silinirken işlem yarıda kesilse bile
    geride sidecar'ı olmayan bir dizin kalır ve listeleme onu zaten yok sayar.
    """
    import shutil

    path = snapshot_path.rstrip("/")
    result = DeleteResult(snapshot_path=path)

    if not os.path.isdir(path):
        result.message = f"Snapshot bulunamadı: {path}"
        return result

    freed = compute_dir_stats(path).disk_usage_bytes
    sidecar = path + SIDECAR_SUFFIX

    try:
        if os.path.exists(sidecar):
            os.remove(sidecar)
        shutil.rmtree(path)
    except OSError as exc:
        result.message = f"Silinemedi: {exc}"
        return result

    result.success = True
    result.freed_bytes = freed
    result.message = f"Snapshot silindi: {os.path.basename(path)}"
    return result
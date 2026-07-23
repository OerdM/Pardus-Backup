
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")


@dataclass
class SnapshotConfig:
    """Tek bir snapshot işlemini tanımlar. Doğrulama içermez."""

    source_path: str
    target_path: str
    link_dest_path: Optional[str] = None
    exclude_patterns: List[str] = field(default_factory=list)
    use_delete: bool = True
    numeric_ids: bool = True
    preserve_hardlinks: bool = True
    preserve_acls: bool = True
    preserve_xattrs: bool = True
    one_file_system: bool = False
    show_progress: bool = False
    dry_run: bool = False
    stats: bool = False


def strip_trailing_slashes(path: str) -> str:
    """Sondaki fazla '/' karakterlerini temizler, kökü ('/') korur."""
    if path == "/":
        return "/"
    return path.rstrip("/") or path


def normalize_source(raw: str) -> str:
    """Kaynağı rsync'in içerik-kopyala biçimine getirir (tek sondaki '/').
    'src/' kaynağın içeriğini, 'src' ise dizinin kendisini kopyalar.
    Boş kaynak bilinçli olarak '/' yapılmaz; aksi halde kazara tüm sistem
    yedeklenir.
    """
    if not raw:
        return ""
    normalized = strip_trailing_slashes(raw)
    return "/" if normalized == "/" else normalized + "/"


def human_bytes(count: int) -> str:
    """Byte değerini okunabilir birime çevirir."""
    value = float(count)
    for unit in _BYTE_UNITS:
        if value < 1024 or unit == _BYTE_UNITS[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_BYTE_UNITS[-1]}"
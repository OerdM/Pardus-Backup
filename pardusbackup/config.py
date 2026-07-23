
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import List, Optional

_BYTE_UNITS = ("B", "KB", "MB", "GB", "TB")


@dataclass
class SnapshotConfig:
    """Tek bir snapshot işlemini tanımlar. Doğrulama içermez."""

    source_path: str = ""
    target_path: str = ""
    sources: List[str] = field(default_factory=list)
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

    def resolved_sources(self) -> List[str]:
        """Yedeklenecek yolların nihai listesi.

        `sources` doluysa o kullanılır; boşsa tek kaynaklı eski biçime
        (`source_path`) düşülür. Böylece mevcut çağrılar bozulmaz.
        """
        if self.sources:
            return [s for s in self.sources if s]
        return [self.source_path] if self.source_path else []


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


def source_label(path: str) -> str:
    """Bir kaynağın snapshot içinde alacağı dizin/dosya adı."""
    stripped = strip_trailing_slashes(path)
    return "root" if stripped == "/" else os.path.basename(stripped)
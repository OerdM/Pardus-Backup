"""
CLI ve GUI aynı planlayıcıyı kullanır; böylece "hangi exclude'lar eklenir",
"link-dest nasıl bulunur" gibi kararlar tek yerde tanımlıdır.
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence

from .backend import make_timestamp
from .config import SnapshotConfig
from .listing import find_latest_snapshot

DEFAULT_SYSTEM_EXCLUDES = [
    "/proc/*",
    "/sys/*",
    "/dev/*",
    "/run/*",
    "/tmp/*",
    "/mnt/*",
    "/media/*",
    "/lost+found",
    "/var/cache/*",
    "/swapfile",
    "/swap.img",
]


def self_backup_excludes(sources: Sequence[str], dest: str) -> List[str]:
    """Hedef, kaynaklardan birinin içindeyse gereken dışlama desenlerini üretir."""
    patterns = []
    for source in sources:
        pattern = self_backup_exclude(source, dest)
        if pattern:
            patterns.append(pattern)
    return patterns


def self_backup_exclude(source: str, dest: str) -> Optional[str]:
    """Hedef, kaynağın içindeyse onu dışlayacak deseni döndürür.

    Yedek konumu yedeklenen ağacın içindeyse rsync kendi çıktısını kopyalamaya
    çalışır ve yedek her turda büyür. Bu desen o döngüyü keser.
    """
    source_abs = os.path.abspath(source).rstrip("/")
    dest_abs = os.path.abspath(dest)
    if not dest_abs.startswith(source_abs + "/"):
        return None
    relative = dest_abs[len(source_abs) :].lstrip("/")
    return f"{relative}/*"


def build_excludes(
    source,
    dest: str,
    system_excludes: bool = False,
    extra: Optional[Sequence[str]] = None,
) -> List[str]:
    """Bir snapshot için nihai exclude listesini kurar.

    `source` tek bir yol ya da yol listesi olabilir.
    """
    sources = [source] if isinstance(source, str) else list(source)
    patterns: List[str] = list(DEFAULT_SYSTEM_EXCLUDES) if system_excludes else []
    if extra:
        patterns += [p for p in extra if p]
    patterns += self_backup_excludes(sources, dest)
    return patterns


def unique_target(dest: str, stamp: str) -> str:
    """Çakışmayan bir snapshot hedefi üretir.

    Zaman damgası saniye çözünürlüklüdür; aynı saniyede iki yedek istenirse
    hedef zaten var olur ve rsync başlamadan reddedilir. Bu durumda sonuna
    ayırt edici bir sayı eklenir.
    """
    candidate = os.path.join(dest, stamp)
    if not os.path.exists(candidate) and not os.path.exists(candidate + ".json"):
        return candidate
    index = 2
    while True:
        candidate = os.path.join(dest, f"{stamp}_{index}")
        if not os.path.exists(candidate) and not os.path.exists(candidate + ".json"):
            return candidate
        index += 1


def plan_snapshot(
    source,
    dest: str,
    system_excludes: bool = False,
    extra_excludes: Optional[Sequence[str]] = None,
    one_file_system: bool = False,
    when: Optional[float] = None,
) -> SnapshotConfig:
    """Sıradaki snapshot için hazır bir yapılandırma üretir.

    En son tamamlanmış snapshot varsa otomatik olarak link-dest seçilir; bu
    sayede kullanıcı "tam mı artımlı mı" kararını vermek zorunda kalmaz.
    """
    sources = [source] if isinstance(source, str) else [s for s in source if s]
    latest = find_latest_snapshot(dest) if os.path.isdir(dest) else None
    return SnapshotConfig(
        source_path=sources[0] if sources else "",
        sources=sources,
        target_path=unique_target(dest, make_timestamp(when)),
        link_dest_path=latest.snapshot_path if latest else None,
        exclude_patterns=build_excludes(sources, dest, system_excludes, extra_excludes),
        one_file_system=one_file_system,
    )
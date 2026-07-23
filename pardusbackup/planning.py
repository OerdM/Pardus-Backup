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
    source: str,
    dest: str,
    system_excludes: bool = False,
    extra: Optional[Sequence[str]] = None,
) -> List[str]:
    """Bir snapshot için nihai exclude listesini kurar."""
    patterns: List[str] = list(DEFAULT_SYSTEM_EXCLUDES) if system_excludes else []
    if extra:
        patterns += [p for p in extra if p]
    guard = self_backup_exclude(source, dest)
    if guard:
        patterns.append(guard)
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
    source: str,
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
    latest = find_latest_snapshot(dest) if os.path.isdir(dest) else None
    return SnapshotConfig(
        source_path=source,
        target_path=unique_target(dest, make_timestamp(when)),
        link_dest_path=latest.snapshot_path if latest else None,
        exclude_patterns=build_excludes(source, dest, system_excludes, extra_excludes),
        one_file_system=one_file_system,
    )
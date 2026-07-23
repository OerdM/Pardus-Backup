"""
Çekirdek, arayüzden tamamen bağımsızdır: aynı API'yi hem CLI (`__main__.py`)
hem de ileride yazılacak GUI kullanır.
"""

from .backend import (
    CheckResult,
    CheckStatus,
    Progress,
    RsyncRun,
    SizeEstimate,
    SnapshotResult,
    build_rsync_args,
    check_snapshot,
    estimate_size,
    make_timestamp,
    preflight,
    parse_progress_line,
    run_rsync,
    run_rsync_streaming,
    take_snapshot,
    to_command_string,
)
from .config import SnapshotConfig, human_bytes
from .planning import (
    DEFAULT_SYSTEM_EXCLUDES,
    build_excludes,
    plan_snapshot,
    self_backup_exclude,
    unique_target,
)
from .listing import (
    DirStats,
    ListResult,
    SnapshotInfo,
    DeleteResult,
    compute_dir_stats,
    delete_snapshot,
    find_latest_snapshot,
    list_snapshots,
)

__version__ = "0.1.0"


__all__ = [
    "SnapshotConfig",
    "human_bytes",
    "build_rsync_args",
    "to_command_string",
    "CheckStatus",
    "CheckResult",
    "check_snapshot",
    "RsyncRun",
    "run_rsync",
    "Progress",
    "parse_progress_line",
    "run_rsync_streaming",
    "SizeEstimate",
    "estimate_size",
    "preflight",
    "SnapshotResult",
    "take_snapshot",
    "make_timestamp",
    "DirStats",
    "compute_dir_stats",
    "DeleteResult",
    "delete_snapshot",
    "DEFAULT_SYSTEM_EXCLUDES",
    "build_excludes",
    "plan_snapshot",
    "self_backup_exclude",
    "unique_target",
    "SnapshotInfo",
    "ListResult",
    "list_snapshots",
    "find_latest_snapshot",
    "__version__",
]
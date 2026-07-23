
from __future__ import annotations
import dataclasses
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional
from .config import (
    SnapshotConfig,
    human_bytes,
    normalize_source,
    source_label,
    strip_trailing_slashes,
)

RSYNC_OK_EXIT_CODES = (0, 24)
METADATA_VERSION = 1
SPACE_SAFETY_MARGIN = 0.10
_STATS_TRANSFERRED = "Total transferred file size"
_STATS_TOTAL = "Total file size"
_PROGRESS_PATTERN = re.compile(r"([\d,.\s]+?)\s+(\d+)%\s+(\S+)\s+(\d+:\d{2}:\d{2})")


def _digits_to_int(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def _c_locale_env() -> dict:
    return {**os.environ, "LC_ALL": "C"}


def _short_flags(cfg: SnapshotConfig) -> str:
    flags = "a"
    if cfg.preserve_hardlinks:
        flags += "H"
    if cfg.preserve_acls:
        flags += "A"
    if cfg.preserve_xattrs:
        flags += "X"
    if cfg.one_file_system:
        flags += "x"
    return "-" + flags


def _long_flags(cfg: SnapshotConfig) -> List[str]:
    toggles = (
        (cfg.use_delete, "--delete"),
        (cfg.numeric_ids, "--numeric-ids"),
        (cfg.dry_run, "--dry-run"),
        (cfg.stats, "--stats"),
        (cfg.show_progress, "--info=progress2"),
    )
    return [flag for enabled, flag in toggles if enabled]


def _source_args(cfg: SnapshotConfig) -> List[str]:
    """Kaynakları rsync argümanına çevirir.

    Tek kaynakta içerik-kopyala biçimi kullanılır: snapshot dizini kaynağın
    içeriğini birebir yansıtır. Birden çok kaynakta ise her yol sondaki '/'
    olmadan verilir; böylece her biri snapshot içinde kendi adıyla ayrı bir
    girdi olur (Belgeler/, Resimler/, notlar.txt).
    """
    sources = cfg.resolved_sources()
    if not sources:
        return [""]
    if len(sources) == 1:
        return [normalize_source(sources[0])]
    return [strip_trailing_slashes(path) for path in sources]


def build_rsync_args(cfg: SnapshotConfig) -> List[str]:
    """Yapılandırmadan rsync argüman listesini üretir.
    Saf fonksiyondur; I/O yapmaz. Program adını içermez. Her argüman ayrı bir
    eleman olduğu için shell quoting gerekmez.
    """
    args = [_short_flags(cfg)]
    args += _long_flags(cfg)
    args += [f"--exclude={pattern}" for pattern in cfg.exclude_patterns if pattern]
    if cfg.link_dest_path:
        args.append(f"--link-dest={strip_trailing_slashes(cfg.link_dest_path)}")
    args += _source_args(cfg)
    args.append(strip_trailing_slashes(cfg.target_path))
    return args


def to_command_string(args: List[str]) -> str:
    """Argümanları log için okunur satıra çevirir. Shell'e verilmemelidir."""
    parts = ["rsync"]
    parts += [f'"{arg}"' if " " in arg else arg for arg in args]
    return " ".join(parts)


class CheckStatus(Enum):
    OK = "Ok"
    SOURCE_NOT_ACCESSIBLE = "SourceNotAccessible"
    SOURCE_NAME_COLLISION = "SourceNameCollision"
    TARGET_NOT_WRITABLE = "TargetNotWritable"
    LINK_DEST_MISSING = "LinkDestMissing"
    NOT_SAME_FILESYSTEM = "NotSameFilesystem"
    INSUFFICIENT_DISK_SPACE = "InsufficientDiskSpace"


@dataclass
class CheckResult:
    status: CheckStatus = CheckStatus.OK
    message: str = ""
    available_bytes: int = 0
    estimated_needed_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.OK


def _target_parent_of(cfg: SnapshotConfig) -> str:
    return os.path.dirname(strip_trailing_slashes(cfg.target_path)) or "."


def _validate_source(cfg: SnapshotConfig) -> Optional[CheckResult]:
    sources = cfg.resolved_sources()
    if not sources:
        return CheckResult(CheckStatus.SOURCE_NOT_ACCESSIBLE, "Kaynak seçilmedi.")

    for path in sources:
        if not os.access(path, os.R_OK):
            return CheckResult(
                CheckStatus.SOURCE_NOT_ACCESSIBLE,
                f"Kaynak okunamıyor: {path}",
            )

    if len(sources) > 1:
        seen: dict = {}
        for path in sources:
            label = source_label(path)
            if label in seen:
                return CheckResult(
                    CheckStatus.SOURCE_NAME_COLLISION,
                    f"İki kaynak aynı ada sahip ({label}): {seen[label]} ve {path}. "
                    "Aynı adlı iki yol tek yedekte birleşeceği için biri "
                    "çıkarılmalı.",
                )
            seen[label] = path
    return None


def _validate_target_parent(parent: str) -> Optional[CheckResult]:
    if not os.path.isdir(parent):
        return CheckResult(
            CheckStatus.TARGET_NOT_WRITABLE, f"Hedef dizin bulunamadı: {parent}"
        )
    if not os.access(parent, os.W_OK):
        return CheckResult(
            CheckStatus.TARGET_NOT_WRITABLE, f"Hedefe yazılamıyor: {parent}"
        )
    return None


def _validate_link_dest(link_dest: str, target_parent: str) -> Optional[CheckResult]:
    """link-dest'in varlığını ve hedefle aynı dosya sisteminde olduğunu doğrular.
    Farklı dosya sistemlerinde rsync hata vermeden tam kopyaya düşer; artımlı
    mekanizma sessizce çöker. Bu yüzden st_dev karşılaştırması zorunludur.
    """
    if not os.path.isdir(link_dest):
        return CheckResult(
            CheckStatus.LINK_DEST_MISSING,
            f"Referans snapshot bulunamadı veya dizin değil: {link_dest}",
        )
    if os.stat(link_dest).st_dev != os.stat(target_parent).st_dev:
        return CheckResult(
            CheckStatus.NOT_SAME_FILESYSTEM,
            "Referans snapshot ile hedef farklı dosya sistemlerinde "
            "(ikisi de aynı bölümde olmalı). Aksi halde hardlink kurulamaz "
            "ve artımlı yedek tam kopyaya düşer.",
        )
    return None


def _available_bytes(path: str) -> int:
    vfs = os.statvfs(path)
    return vfs.f_bavail * vfs.f_frsize


def check_snapshot(cfg: SnapshotConfig, estimated_needed: int = 0) -> CheckResult:
    """Snapshot öncesi ön koşulları doğrular; hiçbir şeye yazmaz.
    estimated_needed sıfırsa alan yeterliliği kararı atlanır, available_bytes
    yine doldurulur.
    """
    target_parent = _target_parent_of(cfg)
    for failure in (
        _validate_source(cfg),
        _validate_target_parent(target_parent),
    ):
        if failure is not None:
            return failure
    if cfg.link_dest_path:
        failure = _validate_link_dest(cfg.link_dest_path, target_parent)
        if failure is not None:
            return failure
    result = CheckResult(estimated_needed_bytes=estimated_needed)
    result.available_bytes = _available_bytes(target_parent)
    if 0 < estimated_needed > result.available_bytes:
        result.status = CheckStatus.INSUFFICIENT_DISK_SPACE
        result.message = (
            f"Yetersiz disk alanı. Gerekli ~{human_bytes(estimated_needed)}, "
            f"kullanılabilir {human_bytes(result.available_bytes)}."
        )
        return result
    result.message = (
        f"Tüm kontroller geçti. Kullanılabilir alan: "
        f"{human_bytes(result.available_bytes)}."
    )
    return result


@dataclass
class RsyncRun:
    launched: bool = False
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    error_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.launched and self.exit_code in RSYNC_OK_EXIT_CODES


def run_rsync(args: List[str], timeout: Optional[float] = None) -> RsyncRun:
    """rsync'i shell kullanmadan çalıştırıp çıktısını toplar."""
    try:
        proc = subprocess.run(
            ["rsync", *args],
            capture_output=True,
            text=True,
            env=_c_locale_env(),
            timeout=timeout,
        )
    except FileNotFoundError:
        return RsyncRun(error_message="rsync bulunamadı (PATH'te yok).")
    except subprocess.TimeoutExpired:
        return RsyncRun(error_message="rsync zaman aşımına uğradı.")
    except OSError as exc:
        return RsyncRun(error_message=f"rsync çalıştırılamadı: {exc}")
    return RsyncRun(True, proc.returncode, proc.stdout, proc.stderr)


@dataclass
class Progress:
    transferred_bytes: int = 0
    percent: float = 0.0
    speed: str = ""
    eta: str = ""


ProgressCallback = Callable[[Progress], None]


def parse_progress_line(line: str) -> Optional[Progress]:
    """Tek bir --info=progress2 satırını ayrıştırır."""
    match = _PROGRESS_PATTERN.search(line)
    if not match:
        return None
    return Progress(
        transferred_bytes=_digits_to_int(match.group(1)),
        percent=float(match.group(2)),
        speed=match.group(3),
        eta=match.group(4),
    )


class _OutputReader:
    """rsync stdout'unu satırlara böler ve ilerlemeyi bildirir.
    progress2 çıktısı satırları '\\r' ile günceller, '\\n' ile değil; bu yüzden
    karakter karakter okunup her iki ayraç da satır sonu sayılır.
    """

    LINE_BREAKS = ("\r", "\n")

    def __init__(self, on_progress: Optional[ProgressCallback]) -> None:
        self._on_progress = on_progress
        self._buffer = ""
        self.lines: List[str] = []

    def feed(self, char: str) -> None:
        if char in self.LINE_BREAKS:
            self._flush()
        else:
            self._buffer += char

    def finish(self) -> str:
        self._flush()
        return "".join(self.lines)

    def _flush(self) -> None:
        line = self._buffer.strip()
        self._buffer = ""
        if not line:
            return
        self.lines.append(line + "\n")
        if self._on_progress is None:
            return
        progress = parse_progress_line(line)
        if progress is not None:
            self._on_progress(progress)


def run_rsync_streaming(
    args: List[str],
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> RsyncRun:
    """rsync'i çalıştırıp çıktısını canlı okur ve ilerlemeyi bildirir.
    GUI'nin donmaması için arka plan iş parçacığında çağrılmalıdır.
    cancel_event set edilirse rsync SIGTERM ile sonlandırılır.

    --info=progress2 satırları satır sonu yerine '\\r' ile güncellenir; bu
    yüzden çıktı karakter karakter okunur. Yalnızca '\\n' beklenirse hiçbir
    ilerleme görünmez. stderr ayrı iş parçacığında boşaltılır, aksi halde bir
    borunun dolması kilitlenmeye yol açar.
    """
    try:
        proc = subprocess.Popen(
            ["rsync", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_c_locale_env(),
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return RsyncRun(error_message="rsync bulunamadı (PATH'te yok).")
    except OSError as exc:
        return RsyncRun(error_message=f"rsync çalıştırılamadı: {exc}")
    stderr_chunks: List[str] = []
    stderr_thread = threading.Thread(
        target=lambda: stderr_chunks.extend(proc.stderr or []), daemon=True
    )
    stderr_thread.start()
    reader = _OutputReader(on_progress)
    cancelled = False
    assert proc.stdout is not None
    while True:
        if cancel_event is not None and cancel_event.is_set() and not cancelled:
            cancelled = True
            proc.terminate()
        char = proc.stdout.read(1)
        if not char:
            break
        reader.feed(char)
    stdout_text = reader.finish()
    proc.wait()
    stderr_thread.join(timeout=2)
    return RsyncRun(
        launched=True,
        exit_code=proc.returncode,
        stdout=stdout_text,
        stderr="".join(stderr_chunks),
        error_message="Kullanıcı tarafından iptal edildi." if cancelled else "",
    )


def _parse_stats_bytes(text: str, label: str) -> int:
    match = re.search(re.escape(label) + r"[^:\n]*:\s*([\d,.\s]+)", text)
    return _digits_to_int(match.group(1)) if match else 0


@dataclass
class SizeEstimate:
    ok: bool = False
    transferred_bytes: int = 0
    total_bytes: int = 0
    message: str = ""


def estimate_size(cfg: SnapshotConfig) -> SizeEstimate:
    """--dry-run --stats ile bu snapshot'ın yazacağı tahmini byte'ı çıkarır.
    link-dest verildiğinde transferred_bytes yalnızca gerçekten kopyalanacak
    dosyaları yansıtır; artımlı yedek için doğru tahmin budur.
    """
    probe = dataclasses.replace(cfg, dry_run=True, stats=True, show_progress=False)
    run = run_rsync(build_rsync_args(probe))
    if not run.launched:
        return SizeEstimate(message=f"rsync çalıştırılamadı: {run.error_message}")
    if run.exit_code not in RSYNC_OK_EXIT_CODES:
        return SizeEstimate(
            message=(
                f"rsync --dry-run hata verdi (exit {run.exit_code}): "
                f"{run.stderr.strip()}"
            )
        )
    if _STATS_TRANSFERRED not in run.stdout:
        return SizeEstimate(message="rsync --stats çıktısı beklenen biçimde değil.")
    transferred = _parse_stats_bytes(run.stdout, _STATS_TRANSFERRED)
    total = _parse_stats_bytes(run.stdout, _STATS_TOTAL)
    return SizeEstimate(
        ok=True,
        transferred_bytes=transferred,
        total_bytes=total,
        message=(
            f"Aktarılacak ~{human_bytes(transferred)} "
            f"(toplam ağaç {human_bytes(total)})."
        ),
    )


def preflight(cfg: SnapshotConfig) -> CheckResult:
    """Ucuz kontroller, boyut tahmini ve disk alanı doğrulamasını birleştirir.
    Tahmin üretilemezse kontroller Ok kalır ve mesaja uyarı eklenir.
    """
    result = check_snapshot(cfg)
    if not result.ok:
        return result
    estimate = estimate_size(cfg)
    if not estimate.ok:
        result.message += (
            f" (Uyarı: boyut tahmin edilemedi, disk alanı doğrulanmadı — "
            f"{estimate.message})"
        )
        return result
    needed = int(estimate.transferred_bytes * (1 + SPACE_SAFETY_MARGIN))
    final = check_snapshot(cfg, needed)
    final.message += " " + estimate.message
    return final


@dataclass
class SnapshotResult:
    success: bool = False
    snapshot_path: str = ""
    info_json_path: str = ""
    exit_code: int = -1
    transferred_bytes: int = 0
    error_output: str = ""


def make_timestamp(when: Optional[float] = None) -> str:
    """Zaman damgalı snapshot dizin adı üretir."""
    return time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(when))


def _metadata_for(
    cfg: SnapshotConfig, rsync_exit: int, transferred: int, total: int
) -> dict:

    now = int(time.time())
    sources = cfg.resolved_sources()
    return {
        "version": METADATA_VERSION,
        "createdUnix": now,
        "createdLocal": make_timestamp(now),
        "source": sources[0] if sources else "",
        "sources": sources,
        "target": strip_trailing_slashes(cfg.target_path),
        "linkDest": cfg.link_dest_path,
        "incremental": bool(cfg.link_dest_path),
        "excludes": list(cfg.exclude_patterns),
        "transferredBytes": transferred,
        "totalBytes": total,
        "rsyncExitCode": rsync_exit,
        "completed": True,
    }


def _write_metadata(path: str, metadata: dict) -> None:
    """Metadata'yı snapshot dizininin yanına '<dir>.json' olarak yazar.
    Ağacın içine yazılmaz; aksi halde restore kaynağı kirletir ve bu snapshot
    bir sonrakine link-dest olduğunda fazladan dosya görünür.
    """
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _discard(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _execute_snapshot(
    cfg: SnapshotConfig,
    on_progress: Optional[ProgressCallback],
    cancel_event: Optional[threading.Event],
) -> RsyncRun:

    streaming = on_progress is not None or cancel_event is not None
    run_cfg = dataclasses.replace(
        cfg, dry_run=False, stats=True, show_progress=streaming
    )
    args = build_rsync_args(run_cfg)
    if streaming:
        return run_rsync_streaming(args, on_progress, cancel_event)
    return run_rsync(args)


def take_snapshot(
    cfg: SnapshotConfig,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[threading.Event] = None,
) -> SnapshotResult:
    """Snapshot'ı alır ve başarılı olursa metadata yazar.
    Metadata yalnızca rsync bittikten sonra yazılır; böylece yarım kalan
    snapshot'lar sidecar'sız kalır ve listelemede tamamlanmamış sayılır.
    Pahalı boyut/alan kontrolü yapmaz; onu preflight() üstlenir.
    """
    result = SnapshotResult(snapshot_path=strip_trailing_slashes(cfg.target_path))
    check = check_snapshot(cfg)
    if not check.ok:
        result.error_output = (
            f"Ön kontrol başarısız ({check.status.value}): {check.message}"
        )
        return result
    try:
        os.mkdir(result.snapshot_path, 0o755)
    except FileExistsError:
        result.error_output = f"Snapshot dizini zaten var: {result.snapshot_path}"
        return result
    except OSError as exc:
        result.error_output = f"Dizin oluşturulamadı: {result.snapshot_path} ({exc})"
        return result
    run = _execute_snapshot(cfg, on_progress, cancel_event)
    if not run.launched:
        result.error_output = f"rsync başlatılamadı: {run.error_message}"
        _discard(result.snapshot_path)
        return result
    result.exit_code = run.exit_code
    if cancel_event is not None and cancel_event.is_set():
        result.error_output = "Yedekleme iptal edildi."
        _discard(result.snapshot_path)
        return result
    if run.exit_code not in RSYNC_OK_EXIT_CODES:
        result.error_output = (
            f"rsync hata verdi (exit {run.exit_code}): {run.stderr.strip()}"
        )
        _discard(result.snapshot_path)
        return result
    result.transferred_bytes = _parse_stats_bytes(run.stdout, _STATS_TRANSFERRED)
    total_bytes = _parse_stats_bytes(run.stdout, _STATS_TOTAL)
    info_path = result.snapshot_path + ".json"
    try:
        _write_metadata(
            info_path,
            _metadata_for(cfg, run.exit_code, result.transferred_bytes, total_bytes),
        )
    except OSError as exc:
        result.error_output = f"Snapshot alındı ama metadata yazılamadı: {exc}"
        return result
    result.info_json_path = info_path
    result.success = True
    return result
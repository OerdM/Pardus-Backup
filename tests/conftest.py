
from __future__ import annotations
import os
import shutil
import subprocess
import pytest

RSYNC_AVAILABLE = shutil.which("rsync") is not None


needs_rsync = pytest.mark.skipif(not RSYNC_AVAILABLE, reason="rsync kurulu değil")


def age_files_to_past(directory: str) -> None:
    """Dizindeki tüm dosyalara sabit, geçmişte bir mtime verir.
    NEDEN: rsync bir dosyayı "değişmedi" saymak için boyut + mtime
    karşılaştırır. Testte dosya oluşturulup hemen snapshot alındığında mtime
    "şimdi"ye çok yakın olur; bu belirsizlik bazı dosya sistemlerinde rsync'in
    --link-dest ile hardlink yerine yeniden kopyalamasına yol açıp testi
    kırılgan (flaky) yapar. Sabit geçmiş mtime bunu tümüyle ortadan kaldırır.
    """
    subprocess.run(
        ["find", directory, "-exec", "touch", "-d", "2020-01-01 00:00:00", "{}", "+"],
        check=False,
        capture_output=True,
    )
    subprocess.run(["sync"], check=False, capture_output=True)


def itemize_diag(source: str, link_dest: str, tmp: str) -> str:
    """Hardlink kurulmadıysa NEDENİNİ gösteren rsync itemize çıktısı.
    Harfler farkı gösterir: t=mtime, p=izin, o/g=sahip/grup, a=ACL, x=xattr.
    Satır varsa o dosya kopyalanıyor demektir.
    """
    probe = os.path.join(tmp, "_itemize_probe")
    shutil.rmtree(probe, ignore_errors=True)
    os.makedirs(probe, exist_ok=True)
    proc = subprocess.run(
        [
            "rsync",
            "-aHAX",
            "--numeric-ids",
            f"--link-dest={link_dest}",
            "-ni",
            source.rstrip("/") + "/",
            probe + "/",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    shutil.rmtree(probe, ignore_errors=True)
    return proc.stdout + proc.stderr


@pytest.fixture
def workspace(tmp_path):
    """source/ + snapshots/ düzeninde geçici bir çalışma alanı."""
    source = tmp_path / "source"
    snapshots = tmp_path / "snapshots"
    source.mkdir()
    snapshots.mkdir()
    (source / "a.txt").write_text("veri\n", encoding="utf-8")

    class Workspace:
        def __init__(self):
            self.root = str(tmp_path)
            self.source = str(source)
            self.snapshots = str(snapshots)

        def target(self, name: str = "2026-01-01_10-00-00") -> str:
            return os.path.join(self.snapshots, name)

        def age(self) -> None:
            age_files_to_past(self.source)

    return Workspace()
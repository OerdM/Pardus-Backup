"""
Bu katman çekirdek mantık içermez; yalnızca backend/listing/planning API'sini
çağırır. rsync bilgisi, yol normalizasyonu ve metadata biçimi çekirdekte kalır.

GTK iş parçacığı güvenli değildir: rsync ayrı bir thread'de koşar ve arayüze
her dokunuş GLib.idle_add ile ana döngüye aktarılır.
"""

from __future__ import annotations

import os
import threading
from typing import List, Optional

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from .backend import Progress, SnapshotResult, preflight, take_snapshot
from .config import human_bytes
from .listing import SnapshotInfo, delete_snapshot, list_snapshots
from .planning import plan_snapshot

APP_ID = "tr.org.pardus.backup"
APP_TITLE = "Pardus Yedekleme"


class BackupWorker:
    """Yedeklemeyi arka planda çalıştırıp arayüze güvenle haber verir."""

    def __init__(self, window: "MainWindow", cfg) -> None:
        self._window = window
        self._cfg = cfg
        self._cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        result = take_snapshot(
            self._cfg, on_progress=self._report, cancel_event=self._cancel
        )
        GLib.idle_add(self._window.on_backup_finished, result)

    def _report(self, progress: Progress) -> None:
        GLib.idle_add(self._window.on_backup_progress, progress)


class SnapshotDetails(Gtk.Box):
    """Seçili snapshot'ın ayrıntılarını gösteren yan panel."""

    CAPTIONS = (
        "Tarih",
        "Tür",
        "Kaynak",
        "Konum",
        "Dosya sayısı",
        "İçerik boyutu",
        "Diske eklenen",
        "Referans",
    )

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_size_request(290, -1)

        title = Gtk.Label(xalign=0.0)
        title.set_markup("<b>Yedek ayrıntıları</b>")
        title.set_margin_top(12)
        title.set_margin_bottom(8)
        title.set_margin_start(12)
        self.pack_start(title, False, False, 0)

        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        grid.set_margin_start(12)
        grid.set_margin_end(12)
        grid.set_margin_bottom(12)

        self._values: List[Gtk.Label] = []
        for row, caption in enumerate(self.CAPTIONS):
            key = Gtk.Label(xalign=0.0)
            key.set_markup(f'<span alpha="70%">{caption}</span>')
            key.set_valign(Gtk.Align.START)
            grid.attach(key, 0, row, 1, 1)

            value = Gtk.Label(xalign=0.0, label="—")
            value.set_line_wrap(True)
            value.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value.set_selectable(True)
            value.set_max_width_chars(22)
            value.set_valign(Gtk.Align.START)
            grid.attach(value, 1, row, 1, 1)
            self._values.append(value)

        self._excludes = Gtk.Label(xalign=0.0)
        self._excludes.set_line_wrap(True)
        self._excludes.set_max_width_chars(32)
        grid.attach(self._excludes, 0, len(self.CAPTIONS), 2, 1)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(grid)
        self.pack_start(scroller, True, True, 0)

    def clear(self) -> None:
        for label in self._values:
            label.set_text("—")
        self._excludes.set_text("")

    def show_snapshot(self, snap: SnapshotInfo) -> None:
        self._excludes.set_text("")

        if not snap.metadata_valid:
            self.clear()
            self._values[0].set_text(os.path.basename(snap.snapshot_path))
            self._values[1].set_text("Bozuk kayıt")
            self._values[3].set_text(snap.snapshot_path)
            return

        texts = (
            snap.created_local or "—",
            "Artımlı" if snap.incremental else "Tam",
            snap.source or "—",
            snap.snapshot_path,
            str(snap.file_count),
            human_bytes(snap.apparent_bytes),
            human_bytes(snap.transferred_bytes),
            os.path.basename(snap.link_dest) if snap.link_dest else "yok",
        )
        for label, text in zip(self._values, texts):
            label.set_text(text)

        if snap.excludes:
            listed = ", ".join(snap.excludes[:6])
            if len(snap.excludes) > 6:
                listed += f" (+{len(snap.excludes) - 6})"
            escaped = GLib.markup_escape_text(listed)
            self._excludes.set_markup(
                f'<span alpha="70%">Hariç tutulan</span>\n<small>{escaped}</small>'
            )


class MainWindow(Gtk.ApplicationWindow):
    COL_DATE, COL_KIND, COL_FILES, COL_SIZE, COL_ADDED = range(5)

    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title=APP_TITLE)
        self.set_default_size(940, 600)

        self._worker: Optional[BackupWorker] = None
        self._snapshots: List[SnapshotInfo] = []

        self._build_header()
        self._build_body()
        self.refresh_snapshots()

    def _build_header(self) -> None:
        header = Gtk.HeaderBar(show_close_button=True)
        header.set_title(APP_TITLE)
        self.set_titlebar(header)

        self.backup_button = Gtk.Button(label="Yedek Al")
        self.backup_button.get_style_context().add_class("suggested-action")
        self.backup_button.connect("clicked", self.on_backup_clicked)
        header.pack_start(self.backup_button)

        self.cancel_button = Gtk.Button(label="İptal")
        self.cancel_button.get_style_context().add_class("destructive-action")
        self.cancel_button.connect("clicked", self.on_cancel_clicked)
        self.cancel_button.set_no_show_all(True)
        header.pack_start(self.cancel_button)

        self.delete_button = Gtk.Button.new_from_icon_name(
            "user-trash-symbolic", Gtk.IconSize.BUTTON
        )
        self.delete_button.set_tooltip_text("Seçili yedeği sil")
        self.delete_button.set_sensitive(False)
        self.delete_button.connect("clicked", self.on_delete_clicked)
        header.pack_end(self.delete_button)

        refresh = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        refresh.set_tooltip_text("Listeyi yenile")
        refresh.connect("clicked", lambda _b: self.refresh_snapshots())
        header.pack_end(refresh)

    def _build_body(self) -> None:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root)

        root.pack_start(self._build_paths(), False, False, 0)
        root.pack_start(Gtk.Separator(), False, False, 0)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(630)
        paned.pack1(self._build_list(), True, False)

        self.details = SnapshotDetails()
        paned.pack2(self.details, False, False)
        root.pack_start(paned, True, True, 0)

        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.set_no_show_all(True)
        root.pack_start(self.progress, False, False, 0)

        root.pack_start(Gtk.Separator(), False, False, 0)
        self.status = Gtk.Label(xalign=0.0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_margin_start(12)
        self.status.set_margin_end(12)
        self.status.set_margin_top(6)
        self.status.set_margin_bottom(6)
        root.pack_start(self.status, False, False, 0)

    def _build_paths(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(12)
        grid.set_margin_bottom(12)
        grid.set_margin_start(12)
        grid.set_margin_end(12)

        grid.attach(Gtk.Label(label="Yedeklenecek:", xalign=1.0), 0, 0, 1, 1)
        self.source_chooser = Gtk.FileChooserButton(
            title="Yedeklenecek dizini seçin",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        self.source_chooser.set_filename(os.path.expanduser("~"))
        self.source_chooser.set_hexpand(True)
        grid.attach(self.source_chooser, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Yedek konumu:", xalign=1.0), 0, 1, 1, 1)
        self.dest_chooser = Gtk.FileChooserButton(
            title="Yedeklerin saklanacağı dizini seçin",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        self.dest_chooser.set_filename(os.path.expanduser("~"))
        self.dest_chooser.set_hexpand(True)
        self.dest_chooser.connect("file-set", lambda _c: self.refresh_snapshots())
        grid.attach(self.dest_chooser, 1, 1, 1, 1)

        self.system_check = Gtk.CheckButton(
            label="Sistem dizinlerini hariç tut (/proc, /sys, /dev …)"
        )
        self.system_check.set_tooltip_text("Tüm sistemi (/) yedeklerken işaretleyin.")
        grid.attach(self.system_check, 1, 2, 1, 1)
        return grid

    def _build_list(self) -> Gtk.Widget:
        self.store = Gtk.ListStore(str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)

        columns = (
            ("Tarih", self.COL_DATE, True),
            ("Tür", self.COL_KIND, False),
            ("Dosya", self.COL_FILES, False),
            ("Boyut", self.COL_SIZE, False),
            ("Diske eklenen", self.COL_ADDED, False),
        )
        for title, index, expand in columns:
            renderer = Gtk.CellRendererText()
            if index in (self.COL_FILES, self.COL_SIZE, self.COL_ADDED):
                renderer.set_property("xalign", 1.0)
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_expand(expand)
            self.tree.append_column(column)

        self.tree.get_selection().connect("changed", self.on_selection_changed)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.tree)

        self.empty_state = Gtk.Label()
        self.empty_state.set_markup(
            '<span size="large">Henüz yedek yok</span>\n\n'
            '<span alpha="65%">Yedeklenecek dizini ve yedek konumunu seçip\n'
            "“Yedek Al” düğmesine basın.</span>"
        )
        self.empty_state.set_justify(Gtk.Justification.CENTER)

        self.list_stack = Gtk.Stack()
        self.list_stack.add_named(scroller, "list")
        self.list_stack.add_named(self.empty_state, "empty")
        return self.list_stack

    def refresh_snapshots(self) -> None:
        dest = self.dest_chooser.get_filename()
        self.store.clear()
        self._snapshots = []
        self.details.clear()
        self.delete_button.set_sensitive(False)

        if not dest:
            self.list_stack.set_visible_child_name("empty")
            self.set_status("Yedek konumu seçilmedi.")
            return

        listing = list_snapshots(dest)
        self._snapshots = listing.snapshots

        for snap in listing.snapshots:
            valid = snap.metadata_valid
            self.store.append(
                [
                    (
                        snap.created_local or "—"
                        if valid
                        else os.path.basename(snap.snapshot_path)
                    ),
                    (
                        ("Artımlı" if snap.incremental else "Tam")
                        if valid
                        else "Bozuk kayıt"
                    ),
                    str(snap.file_count),
                    human_bytes(snap.apparent_bytes),
                    human_bytes(snap.transferred_bytes) if valid else "—",
                ]
            )

        if listing.snapshots:
            self.list_stack.set_visible_child_name("list")
            total = sum(s.transferred_bytes for s in listing.snapshots)
            self.set_status(
                f"{len(listing.snapshots)} yedek · toplam eklenen {human_bytes(total)}"
            )
        else:
            self.list_stack.set_visible_child_name("empty")
            self.set_status(listing.message)

    def _selected_snapshot(self) -> Optional[SnapshotInfo]:
        model, tree_iter = self.tree.get_selection().get_selected()
        if tree_iter is None:
            return None
        index = model.get_path(tree_iter).get_indices()[0]
        if 0 <= index < len(self._snapshots):
            return self._snapshots[index]
        return None

    def on_selection_changed(self, _selection: Gtk.TreeSelection) -> None:
        snap = self._selected_snapshot()
        self.delete_button.set_sensitive(snap is not None and self._worker is None)
        if snap is None:
            self.details.clear()
        else:
            self.details.show_snapshot(snap)

    def on_backup_clicked(self, _button: Gtk.Button) -> None:
        source = self.source_chooser.get_filename()
        dest = self.dest_chooser.get_filename()
        if not source or not dest:
            self.show_message(
                "Eksik seçim",
                "Lütfen hem yedeklenecek dizini hem de yedek konumunu seçin.",
                Gtk.MessageType.WARNING,
            )
            return

        cfg = plan_snapshot(source, dest, self.system_check.get_active())

        self.set_status("Ön kontrol yapılıyor…")
        check = preflight(cfg)
        if not check.ok:
            self.set_status(f"Ön kontrol başarısız: {check.status.value}")
            self.show_message(
                "Yedekleme başlatılamadı", check.message, Gtk.MessageType.ERROR
            )
            return

        self._worker = BackupWorker(self, cfg)
        self._set_busy(True)
        kind = "Artımlı" if cfg.link_dest_path else "Tam"
        self.set_status(f"{kind} yedekleme sürüyor… {check.message}")
        self._worker.start()

    def on_cancel_clicked(self, _button: Gtk.Button) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.set_sensitive(False)
            self.set_status("İptal ediliyor…")

    def on_delete_clicked(self, _button: Gtk.Button) -> None:
        snap = self._selected_snapshot()
        if snap is None:
            return

        name = snap.created_local or os.path.basename(snap.snapshot_path)
        confirm = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text=f"“{name}” yedeği silinsin mi?",
        )
        confirm.format_secondary_text(
            "Bu işlem geri alınamaz. Diğer yedekler etkilenmez: ortak dosyalar "
            "bağlantılı tutulduğu için yalnızca bu yedeğe ait alan boşalır."
        )
        confirm.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
        delete = confirm.add_button("Sil", Gtk.ResponseType.ACCEPT)
        delete.get_style_context().add_class("destructive-action")
        response = confirm.run()
        confirm.destroy()

        if response != Gtk.ResponseType.ACCEPT:
            return

        result = delete_snapshot(snap.snapshot_path)
        if result.success:
            self.refresh_snapshots()
            self.set_status(
                f"{result.message} · {human_bytes(result.freed_bytes)} boşaldı"
            )
        else:
            self.show_message("Silinemedi", result.message, Gtk.MessageType.ERROR)

    def on_backup_progress(self, progress: Progress) -> bool:
        self.progress.set_fraction(min(progress.percent / 100.0, 1.0))
        parts = [f"%{progress.percent:.0f}", human_bytes(progress.transferred_bytes)]
        if progress.speed:
            parts.append(progress.speed)
        if progress.eta:
            parts.append(f"kalan {progress.eta}")
        self.progress.set_text(" · ".join(parts))
        return GLib.SOURCE_REMOVE

    def on_backup_finished(self, result: SnapshotResult) -> bool:
        self._worker = None
        self._set_busy(False)

        if result.success:
            self.set_status(
                f"Yedekleme tamamlandı · diske eklenen "
                f"{human_bytes(result.transferred_bytes)}"
            )
        else:
            self.set_status("Yedekleme tamamlanamadı.")
            self.show_message(
                "Yedekleme hatası",
                result.error_output or "Bilinmeyen hata.",
                Gtk.MessageType.ERROR,
            )

        self.refresh_snapshots()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self.backup_button,
            self.source_chooser,
            self.dest_chooser,
            self.system_check,
        ):
            widget.set_sensitive(not busy)

        self.delete_button.set_sensitive(
            not busy and self._selected_snapshot() is not None
        )
        self.cancel_button.set_sensitive(True)
        self.cancel_button.set_visible(busy)
        self.progress.set_visible(busy)
        if busy:
            self.progress.set_fraction(0.0)
            self.progress.set_text("Başlatılıyor…")

    def set_status(self, text: str) -> None:
        self.status.set_text(text)

    def show_message(
        self, title: str, body: str, kind: Gtk.MessageType = Gtk.MessageType.INFO
    ) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=kind,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(body)
        dialog.run()
        dialog.destroy()


class PardusBackupApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)

    def do_activate(self) -> None:
        window = self.get_active_window() or MainWindow(self)
        window.show_all()
        window.present()


def main(argv: Optional[list] = None) -> int:
    return PardusBackupApp().run(argv or [])


if __name__ == "__main__":
    raise SystemExit(main())
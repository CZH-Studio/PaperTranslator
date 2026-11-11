#!/usr/bin/env python3
"""PaperTranslator GUI — PyQt6 dashboard for batch paper translation."""
import os
import sys
import shutil
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QSpinBox, QCheckBox, QLabel, QTabWidget,
    QTextEdit, QProgressBar, QFileDialog, QMessageBox, QApplication,
    QGroupBox, QStyle, QFrame, QAbstractItemView,
)
from PyQt6.QtCore import (
    Qt, QProcess, pyqtSignal, QTimer, QFileSystemWatcher,
)
from PyQt6.QtGui import QColor, QFont, QTextCursor


# ── Data model ─────────────────────────────────────────────────────────
class FileStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class FileItem:
    path: str
    name: str = ""
    file_type: str = ""
    status: FileStatus = FileStatus.PENDING
    output_dir: str = ""
    log_widget: object = None
    process: object = None
    rows: list[str] = field(default_factory=list)

    def __post_init__(self):
        self.name = os.path.basename(self.path)
        ext = os.path.splitext(self.path)[1].lower()
        self.file_type = ext.upper() if ext else "?"


# ── Main window ────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    log_signal = pyqtSignal(str, str)  # file_path, log_line

    # Style constants
    COLOR_DONE = QColor("#4CAF50")
    COLOR_FAILED = QColor("#F44336")
    COLOR_RUNNING = QColor("#2196F3")
    COLOR_PENDING = QColor("#9E9E9E")
    COLOR_BG = "#1e1e2e"
    COLOR_PANEL = "#2a2a3c"
    COLOR_TEXT = "#cdd6f4"
    COLOR_ACCENT = "#89b4fa"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PaperTranslator — Academic Paper Translation Dashboard")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)

        self._files: dict[str, FileItem] = {}  # path -> FileItem
        self._tab_indices: dict[str, int] = {}  # path -> tab index
        self._running = 0
        self._completed = 0

        self._init_ui()
        self._apply_styles()

    # ── UI Construction ───────────────────────────────────────────────

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Splitter: left (files) | right (settings) ───────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # LEFT: File list panel
        left = self._build_file_panel()
        splitter.addWidget(left)

        # RIGHT: Settings panel
        right = self._build_settings_panel()
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 400])
        root.addWidget(splitter)

        # ── BOTTOM: Log tabs + progress ──────────────────────────────────
        self._log_tabs = QTabWidget()
        self._log_tabs.setTabsClosable(True)
        self._log_tabs.tabCloseRequested.connect(self._close_log_tab)
        root.addWidget(self._log_tabs, 1)

        # ── Bottom bar: Start / Stop buttons ────────────────────────────
        bar = QHBoxLayout()
        bar.addStretch()

        self._btn_start = QPushButton("Start All")
        self._btn_start.setMinimumWidth(130)
        self._btn_start.clicked.connect(self._on_start_all)
        bar.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Stop All")
        self._btn_stop.setMinimumWidth(130)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._on_stop_all)
        bar.addWidget(self._btn_stop)

        self._btn_open_out = QPushButton("Open Output Folder")
        self._btn_open_out.clicked.connect(self._on_open_output)
        bar.addWidget(self._btn_open_out)

        bar.addStretch()
        root.addLayout(bar)

    # ── LEFT: File list panel ──────────────────────────────────────────

    def _build_file_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("Files to Translate")
        lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl)

        btn_row = QHBoxLayout()
        btn_add_file = QPushButton("Add File(s)")
        btn_add_file.clicked.connect(self._on_add_files)
        btn_row.addWidget(btn_add_file)

        btn_add_folder = QPushButton("Add Folder")
        btn_add_folder.clicked.connect(self._on_add_folder)
        btn_row.addWidget(btn_add_folder)

        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self._on_remove_selected)
        btn_row.addWidget(btn_remove)

        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._on_clear_all)
        btn_row.addWidget(btn_clear)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["File Name", "Type", "Status", "Progress"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 50)
        self._table.setColumnWidth(2, 80)
        self._table.setColumnWidth(3, 100)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        return panel

    # ── RIGHT: Settings panel ──────────────────────────────────────────

    def _build_settings_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("Settings")
        lbl.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(lbl)

        # Output directory
        g1 = QGroupBox("Output")
        g1l = QVBoxLayout(g1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Output Dir:"))
        self._edit_outdir = QLineEdit("output")
        row.addWidget(self._edit_outdir)
        btn_browse = QPushButton("Browse")
        btn_browse.clicked.connect(self._on_browse_outdir)
        row.addWidget(btn_browse)
        g1l.addLayout(row)

        # Concurrency
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Concurrency:"))
        self._spin_concurrency = QSpinBox()
        self._spin_concurrency.setRange(1, 20)
        self._spin_concurrency.setValue(10)
        row2.addWidget(self._spin_concurrency)
        row2.addStretch()
        g1l.addLayout(row2)
        layout.addWidget(g1)

        # Options
        g2 = QGroupBox("Options")
        g2l = QVBoxLayout(g2)
        self._chk_no_summary = QCheckBox("Skip summary generation (--no-summary)")
        self._chk_no_summary.setChecked(False)
        g2l.addWidget(self._chk_no_summary)

        self._chk_no_translation = QCheckBox("Skip translation (--no-translation)")
        self._chk_no_translation.setChecked(False)
        g2l.addWidget(self._chk_no_translation)

        self._lbl_count = QLabel("0 files loaded")
        g2l.addWidget(self._lbl_count)
        layout.addWidget(g2)

        layout.addStretch()
        return panel

    # ── Styling ────────────────────────────────────────────────────────

    def _apply_styles(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color: {self.COLOR_BG};
                color: {self.COLOR_TEXT};
                font-size: 12px;
            }}
            QGroupBox {{
                border: 1px solid #45475a;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
                font-weight: bold;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                color: {self.COLOR_ACCENT};
            }}
            QPushButton {{
                background-color: #45475a;
                border: 1px solid #585b70;
                border-radius: 4px;
                padding: 6px 14px;
                color: {self.COLOR_TEXT};
                min-height: 24px;
            }}
            QPushButton:hover {{
                background-color: #585b70;
            }}
            QPushButton:pressed {{
                background-color: #313244;
            }}
            QPushButton:disabled {{
                background-color: #313244;
                color: #6c7086;
            }}
            QLineEdit, QSpinBox {{
                background-color: #313244;
                border: 1px solid #45475a;
                border-radius: 4px;
                padding: 4px 8px;
                color: {self.COLOR_TEXT};
            }}
            QCheckBox {{
                spacing: 8px;
            }}
            QTableWidget {{
                background-color: {self.COLOR_PANEL};
                alternate-background-color: #2e2e42;
                gridline-color: #45475a;
                border: 1px solid #45475a;
                border-radius: 4px;
            }}
            QTableWidget::item {{
                padding: 4px;
            }}
            QHeaderView::section {{
                background-color: #313244;
                border: none;
                padding: 6px;
                font-weight: bold;
                color: {self.COLOR_ACCENT};
            }}
            QTabWidget::pane {{
                border: 1px solid #45475a;
                border-radius: 4px;
                background-color: {self.COLOR_PANEL};
            }}
            QTabBar::tab {{
                background-color: #313244;
                border: 1px solid #45475a;
                padding: 6px 14px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background-color: {self.COLOR_ACCENT};
                color: #1e1e2e;
            }}
            QTextEdit {{
                background-color: #11111b;
                color: #a6e3a1;
                border: none;
                font-family: "Cascadia Code", "Consolas", monospace;
                font-size: 11px;
            }}
            QProgressBar {{
                border: 1px solid #45475a;
                border-radius: 3px;
                text-align: center;
                height: 16px;
            }}
            QProgressBar::chunk {{
                background-color: {self.COLOR_RUNNING.name()};
                border-radius: 2px;
            }}
            QSplitter::handle {{
                background-color: #45475a;
                width: 2px;
            }}
        """)

    # ── File management ────────────────────────────────────────────────

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select paper files",
            "",
            "Papers (*.html *.pdf);;All Files (*.*)"
        )
        for p in paths:
            self._add_file(p)

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if not folder:
            return
        for root, _, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(('.html', '.pdf')):
                    self._add_file(os.path.join(root, f))

    def _on_remove_selected(self):
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        for row in sorted(rows, reverse=True):
            path = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            self._remove_file(path)
            self._table.removeRow(row)
        self._update_count()

    def _on_clear_all(self):
        for path in list(self._files.keys()):
            self._remove_file(path)
        self._table.setRowCount(0)
        self._update_count()

    def _add_file(self, path: str):
        path = os.path.abspath(path)
        if path in self._files:
            return
        item = FileItem(path=path)
        self._files[path] = item

        row = self._table.rowCount()
        self._table.insertRow(row)

        # Column 0: name
        name_item = QTableWidgetItem(item.name)
        name_item.setData(Qt.ItemDataRole.UserRole, path)
        name_item.setToolTip(path)
        self._table.setItem(row, 0, name_item)

        # Column 1: type
        self._table.setItem(row, 1, QTableWidgetItem(item.file_type))

        # Column 2: status
        self._table.setItem(row, 2, QTableWidgetItem("Pending"))

        # Column 3: progress
        prog = QProgressBar()
        prog.setRange(0, 0)  # indeterminate
        prog.hide()
        self._table.setCellWidget(row, 3, prog)

        self._update_count()

    def _remove_file(self, path: str):
        item = self._files.pop(path, None)
        if item and item.process:
            item.process.kill()
        if path in self._tab_indices:
            idx = self._tab_indices.pop(path)
            if idx < self._log_tabs.count():
                self._log_tabs.removeTab(idx)
            # Fix remaining indices
            for p, i in list(self._tab_indices.items()):
                if i > idx:
                    self._tab_indices[p] = i - 1

    def _update_count(self):
        self._lbl_count.setText(f"{len(self._files)} files loaded")

    # ── Settings ───────────────────────────────────────────────────────

    def _on_browse_outdir(self):
        folder = QFileDialog.getExistingDirectory(self, "Select output directory")
        if folder:
            self._edit_outdir.setText(folder)

    # ── Execution ──────────────────────────────────────────────────────

    def _on_start_all(self):
        if not self._files:
            QMessageBox.information(self, "No files", "Add files to translate first.")
            return

        out_dir = self._edit_outdir.text().strip() or "output"
        concurrency = str(self._spin_concurrency.value())
        no_summary = self._chk_no_summary.isChecked()
        no_translation = self._chk_no_translation.isChecked()

        self._btn_start.setEnabled(False)
        self._btn_stop.setEnabled(True)

        for path, item in self._files.items():
            if item.status in (FileStatus.RUNNING, FileStatus.DONE):
                continue
            self._launch_process(path, item, out_dir, concurrency, no_summary, no_translation)

    def _on_stop_all(self):
        for item in self._files.values():
            if item.process and item.process.state() != QProcess.ProcessState.NotRunning:
                item.process.kill()
                item.status = FileStatus.FAILED
        self._btn_stop.setEnabled(False)
        self._btn_start.setEnabled(True)
        self._refresh_table()

    def _on_open_output(self):
        out_dir = self._edit_outdir.text().strip() or "output"
        abs_out = os.path.abspath(out_dir)
        os.makedirs(abs_out, exist_ok=True)
        os.startfile(abs_out)

    def _launch_process(self, path: str, item: FileItem, out_dir: str,
                        concurrency: str, no_summary: bool, no_translation: bool):
        item.status = FileStatus.RUNNING

        # Create log widget
        log_widget = QTextEdit()
        log_widget.setReadOnly(True)
        f = QFont("Consolas", 10)
        f.setStyleHint(QFont.StyleHint.Monospace)
        log_widget.setFont(f)
        item.log_widget = log_widget

        tab_name = item.name[:40] + ("..." if len(item.name) > 40 else "")
        idx = self._log_tabs.addTab(log_widget, f"[RUN] {tab_name}")
        self._tab_indices[path] = idx
        self._log_tabs.setCurrentIndex(idx)

        # Build command
        args = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py"),
            path,
            "-c", concurrency,
            "--output-dir", out_dir,
        ]
        if no_summary:
            args.append("--no-summary")
        if no_translation:
            args.append("--no-translation")

        # Launch QProcess
        proc = QProcess()
        # Use SeparateChannels to capture both stdout and stderr
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        proc.readyReadStandardOutput.connect(lambda p=path, pr=proc: self._on_stdout(p, pr))
        proc.readyReadStandardError.connect(lambda p=path, pr=proc: self._on_stdout(p, pr))
        proc.finished.connect(lambda ec, es, p=path: self._on_finished(p, ec, es))
        item.process = proc
        self._running += 1

        self._append_log(path, f"$ python {' '.join(args)}\n")
        self._append_log(path, f"{'─' * 60}\n")
        proc.start(sys.executable, args)

        self._refresh_table()

    def _on_stdout(self, path: str, proc: QProcess):
        text = proc.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self._append_log(path, text)

    def _on_finished(self, path: str, exit_code: int, exit_status):
        item = self._files.get(path)
        if not item:
            return

        self._running -= 1

        if exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0:
            item.status = FileStatus.DONE
            self._completed += 1
        else:
            item.status = FileStatus.FAILED

        self._append_log(path, f"\n{'─' * 60}\n")
        if item.status == FileStatus.DONE:
            self._append_log(path, "Processing complete.\n")
        else:
            self._append_log(path, f"Process exited with code {exit_code}\n")

        # Update tab title
        if path in self._tab_indices:
            idx = self._tab_indices[path]
            label = item.name[:40] + ("..." if len(item.name) > 40 else "")
            prefix = "[OK]" if item.status == FileStatus.DONE else "[FAIL]"
            self._log_tabs.setTabText(idx, f"{prefix} {label}")

        # Check if all done
        all_done = not any(
            f.status in (FileStatus.PENDING, FileStatus.RUNNING)
            for f in self._files.values()
        )
        if all_done:
            self._btn_stop.setEnabled(False)
            self._btn_start.setEnabled(True)
            QMessageBox.information(
                self, "All Done",
                f"All {self._completed} file(s) processed.\n"
                f"Output is in: {os.path.abspath(self._edit_outdir.text().strip() or 'output')}"
            )

        self._refresh_table()

    def _append_log(self, path: str, text: str):
        item = self._files.get(path)
        if not item or not item.log_widget:
            return
        item.log_widget.moveCursor(QTextCursor.MoveOperation.End)
        item.log_widget.insertPlainText(text)
        # auto-scroll
        sb = item.log_widget.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _close_log_tab(self, idx: int):
        # Find which file this tab belongs to
        for path, ti in self._tab_indices.items():
            if ti == idx:
                del self._tab_indices[path]
                break
        self._log_tabs.removeTab(idx)
        # Fix remaining indices
        for p in list(self._tab_indices.keys()):
            if self._tab_indices[p] > idx:
                self._tab_indices[p] -= 1

    # ── Table refresh ──────────────────────────────────────────────────

    def _refresh_table(self):
        for row in range(self._table.rowCount()):
            path = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            item = self._files.get(path)
            if not item:
                continue

            # Status column
            status_item = self._table.item(row, 2)
            if status_item:
                status_item.setText(item.status.value.capitalize())
                color = {
                    FileStatus.PENDING: self.COLOR_PENDING,
                    FileStatus.RUNNING: self.COLOR_RUNNING,
                    FileStatus.DONE: self.COLOR_DONE,
                    FileStatus.FAILED: self.COLOR_FAILED,
                }[item.status]
                status_item.setForeground(color)

            # Progress bar
            prog = self._table.cellWidget(row, 3)
            if prog:
                if item.status == FileStatus.RUNNING:
                    prog.show()
                elif item.status == FileStatus.DONE:
                    prog.hide()
                else:
                    prog.hide()


# ── Entry point ────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PaperTranslator")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

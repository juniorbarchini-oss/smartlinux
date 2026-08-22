import os
from typing import List, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QButtonGroup, QFileDialog,
    QMessageBox, QCheckBox, QListWidget, QListWidgetItem, QFrame
)
from PySide6.QtCore import Qt
from ..core.models import DiskInfo, HealthStatus
from ..core.exporter import ReportExporter


class ExportDialog(QDialog):
    """Interactive dialog to select disks, format (.md, .pdf, .doc, .xls), and destination folder."""

    def __init__(self, available_disks: List[DiskInfo], selected_disk: Optional[DiskInfo] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exportar Informes S.M.A.R.T.")
        self.setMinimumWidth(540)
        self.setMinimumHeight(460)

        self.available_disks = available_disks
        self.selected_disk = selected_disk
        self.exported_files: List[str] = []

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # Header
        header = QLabel("📄 Exportar Informes de Diagnóstico S.M.A.R.T.")
        header.setStyleSheet("font-size: 17px; font-weight: bold; color: #58a6ff;")
        layout.addWidget(header)

        desc = QLabel("Seleccione los discos diagnosticados, el formato deseado y la carpeta de destino.")
        desc.setStyleSheet("color: #8b949e; font-size: 12px;")
        layout.addWidget(desc)

        # 1. Disk Selection List
        disk_frame = QFrame()
        disk_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px;")
        disk_box = QVBoxLayout(disk_frame)
        disk_box.setSpacing(8)

        disk_hdr = QHBoxLayout()
        disk_title = QLabel("1. SELECCIONAR DISCOS A EXPORTAR")
        disk_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #58a6ff; letter-spacing: 0.5px;")
        disk_hdr.addWidget(disk_title)
        disk_hdr.addStretch()

        self.btn_select_all = QPushButton("Seleccionar Todos")
        self.btn_select_all.setFixedSize(130, 26)
        self.btn_select_all.clicked.connect(self._select_all_disks)
        disk_hdr.addWidget(self.btn_select_all)

        self.btn_deselect_all = QPushButton("Deseleccionar")
        self.btn_deselect_all.setFixedSize(100, 26)
        self.btn_deselect_all.clicked.connect(self._deselect_all_disks)
        disk_hdr.addWidget(self.btn_deselect_all)

        disk_box.addLayout(disk_hdr)

        self.disk_list_widget = QListWidget()
        self.disk_list_widget.setStyleSheet("background-color: #0d1117; border: 1px solid #30363d; border-radius: 6px;")
        self.disk_list_widget.setMinimumHeight(120)

        for disk in self.available_disks:
            item = QListWidgetItem()
            host_tag = f"[{disk.server_name}] " if disk.is_remote and disk.server_name else "[Local] "
            status_tag = f"{disk.health_status.icon} {disk.health_status.label}" if disk.attributes else "⚪ (Sin telemetría)"
            item.setText(f"{host_tag}{disk.device_path} • {disk.model} ({disk.size_human}) — {status_tag}")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            
            # Check by default if it was the actively selected disk or has attributes
            if self.selected_disk and disk.device_path == self.selected_disk.device_path and disk.server_id == self.selected_disk.server_id:
                item.setCheckState(Qt.Checked)
            elif len(self.available_disks) == 1 or len(disk.attributes) > 0:
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)

            item.setData(Qt.UserRole, disk)
            self.disk_list_widget.addItem(item)

        disk_box.addWidget(self.disk_list_widget)
        layout.addWidget(disk_frame)

        # 2. Format Selection
        fmt_frame = QFrame()
        fmt_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px;")
        fmt_box = QVBoxLayout(fmt_frame)
        fmt_box.setSpacing(8)

        fmt_title = QLabel("2. FORMATO DE EXPORTACIÓN")
        fmt_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #58a6ff; letter-spacing: 0.5px;")
        fmt_box.addWidget(fmt_title)

        fmt_row = QHBoxLayout()
        self.fmt_group = QButtonGroup(self)

        self.rb_md = QRadioButton("📝 Markdown (.md)")
        self.rb_pdf = QRadioButton("📕 PDF (.pdf)")
        self.rb_doc = QRadioButton("📘 Word (.doc)")
        self.rb_xls = QRadioButton("📊 Excel (.xls)")

        self.rb_md.setChecked(True)

        self.fmt_group.addButton(self.rb_md, 0)
        self.fmt_group.addButton(self.rb_pdf, 1)
        self.fmt_group.addButton(self.rb_doc, 2)
        self.fmt_group.addButton(self.rb_xls, 3)

        fmt_row.addWidget(self.rb_md)
        fmt_row.addWidget(self.rb_pdf)
        fmt_row.addWidget(self.rb_doc)
        fmt_row.addWidget(self.rb_xls)
        fmt_box.addLayout(fmt_row)

        self.chk_consolidated = QCheckBox("Generar un único archivo consolidado con todos los discos seleccionados")
        self.chk_consolidated.setChecked(True)
        fmt_box.addWidget(self.chk_consolidated)

        layout.addWidget(fmt_frame)

        # 3. Output Folder Selector
        dir_frame = QFrame()
        dir_frame.setStyleSheet("background-color: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px;")
        dir_box = QVBoxLayout(dir_frame)
        dir_box.setSpacing(8)

        dir_title = QLabel("3. CARPETA DE DESTINO")
        dir_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #58a6ff; letter-spacing: 0.5px;")
        dir_box.addWidget(dir_title)

        dir_row = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setText(ReportExporter.DEFAULT_EXPORT_DIR)
        dir_row.addWidget(self.dir_input)

        self.btn_browse = QPushButton("Explorar Carpeta...")
        self.btn_browse.clicked.connect(self._browse_directory)
        dir_row.addWidget(self.btn_browse)

        dir_box.addLayout(dir_row)
        layout.addWidget(dir_frame)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.export_btn = QPushButton("🚀 Exportar Informes")
        self.export_btn.setObjectName("PrimaryButton")
        self.export_btn.clicked.connect(self._do_export)
        btn_layout.addWidget(self.export_btn)

        layout.addLayout(btn_layout)

    def _select_all_disks(self):
        for i in range(self.disk_list_widget.count()):
            self.disk_list_widget.item(i).setCheckState(Qt.Checked)

    def _deselect_all_disks(self):
        for i in range(self.disk_list_widget.count()):
            self.disk_list_widget.item(i).setCheckState(Qt.Unchecked)

    def _browse_directory(self):
        current_dir = self.dir_input.text().strip() or os.path.expanduser("~")
        selected_dir = QFileDialog.getExistingDirectory(self, "Seleccionar Carpeta de Destino", current_dir)
        if selected_dir:
            self.dir_input.setText(selected_dir)

    def _get_selected_disks(self) -> List[DiskInfo]:
        selected: List[DiskInfo] = []
        for i in range(self.disk_list_widget.count()):
            item = self.disk_list_widget.item(i)
            if item.checkState() == Qt.Checked:
                disk = item.data(Qt.UserRole)
                if disk:
                    selected.append(disk)
        return selected

    def _get_selected_format(self) -> str:
        if self.rb_pdf.isChecked():
            return "pdf"
        elif self.rb_doc.isChecked():
            return "doc"
        elif self.rb_xls.isChecked():
            return "xls"
        return "md"

    def _do_export(self):
        selected_disks = self._get_selected_disks()
        if not selected_disks:
            QMessageBox.warning(self, "Sin Selección", "Por favor marque al menos un disco para exportar.")
            return

        target_dir = self.dir_input.text().strip()
        if not target_dir:
            QMessageBox.warning(self, "Carpeta Requerida", "Debe especificar una carpeta de destino válida.")
            return

        file_fmt = self._get_selected_format()
        consolidated = self.chk_consolidated.isChecked()

        ok, paths = ReportExporter.export(
            disks=selected_disks,
            output_dir=target_dir,
            file_format=file_fmt,
            consolidated=consolidated
        )

        if ok:
            self.exported_files = paths
            self.accept()
        else:
            QMessageBox.critical(self, "Error al Exportar", f"Ocurrió un error:\n\n{paths[0] if paths else 'Error desconocido'}")

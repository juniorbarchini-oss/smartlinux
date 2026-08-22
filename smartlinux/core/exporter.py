import datetime
import html
import json
import os
import re
from typing import List, Optional, Tuple
from PySide6.QtGui import QTextDocument, QPageSize, QPageLayout, QPdfWriter
from PySide6.QtCore import QMarginsF
from .models import DiskInfo, HealthStatus


class ReportExporter:
    """Exports disk S.M.A.R.T. diagnostic reports to Markdown (.md), PDF (.pdf), Word (.doc), and Excel (.xls)."""

    DEFAULT_EXPORT_DIR = os.path.expanduser("~/Documents")

    @classmethod
    def generate_markdown(cls, disk: DiskInfo) -> str:
        """Generates a clean GFM markdown document for a single disk."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        host_info = f"Remoto ({disk.server_name})" if disk.is_remote else "Local (Este equipo)"

        health_icon = disk.health_status.icon
        health_label = disk.health_status.label

        md = []
        md.append(f"# 📊 Informe de Diagnóstico S.M.A.R.T. - {disk.model}")
        md.append("")
        md.append(f"> **Fecha y Hora:** `{now_str}`  ")
        md.append(f"> **Ubicación:** `{host_info}`  ")
        md.append(f"> **Dispositivo:** `{disk.device_path}`  ")
        md.append("")
        md.append("---")
        md.append("")

        # Device Info
        md.append("## 🖥️ Información del Dispositivo")
        md.append("")
        md.append("| Parámetro | Valor |")
        md.append("| :--- | :--- |")
        md.append(f"| **Modelo** | `{disk.model}` |")
        md.append(f"| **Número de Serie** | `{disk.serial}` |")
        md.append(f"| **Versión de Firmware** | `{disk.firmware}` |")
        md.append(f"| **Capacidad Comercial** | `{disk.size_human}` |")
        md.append(f"| **Interfaz / Protocolo** | `{disk.protocol}` |")
        md.append(f"| **Tipo de Unidad** | `{disk.rotation_rate}` |")
        md.append(f"| **Estado de Salud General** | {health_icon} **{health_label}** ({disk.health_summary}) |")
        md.append("")

        # Telemetry
        md.append("## ⚡ Telemetría y Métricas Rápidas")
        md.append("")
        temp_val = disk.formatted_temperature
        hours_val = disk.formatted_power_on
        cycles_val = f"{disk.power_cycles:,}" if disk.power_cycles is not None else "N/A"

        md.append(f"* **🌡️ Temperatura Actual:** `{temp_val}`")
        md.append(f"* **⏱️ Horas de Encendido (POH):** `{hours_val}`")
        md.append(f"* **🔄 Ciclos de Encendido:** `{cycles_val}`")
        md.append("")

        # SMART Attributes Table
        md.append("## 📋 Tabla de Atributos S.M.A.R.T.")
        md.append("")
        if disk.attributes:
            md.append("| ID | Atributo / Parámetro | Valor Actual | Peor Valor | Umbral | Valor Crudo (Raw) | Estado |")
            md.append("| :---: | :--- | :---: | :---: | :---: | :--- | :---: |")
            for attr in disk.attributes:
                icon = attr.status_type.icon
                md.append(
                    f"| `{attr.id}` | **{attr.name}** | {attr.current} | {attr.worst} | {attr.threshold} | `{attr.raw}` | {icon} {attr.status} |"
                )
        else:
            md.append("*No se pudieron recuperar atributos SMART detallados para esta unidad.*")
        md.append("")

        # Raw JSON Log
        if disk.raw_json:
            md.append("---")
            md.append("")
            md.append("## 🔍 Registro Técnico Raw JSON (smartctl)")
            md.append("<details>")
            md.append("<summary>Haga clic para desplegar los datos crudos en formato JSON</summary>")
            md.append("")
            md.append("```json")
            md.append(json.dumps(disk.raw_json, indent=2, ensure_ascii=False))
            md.append("```")
            md.append("</details>")
            md.append("")

        md.append("---")
        md.append("*Generado automáticamente por **SmartLinux** - Diagnóstico S.M.A.R.T. on-demand para Linux.*")
        return "\n".join(md)

    @classmethod
    def generate_html_report(cls, disks: List[DiskInfo]) -> str:
        """Generates an HTML report suitable for PDF rendering and Word (.doc) export."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        html_parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            "<style>",
            "body { font-family: 'Segoe UI', Helvetica, Arial, sans-serif; margin: 30px; color: #1e293b; line-height: 1.5; font-size: 13px; }",
            "h1 { color: #0f172a; border-bottom: 2px solid #3b82f6; padding-bottom: 8px; font-size: 22px; }",
            "h2 { color: #1e40af; margin-top: 24px; border-bottom: 1px solid #e2e8f0; padding-bottom: 4px; font-size: 16px; }",
            ".meta { background-color: #f8fafc; border-left: 4px solid #3b82f6; padding: 10px 14px; margin-bottom: 20px; font-size: 12px; }",
            "table { width: 100%; border-collapse: collapse; margin-top: 12px; margin-bottom: 20px; font-size: 12px; }",
            "th { background-color: #f1f5f9; color: #475569; font-weight: 600; text-align: left; padding: 8px; border: 1px solid #cbd5e1; }",
            "td { padding: 7px 8px; border: 1px solid #e2e8f0; }",
            "tr:nth-child(even) { background-color: #f8fafc; }",
            ".badge-ok { background-color: #dcfce7; color: #166534; padding: 3px 8px; border-radius: 4px; font-weight: bold; }",
            ".badge-warn { background-color: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 4px; font-weight: bold; }",
            ".badge-fail { background-color: #fee2e2; color: #991b1b; padding: 3px 8px; border-radius: 4px; font-weight: bold; }",
            ".metric-box { display: inline-block; background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; margin-right: 12px; min-width: 150px; }",
            ".metric-val { font-size: 16px; font-weight: bold; color: #0f172a; }",
            ".metric-lbl { font-size: 11px; color: #64748b; text-transform: uppercase; }",
            ".page-break { page-break-after: always; margin-top: 30px; }",
            "</style></head><body>",
            f"<h1>📊 Informe Consolidado de Diagnóstico S.M.A.R.T.</h1>",
            f"<div class='meta'><b>Fecha de Emisión:</b> {now_str} &nbsp;|&nbsp; <b>Total de Discos Diagnosticados:</b> {len(disks)}</div>"
        ]

        for idx, disk in enumerate(disks):
            host_info = f"Remoto ({disk.server_name})" if disk.is_remote else "Local"
            status_cls = "badge-ok" if disk.health_status == HealthStatus.HEALTHY else ("badge-warn" if disk.health_status == HealthStatus.WARNING else "badge-fail")

            html_parts.append(f"<h2>Disco #{idx+1}: {html.escape(disk.model)} ({disk.size_human})</h2>")
            html_parts.append(f"<p><b>Ubicación:</b> {host_info} &nbsp;|&nbsp; <b>Ruta:</b> <code>{html.escape(disk.device_path)}</code> &nbsp;|&nbsp; <b>S/N:</b> {html.escape(disk.serial)} &nbsp;|&nbsp; <b>Estado:</b> <span class='{status_cls}'>{disk.health_status.label}</span> ({html.escape(disk.health_summary)})</p>")

            temp_val = disk.formatted_temperature
            hours_val = disk.formatted_power_on
            cycles_val = f"{disk.power_cycles:,}" if disk.power_cycles is not None else "N/A"

            html_parts.append("<div>")
            html_parts.append(f"<div class='metric-box'><div class='metric-lbl'>Temperatura</div><div class='metric-val'>{temp_val}</div></div>")
            html_parts.append(f"<div class='metric-box'><div class='metric-lbl'>Horas Encendido</div><div class='metric-val'>{hours_val}</div></div>")
            html_parts.append(f"<div class='metric-box'><div class='metric-lbl'>Ciclos de Energía</div><div class='metric-val'>{cycles_val}</div></div>")
            html_parts.append("</div>")

            if disk.attributes:
                html_parts.append("<table>")
                html_parts.append("<tr><th>ID</th><th>Atributo</th><th>Actual</th><th>Peor</th><th>Umbral</th><th>Valor Crudo</th><th>Estado</th></tr>")
                for attr in disk.attributes:
                    attr_cls = "badge-ok" if attr.status_type == HealthStatus.HEALTHY else ("badge-warn" if attr.status_type == HealthStatus.WARNING else "badge-fail")
                    html_parts.append(
                        f"<tr><td style='text-align:center;'>{attr.id}</td>"
                        f"<td><b>{html.escape(attr.name)}</b></td>"
                        f"<td style='text-align:center;'>{html.escape(attr.current)}</td>"
                        f"<td style='text-align:center;'>{html.escape(attr.worst)}</td>"
                        f"<td style='text-align:center;'>{html.escape(attr.threshold)}</td>"
                        f"<td><code>{html.escape(attr.raw)}</code></td>"
                        f"<td style='text-align:center;'><span class='{attr_cls}'>{attr.status}</span></td></tr>"
                    )
                html_parts.append("</table>")
            else:
                html_parts.append("<p><i>No se registraron atributos SMART detallados.</i></p>")

            if idx < len(disks) - 1:
                html_parts.append("<hr style='border: 0; border-top: 1px dashed #cbd5e1; margin: 30px 0;'>")

        html_parts.append("<br><p style='font-size: 11px; color: #94a3b8; text-align: center;'>Generado automáticamente por SmartLinux</p>")
        html_parts.append("</body></html>")
        return "".join(html_parts)

    @classmethod
    def generate_excel_xml(cls, disks: List[DiskInfo]) -> str:
        """Generates an XML-based Excel Spreadsheet (.xls) readable by Excel and LibreOffice Calc."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        xml = [
            '<?xml version="1.0"?>',
            '<?mso-application progid="Excel.Sheet"?>',
            '<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"',
            ' xmlns:o="urn:schemas-microsoft-com:office:office"',
            ' xmlns:x="urn:schemas-microsoft-com:office:excel"',
            ' xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">',
            ' <Styles>',
            '  <Style ss:ID="Header"><Font ss:Bold="1" ss:Color="#FFFFFF"/><Interior ss:Color="#1F4E79" ss:Pattern="Solid"/></Style>',
            '  <Style ss:ID="SubHeader"><Font ss:Bold="1" ss:Color="#FFFFFF"/><Interior ss:Color="#2F5597" ss:Pattern="Solid"/></Style>',
            '  <Style ss:ID="Title"><Font ss:Size="14" ss:Bold="1"/><Interior ss:Color="#D9E1F2" ss:Pattern="Solid"/></Style>',
            '  <Style ss:ID="Center"><Alignment ss:Horizontal="Center"/></Style>',
            '  <Style ss:ID="Bold"><Font ss:Bold="1"/></Style>',
            ' </Styles>'
        ]

        for disk in disks:
            safe_sheet_name = re.sub(r'[\/\\:\?\*\[\]]', '_', f"{disk.name}_{disk.model}")[:30]
            xml.append(f' <Worksheet ss:Name="{html.escape(safe_sheet_name)}">')
            xml.append('  <Table>')
            xml.append('   <Column ss:Width="50"/>')
            xml.append('   <Column ss:Width="200"/>')
            xml.append('   <Column ss:Width="70"/>')
            xml.append('   <Column ss:Width="70"/>')
            xml.append('   <Column ss:Width="70"/>')
            xml.append('   <Column ss:Width="120"/>')
            xml.append('   <Column ss:Width="90"/>')
            xml.append('   <Column ss:Width="220"/>')

            xml.append(f'   <Row><Cell ss:StyleID="Title" ss:MergeAcross="7"><Data ss:Type="String">Informe SMART: {html.escape(disk.model)} ({disk.size_human}) - {now_str}</Data></Cell></Row>')
            xml.append('   <Row/>')

            host_info = f"Remoto ({disk.server_name})" if disk.is_remote else "Local"
            xml.append(f'   <Row><Cell ss:StyleID="Bold"><Data ss:Type="String">Dispositivo:</Data></Cell><Cell><Data ss:Type="String">{disk.device_path}</Data></Cell><Cell ss:StyleID="Bold"><Data ss:Type="String">Ubicación:</Data></Cell><Cell><Data ss:Type="String">{host_info}</Data></Cell></Row>')
            xml.append(f'   <Row><Cell ss:StyleID="Bold"><Data ss:Type="String">Número de Serie:</Data></Cell><Cell><Data ss:Type="String">{disk.serial}</Data></Cell><Cell ss:StyleID="Bold"><Data ss:Type="String">Firmware:</Data></Cell><Cell><Data ss:Type="String">{disk.firmware}</Data></Cell></Row>')
            xml.append(f'   <Row><Cell ss:StyleID="Bold"><Data ss:Type="String">Salud General:</Data></Cell><Cell><Data ss:Type="String">{disk.health_status.label} ({disk.health_summary})</Data></Cell><Cell ss:StyleID="Bold"><Data ss:Type="String">Temperatura:</Data></Cell><Cell><Data ss:Type="String">{disk.formatted_temperature}</Data></Cell></Row>')
            xml.append(f'   <Row><Cell ss:StyleID="Bold"><Data ss:Type="String">Horas Encendido:</Data></Cell><Cell><Data ss:Type="String">{disk.formatted_power_on}</Data></Cell><Cell ss:StyleID="Bold"><Data ss:Type="String">Ciclos Energía:</Data></Cell><Cell><Data ss:Type="String">{disk.power_cycles or "N/A"}</Data></Cell></Row>')
            xml.append('   <Row/>')

            xml.append('   <Row ss:StyleID="Header">')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">ID</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Atributo SMART</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Valor Actual</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Peor Valor</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Umbral</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Valor Crudo (Raw)</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Estado</Data></Cell>')
            xml.append('    <Cell ss:StyleID="Header"><Data ss:Type="String">Descripción</Data></Cell>')
            xml.append('   </Row>')

            for attr in disk.attributes:
                xml.append('   <Row>')
                xml.append(f'    <Cell ss:StyleID="Center"><Data ss:Type="Number">{attr.id}</Data></Cell>')
                xml.append(f'    <Cell><Data ss:Type="String">{html.escape(attr.name)}</Data></Cell>')
                xml.append(f'    <Cell ss:StyleID="Center"><Data ss:Type="String">{html.escape(str(attr.current))}</Data></Cell>')
                xml.append(f'    <Cell ss:StyleID="Center"><Data ss:Type="String">{html.escape(str(attr.worst))}</Data></Cell>')
                xml.append(f'    <Cell ss:StyleID="Center"><Data ss:Type="String">{html.escape(str(attr.threshold))}</Data></Cell>')
                xml.append(f'    <Cell><Data ss:Type="String">{html.escape(str(attr.raw))}</Data></Cell>')
                xml.append(f'    <Cell ss:StyleID="Center"><Data ss:Type="String">{html.escape(attr.status)}</Data></Cell>')
                xml.append(f'    <Cell><Data ss:Type="String">{html.escape(attr.description)}</Data></Cell>')
                xml.append('   </Row>')

            xml.append('  </Table>')
            xml.append(' </Worksheet>')

        xml.append('</Workbook>')
        return "\n".join(xml)

    @classmethod
    def export(
        cls,
        disks: List[DiskInfo],
        output_dir: str,
        file_format: str = "md",
        consolidated: bool = True
    ) -> Tuple[bool, List[str]]:
        """
        Exports reports for selected disks in the specified format.
        file_format: 'md', 'pdf', 'doc', 'xls'
        Returns (success, list_of_exported_file_paths).
        """
        if not disks:
            return False, ["No se seleccionó ningún disco para exportar."]

        os.makedirs(output_dir, exist_ok=True)
        date_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        exported_files: List[str] = []

        try:
            file_format = file_format.lower().replace(".", "")

            if consolidated or len(disks) == 1:
                if len(disks) == 1:
                    d = disks[0]
                    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', f"{d.name}_{d.model}").strip('_')
                    filename = f"SmartLinux_{safe_name}_{date_tag}.{file_format}"
                else:
                    filename = f"SmartLinux_Informe_Consolidado_{date_tag}.{file_format}"

                full_path = os.path.join(output_dir, filename)

                if file_format == "md":
                    combined_md = "\n\n---\n\n".join([cls.generate_markdown(d) for d in disks])
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(combined_md)
                elif file_format == "pdf":
                    doc = QTextDocument()
                    doc.setHtml(cls.generate_html_report(disks))
                    writer = QPdfWriter(full_path)
                    writer.setPageSize(QPageSize(QPageSize.A4))
                    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
                    doc.print_(writer)
                elif file_format == "doc":
                    html_content = cls.generate_html_report(disks)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(html_content)
                elif file_format in ("xls", "xlsx"):
                    xml_content = cls.generate_excel_xml(disks)
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(xml_content)
                else:
                    return False, [f"Formato no soportado: {file_format}"]

                exported_files.append(full_path)

            else:
                for d in disks:
                    safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', f"{d.name}_{d.model}").strip('_')
                    filename = f"SmartLinux_{safe_name}_{date_tag}.{file_format}"
                    full_path = os.path.join(output_dir, filename)

                    if file_format == "md":
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(cls.generate_markdown(d))
                    elif file_format == "pdf":
                        doc = QTextDocument()
                        doc.setHtml(cls.generate_html_report([d]))
                        writer = QPdfWriter(full_path)
                        writer.setPageSize(QPageSize(QPageSize.A4))
                        writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)
                        doc.print_(writer)
                    elif file_format == "doc":
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(cls.generate_html_report([d]))
                    elif file_format in ("xls", "xlsx"):
                        with open(full_path, "w", encoding="utf-8") as f:
                            f.write(cls.generate_excel_xml([d]))

                    exported_files.append(full_path)

            return True, exported_files

        except Exception as e:
            return False, [f"Error al exportar: {str(e)}"]


MarkdownExporter = ReportExporter

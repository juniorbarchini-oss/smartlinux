import datetime
import json
import os
import re
from typing import Optional, Tuple
from .models import DiskInfo, HealthStatus


class MarkdownExporter:
    """Exports full disk S.M.A.R.T. diagnostic reports to Markdown (.md)."""

    DEFAULT_EXPORT_DIR = os.path.expanduser("~/Documents")

    @classmethod
    def generate_markdown(cls, disk: DiskInfo) -> str:
        """Generates a clean GFM markdown document for the given disk."""
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

        # Section 1: Device Summary
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

        # Section 2: Quick Telemetry
        md.append("## ⚡ Telemetría y Métricas Rápidas")
        md.append("")
        temp_val = disk.formatted_temperature
        hours_val = disk.formatted_power_on
        cycles_val = f"{disk.power_cycles:,}" if disk.power_cycles is not None else "N/A"

        md.append(f"* **🌡️ Temperatura Actual:** `{temp_val}`")
        md.append(f"* **⏱️ Horas de Encendido (POH):** `{hours_val}`")
        md.append(f"* **🔄 Ciclos de Encendido:** `{cycles_val}`")
        md.append("")

        # Section 3: S.M.A.R.T. Attribute Table
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

        # Section 4: Raw JSON Log in collapsible details
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
    def export_to_file(cls, disk: DiskInfo, output_dir: Optional[str] = None) -> Tuple[bool, str]:
        """Saves the markdown report to a file. Returns (success, file_path_or_error)."""
        target_dir = output_dir or cls.DEFAULT_EXPORT_DIR
        os.makedirs(target_dir, exist_ok=True)

        date_tag = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_model = re.sub(r'[^a-zA-Z0-9_\-]', '_', disk.model).strip('_')
        safe_dev = re.sub(r'[^a-zA-Z0-9_\-]', '_', disk.name).strip('_')
        filename = f"SmartLinux_Report_{safe_model}_{safe_dev}_{date_tag}.md"
        full_path = os.path.join(target_dir, filename)

        try:
            content = cls.generate_markdown(disk)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True, full_path
        except Exception as e:
            return False, f"Error al guardar el informe: {str(e)}"

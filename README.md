# SmartLinux - Diagnóstico S.M.A.R.T. On-Demand

**SmartLinux** es una aplicación de escritorio nativa para Linux desarrollada con **Python 3 y PySide6 (Qt6)** diseñada para diagnosticar la salud, telemetría y estado S.M.A.R.T. de discos duros locales y remotos (Homelab) de forma visual, moderna y rápida.

---

## ⚡ Filosofía de Diseño

* **On-Demand:** Sin demonios ni procesos residentes en segundo plano. La app solo lee telemetría al abrirse o al pulsar *Scan Now*.
* **100% Asíncrona:** Todas las consultas a discos locales y conexiones SSH remotas se ejecutan en hilos (`QThreadPool`) para mantener la interfaz siempre fluida y responsiva.
* **Tema Oscuro Moderno:** Interfaz estilizada con tema oscuro de alto contraste y tarjetas visuales.

---

## 🚀 Características Principales

### 1. Barra Lateral de Dispositivos
* **Discos Locales:** Detección automática y filtrado de discos físicos reales (SATA, NVMe, USB), ignorando loops del sistema, volúmenes de Docker y discos virtuales.
* **Discos Remotos (Homelab SSH):** Gestión interactiva de servidores remotos por SSH (autenticación por clave o contraseña).
* **Semáforo Visual:** Iconos de estado en tiempo real (🟢 Saludable, 🟡 Advertencia, 🔴 Fallo).

### 2. Panel de Detalle
* **Cabecera:** Modelo, número de serie, firmware, protocolo y badge de salud general.
* **Métricas Rápidas:** Cuadrículas visuales con temperatura actual (°C), horas totales de encendido (POH) y ciclos de energía.
* **Tabla S.M.A.R.T.:** Tabla completa de atributos (ID, Nombre, Actual, Peor, Umbral, Raw, Estado).

### 3. Acciones
* **🔄 Scan Now:** Actualiza la telemetría del disco seleccionado de inmediato.
* **📄 Export Report:** Genera informes consolidados en formato Markdown (`.md`) listos para archivar o importar en Obsidian / notas personales.

---

## 📦 Instalación y Ejecución

### Requisitos Previos
* Linux (x86_64 / aarch64)
* `smartmontools` (`smartctl`)
* Python >= 3.10

### Ejecución Directa
```bash
cd /home/hbarchini/Documents/desarrollo/smartlinux
./smartlinux.sh
```

O activando el entorno virtual manualmente:
```bash
source .venv/bin/activate
python3 smartlinux/main.py
```

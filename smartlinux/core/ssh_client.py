import json
import os
import re
import sys
from typing import List, Tuple, Optional, Dict, Any
import paramiko
from .models import ServerConfig, DiskInfo, HealthStatus
from .smart_parser import SmartParser


# Comprehensive search PATH covering Linux (Debian, RHEL, Alpine, Arch, Proxmox, ZimaOS) and macOS (Apple Silicon / Intel)
UNIVERSAL_PATH = (
    "export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin:"
    "$PATH; "
)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """
    Extracts a JSON object from text output, ignoring any leading/trailing
    shell banners, motd messages, or sudo prompts.
    """
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except Exception:
            pass
    return None


def is_permission_denied_json(data: Optional[Dict[str, Any]]) -> bool:
    """Checks if the returned smartctl JSON reports a permission denial."""
    if not data:
        return False
    messages = data.get("smartctl", {}).get("messages", [])
    for msg in messages:
        str_msg = msg.get("string", "").lower()
        if any(term in str_msg for term in ["permission denied", "operation not permitted", "must be root", "access denied"]):
            return True
    return False


class RemoteSSHClient:
    """
    Generic, platform-agnostic SSH client for monitoring storage diagnostics
    across any Linux distribution, macOS (Darwin), or BSD host.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None
        self._remote_os: Optional[str] = None

    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        """Establishes an SSH connection using password or private key."""
        self.disconnect()
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            connect_kwargs: Dict[str, Any] = {
                "hostname": self.config.host,
                "port": self.config.port,
                "username": self.config.username,
                "timeout": timeout,
                "banner_timeout": timeout,
                "auth_timeout": timeout
            }

            if self.config.auth_type == "password" and self.config.password:
                connect_kwargs["password"] = self.config.password
                connect_kwargs["look_for_keys"] = False
                connect_kwargs["allow_agent"] = False
            elif self.config.auth_type == "key":
                key_path = os.path.expanduser(self.config.key_path or "~/.ssh/id_rsa")
                if not os.path.exists(key_path):
                    alt_ed = os.path.expanduser("~/.ssh/id_ed25519")
                    if os.path.exists(alt_ed):
                        key_path = alt_ed

                if os.path.exists(key_path):
                    connect_kwargs["key_filename"] = key_path
                else:
                    connect_kwargs["look_for_keys"] = True
            elif self.config.password:
                connect_kwargs["password"] = self.config.password
                connect_kwargs["look_for_keys"] = False

            self._client.connect(**connect_kwargs)
            self._detect_os()
            return True, "Conexión exitosa"
        except paramiko.AuthenticationException:
            self.disconnect()
            return False, "Error de autenticación SSH: Usuario o contraseña/clave incorrecta."
        except Exception as e:
            self.disconnect()
            return False, f"Fallo al conectar con {self.config.host}:{self.config.port}: {str(e)}"

    def disconnect(self):
        """Closes the SSH connection."""
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._remote_os = None

    def is_connected(self) -> bool:
        if self._client and self._client.get_transport():
            return self._client.get_transport().is_active()
        return False

    def _detect_os(self):
        """Identifies remote OS family (Linux, Darwin, FreeBSD, etc.)."""
        try:
            _, out, _ = self._raw_exec("uname -s")
            self._remote_os = out.strip()
        except Exception:
            self._remote_os = "Linux"

    def _raw_exec(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        """Executes a raw command with standard universal PATH."""
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return -1, "", msg

        full_cmd = UNIVERSAL_PATH + cmd
        stdin, stdout, stderr = self._client.exec_command(full_cmd, timeout=timeout)
        exit_status = stdout.channel.recv_exit_status()
        out_str = stdout.read().decode("utf-8", errors="replace")
        err_str = stderr.read().decode("utf-8", errors="replace")
        return exit_status, out_str, err_str

    def _exec_with_sudo(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        """Executes a command with sudo, injecting password non-interactively if available."""
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return -1, "", msg

        sudo_cmd = UNIVERSAL_PATH + f"sudo -S -p '' {cmd}"
        try:
            stdin, stdout, stderr = self._client.exec_command(sudo_cmd, timeout=timeout)
            if self.config.password:
                stdin.write(self.config.password + "\n")
                stdin.flush()
            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode("utf-8", errors="replace")
            err_str = stderr.read().decode("utf-8", errors="replace")
            return exit_status, out_str, err_str
        except Exception as e:
            return -1, "", str(e)

    def exec_smartctl(self, args: str, timeout: int = 15) -> Tuple[int, str, str]:
        """
        Executes smartctl dynamically across any remote environment,
        trying non-root execution and automatically escalating to sudo when required.
        """
        cmd = f"smartctl {args}"
        
        # If user is not root and password is available, try sudo directly first
        if self.config.username != "root" and self.config.password:
            code, out, err = self._exec_with_sudo(cmd, timeout=timeout)
            json_data = extract_json_object(out)
            if json_data and not is_permission_denied_json(json_data):
                return code, out, err

        # Try direct execution
        code, out, err = self._raw_exec(cmd, timeout=timeout)
        json_data = extract_json_object(out)
        if json_data and not is_permission_denied_json(json_data):
            return code, out, err

        # If direct failed due to permissions, fallback to sudo
        if self.config.password or self.config.username != "root":
            code_s, out_s, err_s = self._exec_with_sudo(cmd, timeout=timeout)
            return code_s, out_s, err_s

        return code, out, err

    def discover_remote_drives(self) -> List[DiskInfo]:
        """
        Universally discovers real physical storage drives on any platform:
        Linux (all distros), macOS (Darwin), or BSD.
        """
        drives: List[DiskInfo] = []
        is_darwin = (self._remote_os == "Darwin")

        # Universal Tier 1: smartctl --scan -j (Works on Linux, macOS, BSD)
        code, out, err = self.exec_smartctl("--scan -j")
        json_scan = extract_json_object(out)
        if json_scan and "devices" in json_scan:
            for dev in json_scan.get("devices", []):
                dev_name = dev.get("name", "")
                if not dev_name:
                    continue
                # Filter macOS disk images / virtual loops
                if is_darwin and any(x in dev_name for x in ["diskimage", "synthesized"]):
                    continue
                # Filter Linux loop devices
                if any(x in dev_name for x in ["/loop", "/ram", "/zram", "/dm-", "/sr"]):
                    continue

                disk = DiskInfo(
                    device_path=dev_name,
                    name=dev_name.split("/")[-1],
                    model="Disco Físico",
                    protocol=dev.get("protocol", "SATA").upper(),
                    is_remote=True,
                    server_id=self.config.id,
                    server_name=self.config.name
                )
                drives.append(disk)

        # Linux Specific Tier 2: lsblk
        if not drives and not is_darwin:
            code, out, err = self._raw_exec("lsblk -J -d -o NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA 2>/dev/null")
            json_lsblk = extract_json_object(out)
            if json_lsblk and "blockdevices" in json_lsblk:
                for dev in json_lsblk.get("blockdevices", []):
                    dev_name = dev.get("name", "")
                    dev_path = dev.get("path", f"/dev/{dev_name}")
                    dev_type = dev.get("type", "").lower()
                    
                    if dev_type != "disk":
                        continue
                    if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                        continue

                    model = dev.get("model") or "Disco Físico"
                    size_str = dev.get("size") or "Desconocido"
                    tran = dev.get("tran") or "SATA"

                    disk = DiskInfo(
                        device_path=dev_path,
                        name=dev_name,
                        model=model.strip(),
                        size_human=size_str,
                        protocol=tran.upper() if tran else "ATA/SATA",
                        is_remote=True,
                        server_id=self.config.id,
                        server_name=self.config.name
                    )
                    drives.append(disk)

        # macOS Specific Tier 3: diskutil list physical
        if not drives and is_darwin:
            code, out, err = self._raw_exec("diskutil list physical | grep -E '^/dev/disk'")
            if out.strip():
                for line in out.strip().splitlines():
                    match = re.search(r'(/dev/disk\d+)', line)
                    if match:
                        dev_path = match.group(1)
                        disk = DiskInfo(
                            device_path=dev_path,
                            name=dev_path.split("/")[-1],
                            model="Disco Mac Físico",
                            is_remote=True,
                            server_id=self.config.id,
                            server_name=self.config.name
                        )
                        drives.append(disk)

        # Linux Fallback Tier 4: /sys/block/
        if not drives and not is_darwin:
            code, out, err = self._raw_exec("ls -d /sys/block/sd* /sys/block/nvme* 2>/dev/null")
            if out.strip():
                for line in out.strip().splitlines():
                    name = line.strip().split("/")[-1]
                    if not name or "loop" in name or "ram" in name:
                        continue
                    disk = DiskInfo(
                        device_path=f"/dev/{name}",
                        name=name,
                        model="Disco Físico",
                        is_remote=True,
                        server_id=self.config.id,
                        server_name=self.config.name
                    )
                    drives.append(disk)

        return drives

    def read_remote_drive_smart(self, device_path: str) -> DiskInfo:
        """Reads full SMART diagnostic data from a drive on any platform."""
        code, out, err = self.exec_smartctl(f"-j -a {device_path}")

        json_data = extract_json_object(out)
        if json_data and not is_permission_denied_json(json_data):
            try:
                disk = SmartParser.parse_smart_json(json_data, device_path)
                disk.is_remote = True
                disk.server_id = self.config.id
                disk.server_name = self.config.name
                return disk
            except Exception as e:
                print(f"Error parsing SMART JSON: {e}")

        # Diagnosis of failure
        error_detail = err.strip()
        if is_permission_denied_json(json_data):
            error_detail = "Permiso denegado: se requieren privilegios de administrador/sudo."
        elif "not found" in error_detail.lower() or "command not found" in out.lower():
            if self._remote_os == "Darwin":
                error_detail = "smartctl no encontrado. En macOS instale con: brew install smartmontools"
            else:
                error_detail = "smartctl no encontrado. Instale el paquete 'smartmontools' en el servidor."
        elif "permission denied" in error_detail.lower() or "operation not permitted" in error_detail.lower():
            error_detail = "Permiso denegado al acceder al dispositivo de bloque. Verifique sudo o permisos."
        elif not error_detail:
            error_detail = f"Sin respuesta válida de smartctl (código {code})"

        disk = DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            is_remote=True,
            server_id=self.config.id,
            server_name=self.config.name,
            health_status=HealthStatus.FAILED,
            health_summary="Fallo de lectura SMART",
            error_message=error_detail
        )
        return disk

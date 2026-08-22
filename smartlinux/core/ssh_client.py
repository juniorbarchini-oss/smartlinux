import json
import os
from typing import List, Tuple, Optional, Dict, Any
import paramiko
from .models import ServerConfig, DiskInfo, HealthStatus
from .smart_parser import SmartParser


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Extracts JSON object from text even if preceded/followed by sudo banners or motd."""
    text = text.strip()
    if not text:
        return None
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
    """Checks if the JSON output is merely an error message stating Permission denied."""
    if not data:
        return False
    messages = data.get("smartctl", {}).get("messages", [])
    for msg in messages:
        str_msg = msg.get("string", "").lower()
        if "permission denied" in str_msg or "operation not permitted" in str_msg or "must be root" in str_msg:
            return True
    return False


class RemoteSSHClient:
    """Manages SSH connections to Homelab hosts and runs smartctl diagnostics."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        """Establishes an SSH connection using password or key."""
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

    def is_connected(self) -> bool:
        if self._client and self._client.get_transport():
            return self._client.get_transport().is_active()
        return False

    def _exec_command(self, cmd: str, timeout: int = 15, force_sudo: bool = False) -> Tuple[int, str, str]:
        """Executes a command over SSH with full PATH and sudo support."""
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return -1, "", msg

        path_env = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH; "

        # If user is not root and password exists and force_sudo or non-root user
        if (self.config.username != "root" and self.config.password) and (force_sudo or "smartctl" in cmd):
            sudo_cmd = path_env + f"sudo -S -p '' {cmd}"
            try:
                stdin, stdout, stderr = self._client.exec_command(sudo_cmd, timeout=timeout)
                stdin.write(self.config.password + "\n")
                stdin.flush()
                exit_status = stdout.channel.recv_exit_status()
                out_str = stdout.read().decode("utf-8", errors="replace")
                err_str = stderr.read().decode("utf-8", errors="replace")
                
                json_data = extract_json_object(out_str)
                if json_data and not is_permission_denied_json(json_data):
                    return exit_status, out_str, err_str
                if exit_status == 0:
                    return exit_status, out_str, err_str
            except Exception as e:
                pass

        # Direct execution fallback
        try:
            full_cmd = path_env + cmd
            stdin, stdout, stderr = self._client.exec_command(full_cmd, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode("utf-8", errors="replace")
            err_str = stderr.read().decode("utf-8", errors="replace")

            json_data = extract_json_object(out_str)
            if json_data and not is_permission_denied_json(json_data):
                return exit_status, out_str, err_str

            # If failed due to permission in stdout json or stderr, retry with sudo
            if (is_permission_denied_json(json_data) or "permission denied" in err_str.lower()) and self.config.password:
                sudo_cmd = path_env + f"sudo -S -p '' {cmd}"
                s_in, s_out, s_err = self._client.exec_command(sudo_cmd, timeout=timeout)
                s_in.write(self.config.password + "\n")
                s_in.flush()
                s_exit = s_out.channel.recv_exit_status()
                s_out_str = s_out.read().decode("utf-8", errors="replace")
                s_err_str = s_err.read().decode("utf-8", errors="replace")
                return s_exit, s_out_str, s_err_str

            return exit_status, out_str, err_str
        except Exception as e:
            return -1, "", str(e)

    def discover_remote_drives(self) -> List[DiskInfo]:
        """Lists physical drives on the remote host."""
        drives: List[DiskInfo] = []
        
        # 1. Try lsblk JSON
        code, out, err = self._exec_command("lsblk -J -d -o NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA")
        json_data = extract_json_object(out)
        
        if json_data and "blockdevices" in json_data:
            for dev in json_data.get("blockdevices", []):
                dev_name = dev.get("name", "")
                dev_path = dev.get("path", f"/dev/{dev_name}")
                dev_type = dev.get("type", "").lower()
                
                if dev_type != "disk":
                    continue
                if any(dev_name.startswith(p) for p in ["loop", "ram", "zram", "dm-", "sr", "cdrom"]):
                    continue

                model = dev.get("model") or "Disco Remoto"
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

        # 2. Fallback: smartctl --scan -j
        if not drives:
            code, out, err = self._exec_command("smartctl --scan -j", force_sudo=True)
            json_scan = extract_json_object(out)
            if json_scan and "devices" in json_scan:
                for dev in json_scan.get("devices", []):
                    dev_name = dev.get("name", "")
                    if not dev_name:
                        continue
                    disk = DiskInfo(
                        device_path=dev_name,
                        name=dev_name.split("/")[-1],
                        model="Disco Remoto",
                        protocol=dev.get("protocol", "SATA"),
                        is_remote=True,
                        server_id=self.config.id,
                        server_name=self.config.name
                    )
                    drives.append(disk)

        return drives

    def read_remote_drive_smart(self, device_path: str) -> DiskInfo:
        """Reads full SMART data for a specific remote drive."""
        cmd = f"smartctl -j -a {device_path}"
        code, out, err = self._exec_command(cmd, force_sudo=True)

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

        # If failed
        error_detail = err.strip()
        if is_permission_denied_json(json_data):
            error_detail = "Permiso denegado: configure permisos sudo para smartctl."
        elif "not found" in error_detail.lower():
            error_detail = "smartctl no está instalado en el servidor remoto."
        elif "permission denied" in error_detail.lower():
            error_detail = "Permiso denegado en el host remoto. Requiere sudo."
        elif not error_detail:
            error_detail = f"Sin datos SMART válidos (código {code})"

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

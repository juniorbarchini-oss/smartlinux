import json
import os
from typing import List, Tuple, Optional, Dict, Any
import paramiko
from .models import ServerConfig, DiskInfo, HealthStatus
from .smart_parser import SmartParser


class RemoteSSHClient:
    """Manages SSH connections to Homelab hosts and runs smartctl diagnostics."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None

    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        """Establishes an SSH connection using key or password."""
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

            if self.config.auth_type == "key":
                key_path = os.path.expanduser(self.config.key_path or "~/.ssh/id_rsa")
                if not os.path.exists(key_path):
                    # Check for ed25519 fallback
                    alt_ed = os.path.expanduser("~/.ssh/id_ed25519")
                    if os.path.exists(alt_ed):
                        key_path = alt_ed

                if os.path.exists(key_path):
                    connect_kwargs["key_filename"] = key_path
                else:
                    # Let paramiko search default agent/keys
                    connect_kwargs["look_for_keys"] = True
            elif self.config.password:
                connect_kwargs["password"] = self.config.password
                connect_kwargs["look_for_keys"] = False

            self._client.connect(**connect_kwargs)
            return True, "Conexión exitosa"
        except paramiko.AuthenticationException:
            self.disconnect()
            return False, "Error de autenticación SSH: Credenciales o clave inválida."
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

    def _exec_command(self, cmd: str, timeout: int = 10) -> Tuple[int, str, str]:
        """Executes a command over SSH and returns (return_code, stdout, stderr)."""
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return -1, "", msg

        try:
            stdin, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
            exit_status = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode("utf-8", errors="replace")
            err_str = stderr.read().decode("utf-8", errors="replace")
            return exit_status, out_str, err_str
        except Exception as e:
            return -1, "", str(e)

    def discover_remote_drives(self) -> List[DiskInfo]:
        """Lists physical drives on the remote host."""
        drives: List[DiskInfo] = []
        
        # 1. Try lsblk JSON
        code, out, err = self._exec_command("lsblk -J -d -o NAME,PATH,MODEL,SIZE,TRAN,TYPE,ROTA")
        if code == 0 and out.strip():
            try:
                data = json.loads(out)
                for dev in data.get("blockdevices", []):
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
            except Exception:
                pass

        # 2. If lsblk returned nothing, fallback to smartctl --scan -j
        if not drives:
            code, out, err = self._exec_command("smartctl --scan -j")
            if out.strip():
                try:
                    data = json.loads(out)
                    for dev in data.get("devices", []):
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
                except Exception:
                    pass

        return drives

    def read_remote_drive_smart(self, device_path: str) -> DiskInfo:
        """Reads full SMART data for a specific remote drive."""
        cmd = f"smartctl -j -a {device_path}"
        code, out, err = self._exec_command(cmd)

        if out.strip():
            try:
                data = json.loads(out)
                disk = SmartParser.parse_smart_json(data, device_path)
                disk.is_remote = True
                disk.server_id = self.config.id
                disk.server_name = self.config.name
                return disk
            except json.JSONDecodeError:
                pass

        # Failed
        disk = DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            is_remote=True,
            server_id=self.config.id,
            server_name=self.config.name,
            health_status=HealthStatus.FAILED,
            health_summary="Error al leer SMART vía SSH",
            error_message=err.strip() or f"Código de retorno: {code}"
        )
        return disk

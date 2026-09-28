import json
import os
import re
import sys
from typing import List, Tuple, Optional, Dict, Any
import paramiko
from .models import ServerConfig, DiskInfo, HealthStatus
from .smart_parser import SmartParser


UNIVERSAL_PATH = (
    "export PATH=/opt/homebrew/bin:/opt/homebrew/sbin:"
    "/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin:"
    "$PATH; "
)


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
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
    if not data:
        return False
    messages = data.get("smartctl", {}).get("messages", [])
    for msg in messages:
        str_msg = msg.get("string", "").lower()
        if any(term in str_msg for term in ["permission denied", "operation not permitted", "must be root", "access denied"]):
            return True
    return False


class HostKeyUntrustedException(paramiko.SSHException):
    """Raised when a remote host key is not present in ~/.ssh/known_hosts."""
    def __init__(self, hostname: str, key: paramiko.PKey):
        self.hostname = hostname
        self.key = key
        self.fingerprint = ":".join(f"{b:02x}" for b in key.get_fingerprint())
        self.key_type = key.get_name()
        super().__init__(
            f"Host key for '{hostname}' is not trusted (not present in ~/.ssh/known_hosts).\n"
            f"Key Type: {self.key_type}\n"
            f"Fingerprint: {self.fingerprint}\n\n"
            f"Verification was rejected to prevent potential Man-in-the-Middle (MITM) attacks."
        )


class StrictKnownHostsPolicy(paramiko.MissingHostKeyPolicy):
    """
    Policy that rejects unknown host keys and records the key metadata
    to allow user notification and explicit confirmation.
    """
    def __init__(self, client_ref: "RemoteSSHClient"):
        self.client_ref = client_ref

    def missing_host_key(self, client, hostname, key):
        self.client_ref.last_untrusted_host_key = (hostname, key)
        raise HostKeyUntrustedException(hostname, key)


class RemoteSSHClient:
    """
    Generic, platform-agnostic SSH client for monitoring storage diagnostics
    across any Linux distribution, macOS (Darwin), or BSD host.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._client: Optional[paramiko.SSHClient] = None
        self._remote_os: Optional[str] = None
        self.last_untrusted_host_key: Optional[Tuple[str, paramiko.PKey]] = None

    @staticmethod
    def get_known_hosts_path() -> str:
        return os.path.expanduser("~/.ssh/known_hosts")

    @classmethod
    def trust_and_save_host_key(cls, hostname: str, key: paramiko.PKey) -> bool:
        """Saves a verified host key to ~/.ssh/known_hosts with 0600 permissions."""
        kh_path = cls.get_known_hosts_path()
        ssh_dir = os.path.dirname(kh_path)
        if not os.path.exists(ssh_dir):
            os.makedirs(ssh_dir, mode=0o700, exist_ok=True)
            try:
                os.chmod(ssh_dir, 0o700)
            except Exception:
                pass

        host_keys = paramiko.HostKeys()
        if os.path.exists(kh_path):
            try:
                host_keys.load(kh_path)
            except Exception as e:
                print(f"Warning: error loading existing known_hosts: {e}")

        host_keys.add(hostname, key.get_name(), key)
        temp_file = kh_path + f".tmp.{os.getpid()}"
        try:
            host_keys.save(temp_file)
            os.chmod(temp_file, 0o600)
            os.replace(temp_file, kh_path)
            os.chmod(kh_path, 0o600)
            return True
        except Exception as e:
            print(f"Error saving host key to {kh_path}: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False

    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        self.disconnect()
        self.last_untrusted_host_key = None
        self._client = paramiko.SSHClient()

        # Load system and user known_hosts
        self._client.load_system_host_keys()
        kh_path = self.get_known_hosts_path()
        if os.path.exists(kh_path):
            try:
                self._client.load_host_keys(kh_path)
            except Exception as e:
                print(f"Warning loading known_hosts from {kh_path}: {e}")

        # Strict policy: reject unknown host keys instead of silently auto-adding them
        self._client.set_missing_host_key_policy(StrictKnownHostsPolicy(self))

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
            return True, "Connected successfully"
        except paramiko.BadHostKeyException as e:
            self.disconnect()
            fp = ":".join(f"{b:02x}" for b in e.key.get_fingerprint())
            return False, (
                f"🚨 SECURITY ALERT: Host Key Mismatch for '{self.config.host}'!\n\n"
                f"The host key presented by the remote server DOES NOT match the recorded key in ~/.ssh/known_hosts.\n\n"
                f"Server: {self.config.host}:{self.config.port}\n"
                f"Key Type: {e.key.get_name()}\n"
                f"Fingerprint: {fp}\n\n"
                f"This may indicate a Man-in-the-Middle (MITM) attack or the server host keys have changed.\n"
                f"SmartLinux has aborted the connection to protect your data."
            )
        except HostKeyUntrustedException as e:
            self.disconnect()
            return False, (
                f"🔒 Host Key Not Verified: Server '{self.config.host}' is not present in ~/.ssh/known_hosts.\n\n"
                f"Key Type: {e.key_type}\n"
                f"Fingerprint: {e.fingerprint}\n\n"
                f"To protect against Man-in-the-Middle (MITM) attacks, SmartLinux does not auto-trust unknown hosts. "
                f"You must verify this fingerprint before trusting it."
            )
        except paramiko.AuthenticationException:
            self.disconnect()
            return False, "SSH Authentication Error: Invalid username, password, or key."
        except paramiko.SSHException as e:
            self.disconnect()
            return False, f"SSH Error: {str(e)}"
        except Exception as e:
            self.disconnect()
            return False, f"Failed to connect to {self.config.host}:{self.config.port}: {str(e)}"

    def disconnect(self):
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
        try:
            _, out, _ = self._raw_exec("uname -s")
            self._remote_os = out.strip()
        except Exception:
            self._remote_os = "Linux"

    def _raw_exec(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
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
        cmd = f"smartctl {args}"
        
        if self.config.username != "root" and self.config.password:
            code, out, err = self._exec_with_sudo(cmd, timeout=timeout)
            json_data = extract_json_object(out)
            if json_data and not is_permission_denied_json(json_data):
                return code, out, err

        code, out, err = self._raw_exec(cmd, timeout=timeout)
        json_data = extract_json_object(out)
        if json_data and not is_permission_denied_json(json_data):
            return code, out, err

        if self.config.password or self.config.username != "root":
            code_s, out_s, err_s = self._exec_with_sudo(cmd, timeout=timeout)
            return code_s, out_s, err_s

        return code, out, err

    def run_self_test(self, device_path: str, test_type: str = "short") -> Tuple[bool, str]:
        """Triggers the drive's own native self-test (smartctl -t short|long) remotely.

        Only queues the test; does not wait for completion. Results show up in a
        later scan under the SMART self-test log.
        """
        mode = "long" if test_type == "long" else "short"
        code, out, err = self.exec_smartctl(f"-t {mode} {device_path}", timeout=15)
        output = (out or "") + (err or "")
        if code == 0 or "test has begun" in output.lower():
            return True, output.strip() or f"Self-test ({mode}) started."
        return False, output.strip() or f"smartctl exited with code {code}"

    def discover_remote_drives(self) -> List[DiskInfo]:
        drives: List[DiskInfo] = []
        is_darwin = (self._remote_os == "Darwin")

        code, out, err = self.exec_smartctl("--scan -j")
        json_scan = extract_json_object(out)
        if json_scan and "devices" in json_scan:
            for dev in json_scan.get("devices", []):
                dev_name = dev.get("name", "")
                if not dev_name:
                    continue
                if is_darwin and any(x in dev_name for x in ["diskimage", "synthesized"]):
                    continue
                if any(x in dev_name for x in ["/loop", "/ram", "/zram", "/dm-", "/sr"]):
                    continue

                protocol = dev.get("protocol", "SATA").upper()
                is_usb = ("USB" in protocol or "usb" in dev_name)

                disk = DiskInfo(
                    device_path=dev_name,
                    name=dev_name.split("/")[-1],
                    model="Physical Drive",
                    protocol=protocol,
                    is_remote=True,
                    is_usb=is_usb,
                    server_id=self.config.id,
                    server_name=self.config.name
                )
                drives.append(disk)

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

                    model = dev.get("model") or "Physical Drive"
                    size_str = dev.get("size") or "Unknown"
                    tran = (dev.get("tran") or "SATA").upper()

                    disk = DiskInfo(
                        device_path=dev_path,
                        name=dev_name,
                        model=model.strip(),
                        size_human=size_str,
                        protocol=tran if tran else "ATA/SATA",
                        is_remote=True,
                        is_usb=(tran == "USB"),
                        server_id=self.config.id,
                        server_name=self.config.name
                    )
                    drives.append(disk)

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
                            model="Mac Physical Drive",
                            is_remote=True,
                            server_id=self.config.id,
                            server_name=self.config.name
                        )
                        drives.append(disk)

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
                        model="Physical Drive",
                        is_remote=True,
                        server_id=self.config.id,
                        server_name=self.config.name
                    )
                    drives.append(disk)

        return drives

    def read_remote_drive_smart(self, device_path: str) -> DiskInfo:
        device_types = [None, "scsi", "sat,auto", "sntrealtek", "sntjmicron", "sntasmedia", "usbjmicron", "usbsunplus"]
        best_disk = None
        last_error = ""
        last_json = None
        last_code = 0

        for dev_type in device_types:
            opt = f"-d {dev_type} " if dev_type else ""
            code, out, err = self.exec_smartctl(f"-j {opt}-a {device_path}")
            last_code = code
            json_data = extract_json_object(out)
            if json_data:
                last_json = json_data
                if not is_permission_denied_json(json_data):
                    has_smart = (
                        "smart_status" in json_data or 
                        "ata_smart_attributes" in json_data or 
                        "nvme_smart_health_information_log" in json_data
                    )
                    has_identity = (
                        "model_name" in json_data or 
                        "scsi_product" in json_data or 
                        "user_capacity" in json_data or
                        "serial_number" in json_data
                    )
                    if has_smart or has_identity:
                        try:
                            disk = SmartParser.parse_smart_json(json_data, device_path)
                            disk.is_remote = True
                            disk.server_id = self.config.id
                            disk.server_name = self.config.name
                            if disk.health_status != HealthStatus.UNKNOWN and disk.size_bytes > 0:
                                return disk
                            if best_disk is None or (disk.size_bytes > 0 and best_disk.size_bytes == 0):
                                best_disk = disk
                        except Exception as e:
                            print(f"Error parsing SMART JSON: {e}")
            if err:
                last_error = err.strip()

        if best_disk and (best_disk.size_bytes > 0 or best_disk.model != "Generic Drive"):
            return best_disk

        error_detail = last_error
        if last_json and is_permission_denied_json(last_json):
            error_detail = "Permission denied: administrator / sudo privileges required."
        elif "not found" in error_detail.lower():
            if self._remote_os == "Darwin":
                error_detail = "smartctl not found. Install on macOS with: brew install smartmontools"
            else:
                error_detail = "smartctl not found. Install 'smartmontools' on the remote host."
        elif "permission denied" in error_detail.lower() or "operation not permitted" in error_detail.lower():
            error_detail = "Permission denied accessing block device. Check sudo or SUID permissions."
        elif not error_detail:
            error_detail = f"No valid SMART response from device (exit code {last_code})"

        disk = DiskInfo(
            device_path=device_path,
            name=device_path.split("/")[-1],
            is_remote=True,
            server_id=self.config.id,
            server_name=self.config.name,
            health_status=HealthStatus.FAILED,
            health_summary="SMART read failure",
            error_message=error_detail
        )
        return disk

import os
import re
from typing import Optional, Tuple, Dict, Any
import paramiko
from .models import ServerConfig
from .remote_common import (  # noqa: F401  (re-exported for backward compatibility)
    BaseRemoteClient, UNIVERSAL_PATH, extract_json_object, is_permission_denied_json,
)
from .plink_client import (
    PlinkSSHClient, PlinkHostKey, PLINK_INSTALL_HINT, find_plink,
)


# paramiko messages meaning "this device only speaks algorithms we cannot negotiate"
# (typically ssh-dss host keys, old kex/ciphers/MACs): the cue to retry with plink.
_LEGACY_NEGOTIATION_RE = re.compile(
    r"no matching host key type|incompatible ssh (peer|server)|no acceptable (host key|kex|cipher|mac)"
    r"|unable to (negotiate|agree)|no matching (key exchange|cipher|mac|kex)|negotiation failed",
    re.I,
)


def is_legacy_negotiation_error(message: str) -> bool:
    return bool(_LEGACY_NEGOTIATION_RE.search(message or ""))


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


class RemoteSSHClient(BaseRemoteClient):
    """
    Generic, platform-agnostic SSH client for monitoring storage diagnostics
    across any Linux distribution, macOS (Darwin), or BSD host.
    """

    def __init__(self, config: ServerConfig):
        super().__init__(config)
        self._client: Optional[paramiko.SSHClient] = None
        self.legacy_negotiation_failure = False
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
        self.legacy_negotiation_failure = False
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
            self.legacy_negotiation_failure = is_legacy_negotiation_error(str(e))
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


def host_key_fingerprint(key) -> str:
    """Human-readable fingerprint of a paramiko PKey or a PlinkHostKey."""
    if isinstance(key, PlinkHostKey):
        return key.fingerprint
    return ":".join(f"{b:02x}" for b in key.get_fingerprint())


def trust_host_key(host: str, key) -> bool:
    """Persists a host key the user explicitly confirmed, whichever engine reported it."""
    if isinstance(key, PlinkHostKey):
        return PlinkSSHClient.trust_host_key(key)
    return RemoteSSHClient.trust_and_save_host_key(host, key)


def known_hosts_label(key) -> str:
    """Where a trusted key of this kind ends up (for confirmation dialogs)."""
    if isinstance(key, PlinkHostKey):
        return "SmartLinux's trusted legacy host keys (~/.config/smartlinux/legacy_hostkeys.json)"
    return "~/.ssh/known_hosts"


class AutoSSHClient:
    """Picks the SSH engine per device and exposes the one public interface the UI uses.

    - ``config.legacy_ssh`` set: plink straight away ("Legacy SSH (old devices)").
    - otherwise paramiko first; if the device cannot be negotiated (no matching host key
      type, incompatible peer, algorithm mismatch) the connection is retried with plink.
    The engine that worked is remembered for later reconnects of this client.
    """

    def __init__(self, config: ServerConfig):
        self.config = config
        self._engine: Optional[BaseRemoteClient] = None
        self._use_plink = bool(config.legacy_ssh)

    @property
    def engine_name(self) -> str:
        return "plink" if isinstance(self._engine, PlinkSSHClient) else "paramiko"

    @property
    def last_untrusted_host_key(self):
        return getattr(self._engine, "last_untrusted_host_key", None)

    @staticmethod
    def _legacy_unavailable_message(reason: str) -> str:
        return (
            f"{reason}\n\nThis device only supports legacy SSH algorithms that the built-in SSH "
            f"library cannot negotiate, and PuTTY's plink is not installed.\n\n{PLINK_INSTALL_HINT}"
        )

    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        if self._engine:
            self._engine.disconnect()

        if not self._use_plink:
            self._engine = RemoteSSHClient(self.config)
            ok, msg = self._engine.connect(timeout=timeout)
            if ok:
                return True, msg
            if not (self._engine.legacy_negotiation_failure or is_legacy_negotiation_error(msg)):
                return False, msg
            if not find_plink():
                return False, self._legacy_unavailable_message(msg)
            self._use_plink = True

        self._engine = PlinkSSHClient(self.config)
        return self._engine.connect(timeout=timeout)

    def disconnect(self):
        if self._engine:
            self._engine.disconnect()

    def is_connected(self) -> bool:
        return bool(self._engine and self._engine.is_connected())

    def _ensure_engine(self) -> BaseRemoteClient:
        if self._engine is None:
            self._engine = PlinkSSHClient(self.config) if self._use_plink else RemoteSSHClient(self.config)
        return self._engine

    def exec_smartctl(self, args: str, timeout: int = 15):
        return self._ensure_engine().exec_smartctl(args, timeout=timeout)

    def run_self_test(self, device_path: str, test_type: str = "short"):
        return self._ensure_engine().run_self_test(device_path, test_type)

    def discover_remote_drives(self):
        return self._ensure_engine().discover_remote_drives()

    def read_remote_drive_smart(self, device_path: str):
        return self._ensure_engine().read_remote_drive_smart(device_path)


def create_ssh_client(config: ServerConfig) -> AutoSSHClient:
    return AutoSSHClient(config)

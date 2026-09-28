import json
import os
import shutil
import subprocess
import uuid
from typing import List, Optional
from .models import ServerConfig

SERVICE_NAME = "smartlinux"


class KeyringHelper:
    """
    Manages secure credential storage using system keyring.
    Supports Python 'keyring' module and falls back to Linux 'secret-tool'
    (FreeDesktop SecretService / gnome-keyring).
    """

    @staticmethod
    def is_available() -> bool:
        try:
            import keyring
            return True
        except ImportError:
            pass
        return shutil.which("secret-tool") is not None

    @staticmethod
    def get_password(server_id: str) -> Optional[str]:
        if not server_id:
            return None

        # 1. Try python keyring module
        try:
            import keyring
            pw = keyring.get_password(SERVICE_NAME, server_id)
            if pw is not None:
                return pw
        except Exception:
            pass

        # 2. Try secret-tool CLI fallback (gnome-keyring / org.freedesktop.secrets)
        if shutil.which("secret-tool"):
            try:
                proc = subprocess.run(
                    ["secret-tool", "lookup", "service", SERVICE_NAME, "server_id", server_id],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=5
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout.rstrip("\r\n")
            except Exception:
                pass

        return None

    @staticmethod
    def set_password(server_id: str, password: str) -> bool:
        if not server_id or password is None:
            return False

        # 1. Try python keyring module
        try:
            import keyring
            keyring.set_password(SERVICE_NAME, server_id, password)
            return True
        except Exception:
            pass

        # 2. Try secret-tool CLI fallback
        if shutil.which("secret-tool"):
            try:
                proc = subprocess.Popen(
                    [
                        "secret-tool", "store",
                        f"--label=SmartLinux SSH Server ({server_id[:8]})",
                        "service", SERVICE_NAME,
                        "server_id", server_id
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
                proc.communicate(input=password + "\n", timeout=5)
                if proc.returncode == 0:
                    return True
            except Exception:
                pass

        return False

    @staticmethod
    def delete_password(server_id: str) -> bool:
        if not server_id:
            return False

        # 1. Try python keyring module
        try:
            import keyring
            keyring.delete_password(SERVICE_NAME, server_id)
            return True
        except Exception:
            pass

        # 2. Try secret-tool CLI fallback
        if shutil.which("secret-tool"):
            try:
                proc = subprocess.run(
                    ["secret-tool", "clear", "service", SERVICE_NAME, "server_id", server_id],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=5
                )
                if proc.returncode == 0:
                    return True
            except Exception:
                pass

        return False


class ConfigManager:
    """Manages persistence of Homelab server configurations in ~/.config/smartlinux/."""

    CONFIG_DIR = os.path.expanduser("~/.config/smartlinux")
    SERVERS_FILE = os.path.join(CONFIG_DIR, "servers.json")

    @classmethod
    def _ensure_dir(cls):
        if not os.path.exists(cls.CONFIG_DIR):
            os.makedirs(cls.CONFIG_DIR, mode=0o700, exist_ok=True)
        try:
            os.chmod(cls.CONFIG_DIR, 0o700)
        except Exception:
            pass

    @classmethod
    def _enforce_file_permissions(cls):
        if os.path.exists(cls.SERVERS_FILE):
            try:
                os.chmod(cls.SERVERS_FILE, 0o600)
            except Exception:
                pass

    @classmethod
    def load_servers(cls) -> List[ServerConfig]:
        """Loads list of configured remote servers, retrieving passwords from keyring if present."""
        cls._ensure_dir()
        if not os.path.exists(cls.SERVERS_FILE):
            return []

        cls._enforce_file_permissions()

        servers: List[ServerConfig] = []
        needs_migration_rewrite = False

        try:
            with open(cls.SERVERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for item in data:
                srv = ServerConfig.from_dict(item)
                # Check if password is in file (legacy plaintext)
                if srv.password:
                    # Attempt migration to system keyring
                    if KeyringHelper.set_password(srv.id, srv.password):
                        needs_migration_rewrite = True
                else:
                    # Password is None in file, retrieve from keyring
                    keyring_pw = KeyringHelper.get_password(srv.id)
                    if keyring_pw:
                        srv.password = keyring_pw

                servers.append(srv)

            # If plaintext passwords were found and migrated, rewrite servers.json securely
            if needs_migration_rewrite:
                cls.save_servers(servers)

            return servers
        except Exception as e:
            print(f"Error loading servers config: {e}")
            return []

    @classmethod
    def save_servers(cls, servers: List[ServerConfig]) -> bool:
        """
        Saves list of remote servers to JSON with 0600 permissions.
        Passwords are saved into system keyring and omitted/nulled in servers.json whenever possible.
        """
        cls._ensure_dir()
        data = []
        for s in servers:
            s_dict = s.to_dict()
            if s.password:
                stored = KeyringHelper.set_password(s.id, s.password)
                if stored:
                    # Successfully in keyring, do not expose plaintext in json
                    s_dict["password"] = None
                else:
                    # Keyring not available, keep password protected by 0600 file permissions
                    s_dict["password"] = s.password
            else:
                s_dict["password"] = None
            data.append(s_dict)

        temp_file = os.path.join(cls.CONFIG_DIR, f"servers.json.tmp.{uuid.uuid4().hex[:8]}")
        try:
            # Atomic creation with 0600 permissions
            fd = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, cls.SERVERS_FILE)
            cls._enforce_file_permissions()
            return True
        except Exception as e:
            print(f"Error saving servers config: {e}")
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            return False

    @classmethod
    def add_server(cls, server: ServerConfig) -> List[ServerConfig]:
        if not server.id:
            server.id = str(uuid.uuid4())
        servers = cls.load_servers()
        # Replace if exists, else append
        existing_idx = next((i for i, s in enumerate(servers) if s.id == server.id), None)
        if existing_idx is not None:
            servers[existing_idx] = server
        else:
            servers.append(server)
        cls.save_servers(servers)
        return servers

    @classmethod
    def remove_server(cls, server_id: str) -> List[ServerConfig]:
        servers = cls.load_servers()
        servers = [s for s in servers if s.id != server_id]
        KeyringHelper.delete_password(server_id)
        cls.save_servers(servers)
        return servers

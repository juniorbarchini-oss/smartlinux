import json
import os
import uuid
from typing import List, Optional
from .models import ServerConfig


class ConfigManager:
    """Manages persistence of Homelab server configurations in ~/.config/smartlinux/."""

    CONFIG_DIR = os.path.expanduser("~/.config/smartlinux")
    SERVERS_FILE = os.path.join(CONFIG_DIR, "servers.json")

    @classmethod
    def _ensure_dir(cls):
        if not os.path.exists(cls.CONFIG_DIR):
            os.makedirs(cls.CONFIG_DIR, exist_ok=True)

    @classmethod
    def load_servers(cls) -> List[ServerConfig]:
        """Loads list of configured remote servers."""
        cls._ensure_dir()
        if not os.path.exists(cls.SERVERS_FILE):
            return []

        try:
            with open(cls.SERVERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return [ServerConfig.from_dict(item) for item in data]
        except Exception as e:
            print(f"Error loading servers config: {e}")
            return []

    @classmethod
    def save_servers(cls, servers: List[ServerConfig]) -> bool:
        """Saves list of remote servers to JSON."""
        cls._ensure_dir()
        try:
            with open(cls.SERVERS_FILE, "w", encoding="utf-8") as f:
                data = [s.to_dict() for s in servers]
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving servers config: {e}")
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
        cls.save_servers(servers)
        return servers

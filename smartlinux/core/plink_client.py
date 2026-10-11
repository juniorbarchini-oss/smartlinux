"""SSH engine backed by PuTTY's ``plink`` binary, for devices paramiko cannot talk to.

Old NAS firmware (e.g. WD My Cloud EX4, OpenSSH 7.5) only offers ``ssh-dss`` host keys,
which paramiko >= 4 and OpenSSH >= 9.8 removed. PuTTY still negotiates them.

Security model
- Passwords reach plink only through ``-pwfile`` (a 0600 temp file that exists just for the
  lifetime of one plink process); never ``-pw`` on the command line, where any local user
  could read it from the process list.
- ``-batch`` is always used, so plink can never stop to ask anything.
- Host keys are never trusted blindly. plink checks ~/.putty/sshhostkeys itself. For a key
  the user explicitly confirmed in SmartLinux's fingerprint dialog, the fingerprint is
  pinned in ~/.config/smartlinux/legacy_hostkeys.json and enforced with ``-hostkey``.
  A different key than the pinned/cached one aborts the connection with a MITM alert.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

from .remote_common import BaseRemoteClient, UNIVERSAL_PATH

PLINK_INSTALL_HINT = (
    "Install PuTTY's command-line client (version 0.75 or newer):\n"
    "  Debian/Ubuntu:  sudo apt install putty-tools\n"
    "  Arch/Manjaro:   sudo pacman -S putty\n"
    "  Fedora:         sudo dnf install putty\n"
    "  macOS:          brew install putty"
)

_FINGERPRINT_RE = re.compile(
    r"(?P<type>ssh-[a-z0-9]+|ecdsa-sha2-\S+|rsa2|dsa|ed25519|ecdsa)\s+(?P<bits>\d+)\s+"
    r"(?P<fp>SHA256:[A-Za-z0-9+/=]+|(?:[0-9a-fA-F]{2}:){15}[0-9a-fA-F]{2})"
)
_NOT_TRUSTED_MARKERS = (
    "host key is not cached",
    "host key not in manually configured list",
    "cannot confirm a host key in batch mode",
    "not cached for this server",
)


def find_plink() -> Optional[str]:
    """Path of the plink binary (``SMARTLINUX_PLINK`` overrides PATH lookup), or None."""
    override = os.environ.get("SMARTLINUX_PLINK")
    if override:
        return override if os.access(override, os.X_OK) else None
    return shutil.which("plink")


class PlinkHostKey:
    """Host key as reported by plink: only its type and fingerprint are known."""

    def __init__(self, host: str, port: int, key_type: str, bits: str, fingerprint: str):
        self.host = host
        self.port = port
        self.key_type = key_type
        self.bits = bits
        self.fingerprint = fingerprint

    def get_name(self) -> str:
        return f"{self.key_type} {self.bits}".strip()


def parse_plink_fingerprint(output: str) -> Optional[Tuple[str, str, str]]:
    """(key_type, bits, fingerprint) from plink's host key warning text, or None."""
    m = _FINGERPRINT_RE.search(output or "")
    return (m.group("type"), m.group("bits"), m.group("fp")) if m else None


class LegacyHostKeyStore:
    """Fingerprints the user explicitly confirmed for plink connections (0600 JSON)."""

    @staticmethod
    def path() -> str:
        return os.path.join(os.path.expanduser("~/.config/smartlinux"), "legacy_hostkeys.json")

    @classmethod
    def _load(cls) -> Dict[str, Dict[str, str]]:
        try:
            with open(cls.path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _key(host: str, port: int) -> str:
        return f"{host}:{port}"

    @classmethod
    def get(cls, host: str, port: int) -> Optional[str]:
        entry = cls._load().get(cls._key(host, port))
        return entry.get("fingerprint") if entry else None

    @classmethod
    def save(cls, key: PlinkHostKey) -> bool:
        data = cls._load()
        data[cls._key(key.host, key.port)] = {"type": key.get_name(), "fingerprint": key.fingerprint}
        path = cls.path()
        try:
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            os.chmod(path, 0o600)
            return True
        except Exception as e:
            print(f"Error saving legacy host key: {e}")
            return False


class PlinkSSHClient(BaseRemoteClient):
    """Same public interface as RemoteSSHClient, but every operation runs ``plink``.

    plink has no persistent session, so "connected" means "the last authentication check
    succeeded"; each command opens its own short-lived plink process.
    """

    def __init__(self, config):
        super().__init__(config)
        self._connected = False
        self.last_untrusted_host_key: Optional[Tuple[str, PlinkHostKey]] = None

    # ---- connection -----------------------------------------------------------------
    def connect(self, timeout: int = 8) -> Tuple[bool, str]:
        self.disconnect()
        self.last_untrusted_host_key = None

        plink = find_plink()
        if not plink:
            return False, f"plink (PuTTY) is not installed, but it is required for legacy SSH devices.\n\n{PLINK_INSTALL_HINT}"
        if self.config.host.startswith("-") or self.config.username.startswith("-"):
            return False, "Invalid host or username."

        code, out, err = self._run_plink("uname -s", timeout=timeout, plink=plink)
        if code == 0:
            self._connected = True
            self._remote_os = out.strip().splitlines()[0] if out.strip() else "Linux"
            return True, "Connected successfully (legacy SSH via plink)"
        return False, self._explain_failure(code, out + "\n" + err)

    def disconnect(self):
        self._connected = False
        self._remote_os = None

    def is_connected(self) -> bool:
        return self._connected

    # ---- plink invocation -------------------------------------------------------------
    def _build_args(self, plink: str, pwfile: Optional[str]) -> List[str]:
        cfg = self.config
        args = [plink, "-ssh", "-batch", "-P", str(cfg.port), "-l", cfg.username]
        if cfg.auth_type == "key":
            key_path = os.path.expanduser(cfg.key_path or "~/.ssh/id_rsa")
            if os.path.exists(key_path):
                args += ["-i", key_path]
        elif pwfile:
            args += ["-pwfile", pwfile, "-noagent"]
        pinned = LegacyHostKeyStore.get(cfg.host, cfg.port)
        if pinned:
            args += ["-hostkey", pinned]
        args.append(cfg.host)
        return args

    @staticmethod
    def _write_pwfile(password: str) -> str:
        base = os.environ.get("XDG_RUNTIME_DIR")
        fd, path = tempfile.mkstemp(
            prefix="smartlinux-pw-", dir=base if base and os.path.isdir(base) else None
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(password + "\n")
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        return path

    def _run_plink(self, command: str, timeout: int = 15, stdin_data: Optional[str] = None,
                   plink: Optional[str] = None) -> Tuple[int, str, str]:
        plink = plink or find_plink()
        if not plink:
            return -1, "", "plink not installed"

        pwfile = None
        try:
            if self.config.auth_type != "key" and self.config.password:
                pwfile = self._write_pwfile(self.config.password)
            argv = self._build_args(plink, pwfile) + [UNIVERSAL_PATH + command]
            proc = subprocess.run(
                argv,
                input=stdin_data if stdin_data is not None else "",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout + 5,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Timed out after {timeout}s waiting for {self.config.host}"
        except Exception as e:
            return -1, "", str(e)
        finally:
            # The secret only has to exist while plink authenticates
            if pwfile:
                try:
                    os.remove(pwfile)
                except OSError:
                    pass

    def _explain_failure(self, code: int, output: str) -> str:
        cfg = self.config
        low = output.lower()

        fp = parse_plink_fingerprint(output)
        pinned = LegacyHostKeyStore.get(cfg.host, cfg.port)
        host_key_problem = (
            "potential security breach" in low
            or "does not match" in low
            or "host key did not match" in low
            or any(m in low for m in _NOT_TRUSTED_MARKERS)
            or (pinned is not None and "host key" in low)
        )

        if host_key_problem and fp:
            key_type, bits, fingerprint = fp
            key = PlinkHostKey(cfg.host, cfg.port, key_type, bits, fingerprint)
            mismatch = (
                "potential security breach" in low
                or "does not match" in low
                or (pinned is not None and pinned != fingerprint)
            )
            if mismatch:
                return (
                    f"🚨 SECURITY ALERT: Host Key Mismatch for '{cfg.host}'!\n\n"
                    f"The host key presented by the remote server DOES NOT match the recorded key "
                    f"(~/.putty/sshhostkeys or SmartLinux's trusted legacy keys).\n\n"
                    f"Server: {cfg.host}:{cfg.port}\n"
                    f"Key Type: {key.get_name()}\n"
                    f"Fingerprint: {fingerprint}\n\n"
                    f"This may indicate a Man-in-the-Middle (MITM) attack or the server host keys have changed.\n"
                    f"SmartLinux has aborted the connection to protect your data."
                )
            self.last_untrusted_host_key = (cfg.host, key)
            return (
                f"🔒 Host Key Not Verified: Server '{cfg.host}' is not present in the trusted host keys.\n\n"
                f"Key Type: {key.get_name()}\n"
                f"Fingerprint: {fingerprint}\n\n"
                f"To protect against Man-in-the-Middle (MITM) attacks, SmartLinux does not auto-trust unknown hosts. "
                f"You must verify this fingerprint before trusting it."
            )

        if any(t in low for t in ("access denied", "authentication failed", "unable to authenticate",
                                  "no supported authentication methods", "disconnected: no more authentication")):
            return "SSH Authentication Error: Invalid username, password, or key (legacy SSH via plink)."
        if "-pwfile" in low and ("unknown option" in low or "unrecognised" in low or "unrecognized" in low):
            return "Your plink is too old (no -pwfile support). Please upgrade PuTTY to 0.75 or newer.\n\n" + PLINK_INSTALL_HINT
        if "network error" in low or "connection refused" in low or "timed out" in low:
            detail = next((ln.strip() for ln in output.splitlines() if ln.strip()), "network error")
            return f"Failed to connect to {cfg.host}:{cfg.port}: {detail}"
        detail = next((ln.strip() for ln in output.splitlines() if ln.strip()), f"plink exited with code {code}")
        return f"SSH Error (legacy via plink): {detail}"

    # ---- BaseRemoteClient transport hooks ----------------------------------------------
    def _ensure_connected(self) -> Optional[str]:
        if not self.is_connected():
            ok, msg = self.connect()
            if not ok:
                return msg
        return None

    def _raw_exec(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        failure = self._ensure_connected()
        if failure:
            return -1, "", failure
        return self._run_plink(cmd, timeout=timeout)

    def _exec_with_sudo(self, cmd: str, timeout: int = 15) -> Tuple[int, str, str]:
        failure = self._ensure_connected()
        if failure:
            return -1, "", failure
        stdin = (self.config.password + "\n") if self.config.password else None
        return self._run_plink(f"sudo -S -p '' {cmd}", timeout=timeout, stdin_data=stdin)

    # ---- host key trust ---------------------------------------------------------------
    @staticmethod
    def trust_host_key(key: PlinkHostKey) -> bool:
        return LegacyHostKeyStore.save(key)

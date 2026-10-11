"""Opt-in "is there a newer release?" check.

Privacy contract (also documented in the README):
- Nothing is contacted unless the user turned the check on, or clicked "Check for updates now".
- One anonymous HTTPS GET to the public GitHub API for this project's latest release.
  The only things sent are the request itself and ``User-Agent: SmartLinux/<version>``.
  No identifiers, no disk or server data.
- The reply is only used to show a version number and a link; nothing is downloaded or installed.
"""
import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.request import Request, urlopen

from .. import __version__

REPO = "juniorbarchini-oss/smartlinux"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
CHECK_INTERVAL_SECONDS = 24 * 3600


class UpdateCheckError(Exception):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    tag: str
    version: str
    url: str


def parse_version(text: Optional[str]) -> Optional[Tuple[int, ...]]:
    match = re.match(r"^\s*v?(\d+(?:\.\d+)*)", text or "")
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(latest: Optional[str], current: Optional[str]) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)) > b + (0,) * (size - len(b))


def should_auto_check(setting: str, last_check: float, now: float) -> bool:
    return setting == "on" and (now - float(last_check or 0)) >= CHECK_INTERVAL_SECONDS


def fetch_latest_release(timeout: float = 5.0, opener=urlopen) -> ReleaseInfo:
    request = Request(
        LATEST_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"SmartLinux/{__version__}"},
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise UpdateCheckError(f"Could not reach GitHub: {exc}") from exc

    tag = str(payload.get("tag_name", "")) if isinstance(payload, dict) else ""
    version = parse_version(tag)
    if not version:
        raise UpdateCheckError("Unexpected reply from GitHub (no release version).")

    url = str(payload.get("html_url", ""))
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = RELEASES_PAGE
    return ReleaseInfo(tag=tag, version=".".join(str(p) for p in version), url=url)

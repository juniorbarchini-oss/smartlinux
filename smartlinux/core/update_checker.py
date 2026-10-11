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


_PRE_RE = re.compile(
    r"^\s*v?(\d+(?:\.\d+)*)(?:[-.~_]?(alpha|a|beta|b|rc|c|preview|pre)[-.~_]?(\d+)?)?\s*$", re.I
)
# alpha < beta < rc < final release
_STAGES = {"alpha": -3, "a": -3, "beta": -2, "b": -2, "pre": -1, "preview": -1, "rc": -1, "c": -1}


def version_key(text: Optional[str]) -> Optional[Tuple[Tuple[int, ...], int, int]]:
    """Sortable key: (release numbers, stage, serial). A final release has stage 0, so
    1.3.0 > 1.3.0-rc.1 > 1.3.0-beta.2 > 1.3.0-beta.1 > 1.2.9."""
    match = _PRE_RE.match(text or "")
    if match:
        release = tuple(int(part) for part in match.group(1).split("."))
        if match.group(2):
            return release, _STAGES[match.group(2).lower()], int(match.group(3) or 0)
        return release, 0, 0
    plain = parse_version(text)          # tolerate trailing text we do not understand
    return (plain, 0, 0) if plain else None


def is_newer(latest: Optional[str], current: Optional[str]) -> bool:
    a, b = version_key(latest), version_key(current)
    if a is None or b is None:
        return False
    size = max(len(a[0]), len(b[0]))
    pad = lambda release: release + (0,) * (size - len(release))
    return (pad(a[0]), a[1], a[2]) > (pad(b[0]), b[1], b[2])


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

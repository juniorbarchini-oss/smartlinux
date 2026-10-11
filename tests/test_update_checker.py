"""Opt-in update check: version logic, privacy of the request, and the settings file."""
import json
import os
import re
import stat

import pytest

import smartlinux
from smartlinux.core import update_checker as uc
from smartlinux.core.config_manager import AppSettings, ConfigManager
from smartlinux.core.update_checker import (
    CHECK_INTERVAL_SECONDS, LATEST_URL, RELEASES_PAGE, UpdateCheckError,
    fetch_latest_release, is_newer, parse_version, should_auto_check,
)

REPO_PREFIX = "https://github.com/juniorbarchini-oss/smartlinux/"


# --- versions ---------------------------------------------------------------

def test_parse_version_variants():
    assert parse_version("v1.2.0") == (1, 2, 0)
    assert parse_version("1.10.0") == (1, 10, 0)
    assert parse_version("  v2") == (2,)
    assert parse_version(None) is None
    assert parse_version("") is None
    assert parse_version("garbage") is None


@pytest.mark.parametrize("latest,current,expected", [
    ("v1.2.0", "1.1.0", True),
    ("1.1.0", "1.1.0", False),
    ("v1.1.0", "1.1.0", False),
    ("1.0.0", "1.1.0", False),
    ("1.10.0", "1.9.0", True),
    ("1.9.0", "1.10.0", False),
    ("1.2", "1.2.0", False),
    ("1.2.0", "1.2", False),
    ("1.2.1", "1.2", True),
    ("garbage", "1.1.0", False),
    ("1.2.0", "garbage", False),
    (None, "1.1.0", False),
    ("1.2.0", None, False),
    (None, None, False),
])
def test_is_newer(latest, current, expected):
    assert is_newer(latest, current) is expected


# --- scheduling -------------------------------------------------------------

DAY = CHECK_INTERVAL_SECONDS


def test_interval_is_24h():
    assert DAY == 24 * 3600


@pytest.mark.parametrize("setting", ["off", "unset", "", "ON", "yes"])
def test_auto_check_never_unless_on(setting):
    assert should_auto_check(setting, 0, 10 ** 10) is False


def test_auto_check_on_with_no_previous_check():
    assert should_auto_check("on", 0, 10 ** 10) is True


def test_auto_check_on_within_24h():
    now = 1_000_000.0
    assert should_auto_check("on", now - DAY + 1, now) is False
    assert should_auto_check("on", now - 60, now) is False


def test_auto_check_on_exactly_24h_and_after():
    now = 1_000_000.0
    assert should_auto_check("on", now - DAY, now) is True
    assert should_auto_check("on", now - DAY - 1, now) is True


# --- fetch_latest_release ---------------------------------------------------

class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def make_opener(payload, calls=None):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def opener(request, timeout=None):
        if calls is not None:
            calls.append((request, timeout))
        return _Resp(body)
    return opener


def test_fetch_normal_reply_and_request_is_anonymous():
    calls = []
    payload = {"tag_name": "v1.3.0", "html_url": REPO_PREFIX + "releases/tag/v1.3.0"}
    info = fetch_latest_release(timeout=3, opener=make_opener(payload, calls))

    assert info.tag == "v1.3.0"
    assert info.version == "1.3.0"
    assert info.url == REPO_PREFIX + "releases/tag/v1.3.0"

    assert len(calls) == 1
    request, timeout = calls[0]
    assert timeout == 3
    assert request.full_url == LATEST_URL
    assert request.get_method() == "GET"
    assert request.data is None
    # urllib normalises header names with str.capitalize()
    headers = {k.lower(): v for k, v in request.header_items()}
    assert headers["user-agent"] == f"SmartLinux/{smartlinux.__version__}"
    assert set(headers) <= {"user-agent", "accept"}


@pytest.mark.parametrize("url", [
    "https://evil.example.com/smartlinux/",
    "http://github.com/juniorbarchini-oss/smartlinux/releases",
    "https://github.com/someone-else/smartlinux/releases",
    "https://github.com/juniorbarchini-oss/smartlinux-evil/x",
    "",
])
def test_fetch_foreign_html_url_replaced_by_releases_page(url):
    info = fetch_latest_release(opener=make_opener({"tag_name": "v1.3.0", "html_url": url}))
    assert info.url == RELEASES_PAGE


def test_fetch_missing_html_url_uses_releases_page():
    info = fetch_latest_release(opener=make_opener({"tag_name": "1.3.0"}))
    assert info.url == RELEASES_PAGE
    assert info.version == "1.3.0"


@pytest.mark.parametrize("payload", [
    {},
    {"tag_name": ""},
    {"tag_name": "nightly"},
    {"tag_name": None},
    {"tag_name": 123456789},  # numeric is stringified, still must not crash
])
def test_fetch_bad_tag_name(payload):
    opener = make_opener(payload)
    if payload.get("tag_name") == 123456789:
        # a bare number is a syntactically valid version; just must not raise oddly
        assert fetch_latest_release(opener=opener).version == "123456789"
        return
    with pytest.raises(UpdateCheckError):
        fetch_latest_release(opener=opener)


@pytest.mark.parametrize("payload", [[], ["v1.0.0"], "v1.0.0", 5, None])
def test_fetch_non_dict_json(payload):
    with pytest.raises(UpdateCheckError):
        fetch_latest_release(opener=make_opener(payload))


def test_fetch_invalid_json_body():
    with pytest.raises(UpdateCheckError):
        fetch_latest_release(opener=make_opener(b"<html>not json</html>"))


@pytest.mark.parametrize("exc", [OSError("no route"), TimeoutError("slow"), ValueError("boom")])
def test_fetch_network_exception(exc):
    def opener(request, timeout=None):
        raise exc
    with pytest.raises(UpdateCheckError):
        fetch_latest_release(opener=opener)


def test_module_constants_point_at_this_project():
    assert LATEST_URL.startswith("https://api.github.com/repos/juniorbarchini-oss/smartlinux/")
    assert RELEASES_PAGE.startswith(REPO_PREFIX)
    assert uc.__version__ == smartlinux.__version__


# --- AppSettings ------------------------------------------------------------

@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    monkeypatch.setattr(ConfigManager, "CONFIG_DIR", str(d))
    return d


def test_settings_roundtrip(settings_dir):
    assert AppSettings.set(update_check="on", update_last_check=123.5) is True
    assert AppSettings.get("update_check") == "on"
    assert AppSettings.get("update_last_check") == 123.5
    # a second set keeps earlier keys
    assert AppSettings.set(other=1) is True
    assert AppSettings.get("update_check") == "on"
    assert AppSettings.get("other") == 1


def test_settings_default_for_missing_key(settings_dir):
    assert AppSettings.get("nope") is None
    assert AppSettings.get("nope", "unset") == "unset"
    AppSettings.set(a=1)
    assert AppSettings.get("nope", "unset") == "unset"


def test_settings_file_mode_0600(settings_dir):
    AppSettings.set(update_check="off")
    path = settings_dir / "settings.json"
    assert path.is_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # no temp files left behind
    assert [p.name for p in settings_dir.iterdir()] == ["settings.json"]


def test_settings_corrupted_file_returns_default_and_set_recovers(settings_dir):
    settings_dir.mkdir(parents=True)
    path = settings_dir / "settings.json"
    path.write_text("{not valid json")
    assert AppSettings.get("update_check", "unset") == "unset"
    assert AppSettings.set(update_check="on") is True
    assert AppSettings.get("update_check") == "on"
    assert json.loads(path.read_text()) == {"update_check": "on"}


def test_settings_non_dict_json_treated_as_empty(settings_dir):
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text("[1, 2, 3]")
    assert AppSettings.get("x", "d") == "d"
    assert AppSettings.set(x=1) is True
    assert AppSettings.get("x") == 1


# --- version consistency guard ---------------------------------------------

def test_package_version_matches_pkgbuild():
    """__version__ is the git tag version; Arch's pkgver is the same without hyphens
    (pkgver may not contain them), derived from `_tag` in the PKGBUILD."""
    pkgbuild = os.path.join(os.path.dirname(__file__), "..", "packaging", "arch", "PKGBUILD")
    with open(pkgbuild, encoding="utf-8") as f:
        text = f.read()
    tag = re.search(r"^_tag=([^#\s]+)", text, re.MULTILINE)
    assert tag, "_tag not found in PKGBUILD"
    assert smartlinux.__version__ == tag.group(1).strip().strip("\"'")
    assert re.search(r"^pkgver=\$\{_tag//-/\}(\s|#|$)", text, re.MULTILINE), "pkgver must derive from _tag"


def test_pre_releases_sort_before_the_final_release():
    assert uc.is_newer("1.3.0", "1.3.0-beta.1")
    assert uc.is_newer("v1.3.0", "1.3.0b1")
    assert not uc.is_newer("1.3.0-beta.1", "1.3.0")
    assert uc.is_newer("1.3.0-beta.2", "1.3.0-beta.1")
    assert uc.is_newer("1.3.0-rc.1", "1.3.0-beta.9")
    assert uc.is_newer("1.3.0-beta.1", "1.2.9")
    assert not uc.is_newer("1.3.0-beta.1", "1.3.0-beta.1")
    assert uc.is_newer("1.3.1", "1.3.0-rc.2")
    assert not uc.is_newer("garbage", "1.3.0-beta.1")

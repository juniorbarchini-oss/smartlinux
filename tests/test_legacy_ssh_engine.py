"""plink engine + automatic engine selection, with no network and no real plink."""
import os
import stat
import subprocess
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from smartlinux.core import plink_client
from smartlinux.core.models import ServerConfig
from smartlinux.core.plink_client import (
    LegacyHostKeyStore, PlinkHostKey, PlinkSSHClient, parse_plink_fingerprint,
)
from smartlinux.core.ssh_client import (
    AutoSSHClient, RemoteSSHClient, host_key_fingerprint, is_legacy_negotiation_error,
    trust_host_key,
)

SHA = "SHA256:abcdEFGH0123456789abcdEFGH0123456789abcdEFG"
MD5 = "aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99"

UNCACHED = f"""The host key is not cached for this server:
  192.168.100.14 (port 22)
You have no guarantee that the server is the computer you
think it is.
The server's ssh-dss key fingerprint is:
  ssh-dss 1024 {SHA}
Connection abandoned.
"""

MISMATCH = f"""WARNING - POTENTIAL SECURITY BREACH!
The host key does not match the one PuTTY has cached for this server:
  192.168.100.14 (port 22)
The new ssh-dss key fingerprint is:
  ssh-dss 1024 {SHA}
Connection abandoned.
"""


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("SMARTLINUX_PLINK", raising=False)
    return tmp_path


def cfg(**kw):
    base = dict(id="nas", name="EX4", host="192.168.100.14", username="root",
                auth_type="password", password="s3cr3t pass")
    base.update(kw)
    return ServerConfig(**base)


def completed(code=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


# ---- plink invocation: password handling ------------------------------------------------

def test_password_goes_through_pwfile_never_pw(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        i = argv.index("-pwfile")
        path = argv[i + 1]
        seen["mode"] = stat.S_IMODE(os.stat(path).st_mode)
        seen["content"] = open(path).read()
        seen["path"] = path
        return completed(0, "Linux\n")

    monkeypatch.setattr(plink_client, "find_plink", lambda: "/usr/bin/plink")
    with patch("subprocess.run", side_effect=fake_run):
        ok, msg = PlinkSSHClient(cfg()).connect()

    assert ok, msg
    argv = seen["argv"]
    assert "-batch" in argv and "-ssh" in argv
    assert "-pw" not in argv
    assert not any("s3cr3t" in a for a in argv)
    assert seen["mode"] == 0o600
    assert seen["content"].strip() == "s3cr3t pass"
    assert not os.path.exists(seen["path"]), "password file must be deleted"


def test_pwfile_deleted_even_when_plink_crashes(monkeypatch):
    paths = []

    def fake_run(argv, **kwargs):
        paths.append(argv[argv.index("-pwfile") + 1])
        raise OSError("boom")

    monkeypatch.setattr(plink_client, "find_plink", lambda: "/usr/bin/plink")
    with patch("subprocess.run", side_effect=fake_run):
        ok, _ = PlinkSSHClient(cfg()).connect()
    assert not ok and paths and not os.path.exists(paths[0])


def test_key_auth_uses_identity_file_and_no_pwfile(monkeypatch, tmp_path):
    key = tmp_path / "id"
    key.write_text("x")
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    with patch("subprocess.run", return_value=completed(0, "Linux\n")) as run:
        PlinkSSHClient(cfg(auth_type="key", key_path=str(key), password=None)).connect()
    argv = run.call_args[0][0]
    assert "-i" in argv and str(key) in argv and "-pwfile" not in argv


def test_exec_runs_plink_with_command_and_connected_state(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return completed(0, "Linux\n")

    with patch("subprocess.run", side_effect=fake_run):
        c = PlinkSSHClient(cfg())
        assert not c.is_connected()
        ok, _ = c.connect()
        assert ok and c.is_connected() and c._remote_os == "Linux"
        code, out, _ = c._raw_exec("echo hi")
        assert code == 0
        c.disconnect()
        assert not c.is_connected()
    assert calls[-1][-1].endswith("echo hi")
    assert calls[-1][-2] == "192.168.100.14"


def test_sudo_password_is_sent_on_stdin_not_argv(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    with patch("subprocess.run", return_value=completed(0, "Linux\n")) as run:
        c = PlinkSSHClient(cfg(username="admin"))
        c.connect()
        c._exec_with_sudo("smartctl -a /dev/sda")
    argv, kwargs = run.call_args[0][0], run.call_args[1]
    assert kwargs["input"] == "s3cr3t pass\n"
    assert "s3cr3t" not in " ".join(argv)


def test_option_injection_in_host_is_rejected(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    with patch("subprocess.run") as run:
        ok, msg = PlinkSSHClient(cfg(host="-oProxyCommand=evil")).connect()
    assert not ok and not run.called


# ---- plink missing ------------------------------------------------------------------------

def test_missing_plink_gives_install_instructions(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: None)
    ok, msg = PlinkSSHClient(cfg()).connect()
    assert not ok
    for hint in ("apt install putty-tools", "pacman -S putty", "brew install putty"):
        assert hint in msg


# ---- host key verification ----------------------------------------------------------------

def test_parse_fingerprint_variants():
    assert parse_plink_fingerprint(UNCACHED) == ("ssh-dss", "1024", SHA)
    assert parse_plink_fingerprint(f"  ssh-dss 1024 {MD5}\n") == ("ssh-dss", "1024", MD5)
    assert parse_plink_fingerprint("nothing here") is None


def test_unknown_host_key_is_not_trusted_blindly(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    c = PlinkSSHClient(cfg())
    with patch("subprocess.run", return_value=completed(2, "", UNCACHED)):
        ok, msg = c.connect()
    assert not ok
    assert "Host Key Not Verified" in msg and SHA in msg and "ssh-dss" in msg
    host, key = c.last_untrusted_host_key
    assert host == "192.168.100.14" and isinstance(key, PlinkHostKey)
    assert host_key_fingerprint(key) == SHA and key.get_name() == "ssh-dss 1024"


def test_trusting_pins_fingerprint_and_next_connect_uses_hostkey(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    c = PlinkSSHClient(cfg())
    with patch("subprocess.run", return_value=completed(2, "", UNCACHED)):
        c.connect()
    host, key = c.last_untrusted_host_key
    assert trust_host_key(host, key)

    path = LegacyHostKeyStore.path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with patch("subprocess.run", return_value=completed(0, "Linux\n")) as run:
        ok, _ = PlinkSSHClient(cfg()).connect()
    assert ok
    argv = run.call_args[0][0]
    assert argv[argv.index("-hostkey") + 1] == SHA


def test_mismatch_with_cached_key_is_a_security_alert(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    c = PlinkSSHClient(cfg())
    with patch("subprocess.run", return_value=completed(2, "", MISMATCH)):
        ok, msg = c.connect()
    assert not ok and "SECURITY ALERT" in msg and "Host Key Mismatch" in msg
    assert c.last_untrusted_host_key is None, "a changed key must never be offered for trust"


def test_pinned_key_that_changed_is_a_security_alert(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    LegacyHostKeyStore.save(PlinkHostKey("192.168.100.14", 22, "ssh-dss", "1024", MD5))
    other = UNCACHED  # host key not in manually configured list -> reports a different fingerprint
    c = PlinkSSHClient(cfg())
    with patch("subprocess.run", return_value=completed(2, "", other)):
        ok, msg = c.connect()
    assert not ok and "SECURITY ALERT" in msg
    assert c.last_untrusted_host_key is None


def test_authentication_failure_message(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    with patch("subprocess.run", return_value=completed(1, "", "Access denied\nFATAL ERROR: No supported authentication methods available")):
        ok, msg = PlinkSSHClient(cfg()).connect()
    assert not ok and "Authentication Error" in msg


# ---- automatic engine selection ------------------------------------------------------------

DSS_ERRORS = [
    "Incompatible ssh peer (no acceptable host key)",
    "Incompatible ssh server (no acceptable kex algorithm)",
    "no matching host key type found. Their offer: ssh-dss",
    "Unable to negotiate with 192.168.100.14: no matching cipher found",
]


@pytest.mark.parametrize("text", DSS_ERRORS)
def test_negotiation_error_detection(text):
    assert is_legacy_negotiation_error(text)


@pytest.mark.parametrize("text", ["Authentication failed.", "Connection refused", "timed out", ""])
def test_other_errors_are_not_negotiation_errors(text):
    assert not is_legacy_negotiation_error(text)


@pytest.mark.parametrize("text", DSS_ERRORS)
def test_auto_falls_back_to_plink_on_ssh_dss(monkeypatch, text):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    monkeypatch.setattr("smartlinux.core.ssh_client.find_plink", lambda: "plink")
    with patch("paramiko.SSHClient.connect", side_effect=paramiko.SSHException(text)) as pm, \
         patch("subprocess.run", return_value=completed(0, "Linux\n")) as run:
        client = AutoSSHClient(cfg())
        ok, msg = client.connect()
    assert ok, msg
    assert pm.called and run.called
    assert client.engine_name == "plink" and client.is_connected()


def test_auto_prefers_paramiko_when_it_works():
    with patch("paramiko.SSHClient.connect", return_value=None), \
         patch.object(RemoteSSHClient, "_detect_os"), \
         patch("subprocess.run") as run:
        client = AutoSSHClient(cfg())
        ok, _ = client.connect()
    assert ok and client.engine_name == "paramiko" and not run.called


def test_auto_does_not_fallback_on_auth_error(monkeypatch):
    monkeypatch.setattr("smartlinux.core.ssh_client.find_plink", lambda: "plink")
    with patch("paramiko.SSHClient.connect", side_effect=paramiko.AuthenticationException()), \
         patch("subprocess.run") as run:
        ok, msg = AutoSSHClient(cfg()).connect()
    assert not ok and "Authentication" in msg and not run.called


def test_auto_does_not_fallback_on_untrusted_host_key():
    from smartlinux.core.ssh_client import HostKeyUntrustedException
    key = paramiko.RSAKey.generate(2048)
    with patch("paramiko.SSHClient.connect", side_effect=HostKeyUntrustedException("h", key)), \
         patch("subprocess.run") as run:
        client = AutoSSHClient(cfg())
        ok, msg = client.connect()
    assert not ok and "Host Key Not Verified" in msg and not run.called


def test_auto_without_plink_explains_how_to_install(monkeypatch):
    monkeypatch.setattr("smartlinux.core.ssh_client.find_plink", lambda: None)
    with patch("paramiko.SSHClient.connect", side_effect=paramiko.SSHException(DSS_ERRORS[0])):
        ok, msg = AutoSSHClient(cfg()).connect()
    assert not ok
    assert "plink" in msg and "apt install putty-tools" in msg and "pacman -S putty" in msg


def test_explicit_legacy_option_skips_paramiko(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    with patch("paramiko.SSHClient.connect") as pm, \
         patch("subprocess.run", return_value=completed(0, "Linux\n")):
        client = AutoSSHClient(cfg(legacy_ssh=True))
        ok, _ = client.connect()
    assert ok and not pm.called and client.engine_name == "plink"


def test_engine_choice_sticks_across_reconnects(monkeypatch):
    monkeypatch.setattr(plink_client, "find_plink", lambda: "plink")
    monkeypatch.setattr("smartlinux.core.ssh_client.find_plink", lambda: "plink")
    with patch("paramiko.SSHClient.connect", side_effect=paramiko.SSHException(DSS_ERRORS[0])) as pm, \
         patch("subprocess.run", return_value=completed(0, "Linux\n")):
        client = AutoSSHClient(cfg())
        client.connect()
        pm.reset_mock()
        client.connect()
    assert not pm.called


def test_auto_client_exposes_ui_interface():
    client = AutoSSHClient(cfg())
    for name in ("connect", "disconnect", "is_connected", "exec_smartctl", "run_self_test",
                 "discover_remote_drives", "read_remote_drive_smart"):
        assert callable(getattr(client, name))
        assert callable(getattr(PlinkSSHClient, name))
    assert client.last_untrusted_host_key is None


def test_server_config_roundtrip_keeps_legacy_flag():
    c = cfg(legacy_ssh=True)
    assert ServerConfig.from_dict(c.to_dict()).legacy_ssh is True
    assert ServerConfig.from_dict({"id": "x", "host": "h"}).legacy_ssh is False


def test_quiet_paramiko_transport_drops_and_restores_logging(caplog):
    import logging
    from smartlinux.core.ssh_client import _quiet_paramiko_transport
    log = logging.getLogger("paramiko.transport")
    with caplog.at_level(logging.ERROR, logger="paramiko.transport"):
        with _quiet_paramiko_transport():
            log.error("Exception (client): Incompatible ssh peer (no acceptable host key)")
        assert not caplog.records
        log.error("after")
        assert [r.getMessage() for r in caplog.records] == ["after"]

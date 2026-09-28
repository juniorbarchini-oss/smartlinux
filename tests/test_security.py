import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import paramiko

from smartlinux.core.models import ServerConfig
from smartlinux.core.config_manager import ConfigManager, KeyringHelper
from smartlinux.core.ssh_client import RemoteSSHClient, HostKeyUntrustedException, StrictKnownHostsPolicy


class TestSecurityHostKeysAndKeyring(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.orig_config_dir = ConfigManager.CONFIG_DIR
        self.orig_servers_file = ConfigManager.SERVERS_FILE

        ConfigManager.CONFIG_DIR = os.path.join(self.test_dir.name, ".config", "smartlinux")
        ConfigManager.SERVERS_FILE = os.path.join(ConfigManager.CONFIG_DIR, "servers.json")

    def tearDown(self):
        ConfigManager.CONFIG_DIR = self.orig_config_dir
        ConfigManager.SERVERS_FILE = self.orig_servers_file
        self.test_dir.cleanup()

    def test_config_directory_and_file_permissions(self):
        """Test that config dir is created with 0700 and servers.json with 0600."""
        srv = ServerConfig(
            id="srv-test-1",
            name="TestServer",
            host="192.168.1.50",
            port=22,
            username="admin",
            auth_type="password",
            password="SecretPassword123"
        )
        ConfigManager.save_servers([srv])

        # Verify directory permissions (0700)
        dir_mode = oct(os.stat(ConfigManager.CONFIG_DIR).st_mode & 0o777)
        self.assertEqual(dir_mode, "0o700")

        # Verify file permissions (0600)
        file_mode = oct(os.stat(ConfigManager.SERVERS_FILE).st_mode & 0o777)
        self.assertEqual(file_mode, "0o600")

    def test_keyring_migration_and_no_plaintext_passwords(self):
        """Test that passwords are stored in keyring and stripped from servers.json."""
        mock_keyring_store = {}

        def mock_set_pw(server_id, password):
            mock_keyring_store[server_id] = password
            return True

        def mock_get_pw(server_id):
            return mock_keyring_store.get(server_id)

        def mock_del_pw(server_id):
            mock_keyring_store.pop(server_id, None)
            return True

        with patch.object(KeyringHelper, "set_password", side_effect=mock_set_pw), \
             patch.object(KeyringHelper, "get_password", side_effect=mock_get_pw), \
             patch.object(KeyringHelper, "delete_password", side_effect=mock_del_pw):

            srv = ServerConfig(
                id="srv-test-keyring",
                name="KeyringServer",
                host="192.168.1.100",
                port=22,
                username="root",
                auth_type="password",
                password="SuperSecretPassword"
            )

            # Save server
            ConfigManager.add_server(srv)

            # Check that file exists and DOES NOT contain plaintext password
            with open(ConfigManager.SERVERS_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertNotIn("SuperSecretPassword", content)
                self.assertIn('"password": null', content)

            # Load servers and verify password was reconstructed from keyring
            loaded_servers = ConfigManager.load_servers()
            self.assertEqual(len(loaded_servers), 1)
            self.assertEqual(loaded_servers[0].password, "SuperSecretPassword")

            # Remove server and check keyring cleanup
            ConfigManager.remove_server(srv.id)
            self.assertNotIn(srv.id, mock_keyring_store)

    def test_legacy_plaintext_password_migration(self):
        """Test that legacy servers.json with plaintext passwords automatically migrates to keyring."""
        ConfigManager._ensure_dir()
        legacy_json = (
            '[\n'
            '  {\n'
            '    "id": "legacy-id-123",\n'
            '    "name": "LegacyNode",\n'
            '    "host": "192.168.1.200",\n'
            '    "port": 22,\n'
            '    "username": "admin",\n'
            '    "auth_type": "password",\n'
            '    "key_path": null,\n'
            '    "password": "OldPlaintextPassword"\n'
            '  }\n'
            ']\n'
        )
        with open(ConfigManager.SERVERS_FILE, "w", encoding="utf-8") as f:
            f.write(legacy_json)
        os.chmod(ConfigManager.SERVERS_FILE, 0o644)

        mock_keyring_store = {}

        with patch.object(KeyringHelper, "set_password", side_effect=lambda sid, pw: mock_keyring_store.update({sid: pw}) or True), \
             patch.object(KeyringHelper, "get_password", side_effect=lambda sid: mock_keyring_store.get(sid)):

            servers = ConfigManager.load_servers()
            self.assertEqual(len(servers), 1)
            self.assertEqual(servers[0].password, "OldPlaintextPassword")
            self.assertEqual(mock_keyring_store.get("legacy-id-123"), "OldPlaintextPassword")

            # Verify that servers.json was rewritten without the plaintext password and with 0600 mode
            with open(ConfigManager.SERVERS_FILE, "r", encoding="utf-8") as f:
                new_content = f.read()
                self.assertNotIn("OldPlaintextPassword", new_content)
                self.assertIn('"password": null', new_content)

            file_mode = oct(os.stat(ConfigManager.SERVERS_FILE).st_mode & 0o777)
            self.assertEqual(file_mode, "0o600")

    def test_strict_host_key_verification_rejects_untrusted_host(self):
        """Test that connecting to an untrusted host raises HostKeyUntrustedException and does not connect."""
        srv = ServerConfig(
            id="srv-untrusted",
            name="UntrustedHost",
            host="192.168.99.99",
            port=22,
            username="root",
            auth_type="password",
            password="test"
        )
        client = RemoteSSHClient(srv)

        # Mock known_hosts path to an empty temporary known_hosts file
        fake_known_hosts = os.path.join(self.test_dir.name, "known_hosts")
        with patch.object(RemoteSSHClient, "get_known_hosts_path", return_value=fake_known_hosts):
            # Attempt to connect to a mock server that presents an RSA key
            mock_key = paramiko.RSAKey.generate(2048)
            with patch("paramiko.SSHClient.connect", side_effect=HostKeyUntrustedException("192.168.99.99", mock_key)):
                ok, msg = client.connect(timeout=2)
                self.assertFalse(ok)
                self.assertIn("Host Key Not Verified", msg)
                self.assertIn("192.168.99.99", msg)
                self.assertIn("To protect against Man-in-the-Middle (MITM) attacks", msg)

    def test_host_key_mismatch_alerts_critical_security(self):
        """Test that a host key mismatch raises BadHostKeyException with critical MITM warning."""
        srv = ServerConfig(
            id="srv-mismatch",
            name="MismatchHost",
            host="192.168.99.100",
            port=22,
            username="root",
            auth_type="password",
            password="test"
        )
        client = RemoteSSHClient(srv)

        fake_known_hosts = os.path.join(self.test_dir.name, "known_hosts")
        with patch.object(RemoteSSHClient, "get_known_hosts_path", return_value=fake_known_hosts):
            bad_key = paramiko.RSAKey.generate(2048)
            expected_key = paramiko.RSAKey.generate(2048)
            with patch("paramiko.SSHClient.connect", side_effect=paramiko.BadHostKeyException("192.168.99.100", bad_key, expected_key)):
                ok, msg = client.connect(timeout=2)
                self.assertFalse(ok)
                self.assertIn("SECURITY ALERT: Host Key Mismatch", msg)
                self.assertIn("Man-in-the-Middle (MITM) attack", msg)

    def test_trust_and_save_host_key(self):
        """Test that trust_and_save_host_key properly persists the host key with 0600 permissions."""
        fake_known_hosts = os.path.join(self.test_dir.name, "known_hosts")
        with patch.object(RemoteSSHClient, "get_known_hosts_path", return_value=fake_known_hosts):
            key = paramiko.RSAKey.generate(2048)
            saved = RemoteSSHClient.trust_and_save_host_key("192.168.1.150", key)
            self.assertTrue(saved)
            self.assertTrue(os.path.exists(fake_known_hosts))

            file_mode = oct(os.stat(fake_known_hosts).st_mode & 0o777)
            self.assertEqual(file_mode, "0o600")

            # Verify it can be loaded by paramiko HostKeys
            hk = paramiko.HostKeys()
            hk.load(fake_known_hosts)
            self.assertIn("192.168.1.150", hk)

    def test_keyring_helper_secret_tool_roundtrip(self):
        """Test real secret-tool store, lookup, and clear if available."""
        import shutil
        if not shutil.which("secret-tool"):
            self.skipTest("secret-tool not available on this system")

        test_id = "test-unit-" + os.urandom(4).hex()
        test_pw = "P@ssw0rdTest!"

        # Store
        saved = KeyringHelper.set_password(test_id, test_pw)
        self.assertTrue(saved)

        # Lookup
        retrieved = KeyringHelper.get_password(test_id)
        self.assertEqual(retrieved, test_pw)

        # Delete
        cleared = KeyringHelper.delete_password(test_id)
        self.assertTrue(cleared)

        # Verify gone
        post_clear = KeyringHelper.get_password(test_id)
        self.assertIsNone(post_clear)


if __name__ == "__main__":
    unittest.main()

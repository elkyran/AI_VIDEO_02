"""Offline preparation checks; no CLI, account, billing or model requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.gemini_free_route import (FreeRouteError, make_route, validate_route, route_paths,
                                   route_settings, prepare_home, child_environment, credential_metadata)


class GeminiFreeRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private" / "profiles"
        self.route = make_route("gemini-1", "gemini-3.8-flash", "personal-free-project",
                                free_tier_confirmed=True, no_billing_confirmed=True)

    def test_paid_unconfirmed_or_unknown_model_routes_fail_closed(self):
        for free, no_billing in ((False, True), (True, False), (False, False), (1, True)):
            with self.assertRaises(FreeRouteError):
                make_route("gemini-1", "gemini-3.8-flash", "project", free_tier_confirmed=free,
                           no_billing_confirmed=no_billing)
        with self.assertRaises(FreeRouteError):
            make_route("gemini-1", "gemini-unknown-paid", "project", free_tier_confirmed=True,
                       no_billing_confirmed=True)
        self.assertFalse(self.root.exists())

    def test_project_labels_reject_secret_like_input(self):
        for label in ("AIza-not-a-real-key", "my api_key", "password", "two\nlines", ""):
            with self.assertRaises(FreeRouteError):
                make_route("gemini-1", "gemini-3.8-flash", label,
                           free_tier_confirmed=True, no_billing_confirmed=True)

    def test_expiry_and_project_mismatch_block_without_relabeling_paid_tier(self):
        expired = deepcopy(self.route)
        expired["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.assertRaises(FreeRouteError) as error:
            validate_route(expired, "gemini-1")
        self.assertEqual(error.exception.code, "billing_confirmation_expired")
        changed = {**self.route, "project_label": "different-project"}
        with self.assertRaises(FreeRouteError) as error:
            validate_route(changed, "gemini-1")
        self.assertEqual(error.exception.code, "billing_identity_unverified")
        with self.assertRaises(FreeRouteError):
            validate_route({**self.route, "no_billing_confirmed": False}, "gemini-1")

    def test_api_home_is_distinct_and_old_oauth_files_unchanged_after_repeat(self):
        old_home = self.root / "gemini-1" / ".gemini"
        old_home.mkdir(parents=True)
        old_settings = old_home / "settings.json"
        old_credential = old_home / "oauth_creds.json"
        old_settings.write_text('{"preserve":true}', encoding="utf-8")
        old_credential.write_text("oauth-fixture-never-copy", encoding="utf-8")
        before = {path.name: path.read_bytes() for path in old_home.iterdir()}
        first = prepare_home(self.root, "gemini-1", self.route)
        second = prepare_home(self.root, "gemini-1", self.route)
        self.assertEqual(first, second)
        self.assertEqual(Path(first["home"]), self.root / "gemini-1-api-free")
        self.assertEqual({path.name: path.read_bytes() for path in old_home.iterdir()}, before)
        self.assertFalse(credential_metadata(self.root, "gemini-1")["present"])
        self.assertEqual(json.loads(Path(first["settings_path"]).read_text()), route_settings())
        self.assertFalse((Path(first["home"]) / ".gemini" / "oauth_creds.json").exists())
        self.assertEqual(len({route_paths(self.root, f"gemini-{n}")[0] for n in range(1, 5)}), 4)

    def test_environment_scrubs_keys_vertex_and_shared_keychain_defaults(self):
        source = {"PATH": "fixture", "GEMINI_API_KEY": "never-in-child", "GOOGLE_API_KEY": "never-in-child",
                  "GOOGLE_GENAI_USE_VERTEXAI": "true", "GOOGLE_CLOUD_PROJECT": "paid",
                  "GOOGLE_APPLICATION_CREDENTIALS": "global", "GEMINI_FORCE_FILE_STORAGE": "false",
                  "NODE_OPTIONS": "--require unexpected", "GEMINI_CLI_HOME": "old-home"}
        before = deepcopy(source)
        home, _, _ = route_paths(self.root, "gemini-1")
        child = child_environment(home, source)
        self.assertEqual(source, before)
        self.assertEqual(child["GEMINI_FORCE_FILE_STORAGE"], "true")
        self.assertEqual(child["HOME"], str(home))
        self.assertEqual(child["GEMINI_CLI_HOME"], str(home))
        self.assertEqual(set(child), {"PATH", "HOME", "USERPROFILE", "GEMINI_CLI_HOME",
                                      "GEMINI_TELEMETRY_ENABLED", "GEMINI_FORCE_FILE_STORAGE"})

    def test_credential_presence_never_reads_decrypts_or_proves_auth_access_billing(self):
        info = prepare_home(self.root, "gemini-1", self.route)
        credential = Path(info["home"]) / ".gemini" / "gemini-credentials.json"
        credential.write_bytes(b"opaque-official-store-fixture")
        with patch.object(Path, "read_text", side_effect=AssertionError("No content reads allowed")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("No content reads allowed")):
            evidence = credential_metadata(self.root, "gemini-1")
        self.assertTrue(evidence["present"])
        self.assertGreater(evidence["size"], 0)
        self.assertFalse(evidence["authentication_verified"])
        self.assertFalse(evidence["model_access_verified"])
        self.assertFalse(evidence["billing_verified"])

    def test_unknown_existing_settings_preserved_and_ancestor_environment_refused(self):
        info = prepare_home(self.root, "gemini-1", self.route)
        path = Path(info["settings_path"])
        path.write_text('{"manual":"preserve"}', encoding="utf-8")
        with self.assertRaises(FreeRouteError):
            prepare_home(self.root, "gemini-1", self.route)
        self.assertEqual(path.read_text(), '{"manual":"preserve"}')
        ancestor = self.root.parent / ".gemini" / ".env"
        ancestor.parent.mkdir()
        ancestor.write_text("DO_NOT_LOAD=fixture", encoding="utf-8")
        with self.assertRaises(FreeRouteError):
            prepare_home(self.root, "gemini-2", make_route("gemini-2", "gemini-3.8-flash", "second",
                                                          free_tier_confirmed=True, no_billing_confirmed=True))
        self.assertFalse((self.root / "gemini-2-api-free").exists())

    def test_same_project_does_not_claim_separate_quota(self):
        other = make_route("gemini-2", "gemini-3.8-flash", "PERSONAL-FREE-PROJECT",
                           free_tier_confirmed=True, no_billing_confirmed=True)
        self.assertEqual(other["project_identity_hash"], self.route["project_identity_hash"])
        self.assertFalse(other["quota_isolation_verified"])
        self.assertFalse(other["rotation_on_quota"])


if __name__ == "__main__":
    unittest.main()

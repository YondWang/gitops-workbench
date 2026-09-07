from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import auth
from auth import AuthManager, User, hash_password, login_cookie


def valid_hash() -> str:
    return hash_password("S3cret!Password")


VALID_USERS = json.dumps({
    "zhangsan": {"role": "user", "password_hash": valid_hash()},
    "lisi": {"role": "user", "password_hash": valid_hash()},
    "workbench-admin": {"role": "admin", "password_hash": valid_hash()},
})


class LoginTest(unittest.TestCase):
    def test_default_test_accounts_work_outside_production(self):
        manager = AuthManager.from_environment()
        admin = manager.login("admin", "admin123")
        user = manager.login("user", "user123")
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(user["role"], "user")

    def test_session_returns_username_and_role(self):
        manager = AuthManager.from_environment()
        session = manager.login("user", "user123")
        info = manager.session(session["token"])
        self.assertEqual(info["username"], "user")
        self.assertEqual(info["role"], "user")

    def test_user_role_cannot_require_admin(self):
        manager = AuthManager.from_environment()
        session = manager.login("user", "user123")
        with self.assertRaises(PermissionError):
            manager.require(session["token"], "admin")

    def test_cookie_has_httponly_and_samesite(self):
        cookie = login_cookie("token")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

    def test_gitops_users_json_seeds_independent_accounts(self):
        with mock.patch.dict("os.environ", {"GITOPS_USERS_JSON": VALID_USERS}, clear=False):
            manager = AuthManager.from_environment()
        zhangsan = manager.login("zhangsan", "S3cret!Password")
        lisi = manager.login("lisi", "S3cret!Password")
        self.assertEqual(zhangsan["username"], "zhangsan")
        self.assertEqual(lisi["username"], "lisi")


class AccountCrudTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "users.json"
        self.secret = "x" * 32
        seed = {"workbench-admin": User("workbench-admin", "admin", valid_hash())}
        self.manager = AuthManager(seed, session_secret=self.secret, accounts_path=str(self.path))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_new_account_requires_password(self):
        with self.assertRaisesRegex(ValueError, "必须提供密码"):
            self.manager.add_user("zhangsan", "user", "")

    def test_add_creates_login_and_persists(self):
        self.manager.add_user("zhangsan", "user", "P@ssword123")
        session = self.manager.login("zhangsan", "P@ssword123")
        self.assertEqual(session["role"], "user")
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("zhangsan", stored)
        self.assertNotIn("password", stored["zhangsan"])
        self.assertTrue(stored["zhangsan"]["password_hash"].startswith("pbkdf2_sha256$"))

    def test_reload_recovers_admin_creation(self):
        self.manager.add_user("zhangsan", "user", "P@ssword123")
        reloaded = AuthManager(
            {"workbench-admin": User("workbench-admin", "admin", valid_hash())},
            session_secret=self.secret,
            accounts_path=str(self.path),
        )
        self.assertIn("zhangsan", reloaded.users)
        self.assertIsNotNone(reloaded.login("zhangsan", "P@ssword123"))

    def test_delete_user(self):
        self.manager.add_user("zhangsan", "user", "P@ssword123")
        self.manager.delete_user("zhangsan")
        self.assertNotIn("zhangsan", json.loads(self.path.read_text(encoding="utf-8")))

    def test_last_admin_cannot_be_demoted_or_deleted(self):
        with self.assertRaisesRegex(ValueError, "至少一个 admin"):
            self.manager.delete_user("workbench-admin")
        with self.assertRaisesRegex(ValueError, "至少一个 admin"):
            self.manager.update_user("workbench-admin", role="user")

    def test_invalid_role_rejected_on_create(self):
        with self.assertRaisesRegex(ValueError, "角色"):
            self.manager.add_user("zhangsan", "guest", "Password1")


if __name__ == "__main__":
    unittest.main()
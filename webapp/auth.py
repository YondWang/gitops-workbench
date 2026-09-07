from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http import cookies
from pathlib import Path
from typing import Any


COOKIE_NAME = "gitops_session"
SESSION_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 160_000

ROLE_PERMISSIONS = {
    "user": {"view", "create_feature", "create_feature_package"},
    "admin": {
        "view",
        "create_feature",
        "create_feature_package",
        "create_release",
        "create_bugfix",
        "create_tag",
        "admin",
    },
}

MIN_PASSWORD_LENGTH = 6


@dataclass(frozen=True)
class User:
    username: str
    role: str
    password_hash: str


class AuthManager:
    def __init__(self, users: dict[str, User], session_secret: str = "", accounts_path: str = "") -> None:
        if not users:
            raise ValueError("至少需要配置一个用户")
        self.users: dict[str, User] = {}
        for entry in users.values():
            self.users[entry.username] = entry
        self.session_secret = session_secret or secrets.token_urlsafe(32)
        self.sessions: dict[str, dict[str, Any]] = {}
        self.accounts_path = str(accounts_path or "")
        self._accounts_lock = threading.RLock()
        if self.accounts_path:
            self._load_or_initialize_accounts()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def from_environment(cls, accounts_path: str = "") -> "AuthManager":
        raw_users = os.environ.get("GITOPS_USERS_JSON", "").strip()
        if raw_users:
            users = parse_users_json(raw_users)
        else:
            users = {
                "admin": User("admin", "admin", hash_password(os.environ.get("GITOPS_ADMIN_PASSWORD", "admin123"))),
                "user": User("user", "user", hash_password(os.environ.get("GITOPS_USER_PASSWORD", "user123"))),
            }
        return cls(users, accounts_path=accounts_path)

    # ------------------------------------------------------------------ #
    # Account persistence
    # ------------------------------------------------------------------ #
    def _load_or_initialize_accounts(self) -> None:
        """Load the persistent account file, or seed it from the bootstrap users.

        Once the file exists it becomes the authoritative runtime source so that
        admin-managed accounts survive restarts.
        """
        path = Path(self.accounts_path)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = None
            if isinstance(data, dict) and data:
                users: dict[str, User] = {}
                for username, info in data.items():
                    if not isinstance(info, dict):
                        continue
                    role = str(info.get("role") or "user")
                    if role not in ROLE_PERMISSIONS:
                        continue
                    password_hash = str(info.get("password_hash") or "")
                    if not password_hash:
                        continue
                    users[str(username)] = User(str(username), role, password_hash)
                if users:
                    self.users = users
                    return
        path.parent.mkdir(parents=True, exist_ok=True)
        self._persist()

    def _serialize(self) -> dict[str, Any]:
        return {
            username: {"role": user.role, "password_hash": user.password_hash}
            for username, user in sorted(self.users.items())
        }

    def _persist(self) -> None:
        if not self.accounts_path:
            return
        with self._accounts_lock:
            path = Path(self.accounts_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(json.dumps(self._serialize(), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    # ------------------------------------------------------------------ #
    # Account management (admin CRUD)
    # ------------------------------------------------------------------ #
    def accounts(self) -> dict[str, list[dict[str, str]]]:
        return {
            "ok": True,
            "accounts": [
                {"username": user.username, "role": user.role}
                for user in sorted(self.users.values(), key=lambda item: item.username)
            ],
        }

    def add_user(self, username: str, role: str, password: str) -> dict[str, Any]:
        username = str(username or "").strip()
        if not username:
            raise ValueError("用户名不能为空")
        if username in self.users:
            raise ValueError(f"账号 {username} 已存在")
        role = require_role(role)
        password_hash = hash_password(require_password(password))
        with self._accounts_lock:
            self.users[username] = User(username, role, password_hash)
            self._persist()
        return {"ok": True, "account": {"username": username, "role": role}}

    def update_user(self, username: str, role: str | None = None, password: str | None = None) -> dict[str, Any]:
        username = str(username or "").strip()
        current = self.users.get(username)
        if current is None:
            raise ValueError(f"账号 {username} 不存在")
        new_role = require_role(role) if role is not None else current.role
        if current.role == "admin" and new_role != "admin" and self._admin_count() <= 1:
            raise ValueError("必须保留至少一个 admin 账号")
        new_password_hash = current.password_hash
        if password is not None:
            new_password_hash = hash_password(require_password(password))
        with self._accounts_lock:
            self.users[username] = User(username, new_role, new_password_hash)
            self._persist()
        drop_user_sessions(self.sessions, username)
        return {"ok": True, "account": {"username": username, "role": new_role}}

    def delete_user(self, username: str) -> dict[str, Any]:
        username = str(username or "").strip()
        current = self.users.get(username)
        if current is None:
            raise ValueError(f"账号 {username} 不存在")
        if current.role == "admin" and self._admin_count() <= 1:
            raise ValueError("必须保留至少一个 admin 账号")
        with self._accounts_lock:
            del self.users[username]
            self._persist()
        drop_user_sessions(self.sessions, username)
        return {"ok": True}

    def _admin_count(self) -> int:
        return sum(1 for user in self.users.values() if user.role == "admin")

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str) -> dict[str, str]:
        user = self.users.get(username)
        if not user or not verify_password(password, user.password_hash):
            raise PermissionError("用户名或密码错误")
        token = secrets.token_urlsafe(32)
        self.sessions[token] = {
            "username": user.username,
            "role": user.role,
            "expires_at": time.time() + SESSION_SECONDS,
        }
        return {"token": token, "username": user.username, "role": user.role}

    def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    def session(self, token: str) -> dict[str, str] | None:
        if not token:
            return None
        item = self.sessions.get(token)
        if not item:
            return None
        if item["expires_at"] < time.time():
            self.sessions.pop(token, None)
            return None
        return {"username": item["username"], "role": item["role"]}

    def require(self, token: str, permission: str) -> dict[str, str]:
        session = self.session(token)
        if not session:
            raise PermissionError("请先登录")
        role = session["role"]
        if permission not in ROLE_PERMISSIONS.get(role, set()):
            raise PermissionError("当前角色没有执行该操作的权限")
        return session


def require_role(role: Any) -> str:
    role = str(role or "").strip()
    if role not in ROLE_PERMISSIONS:
        raise ValueError("角色必须为 user 或 admin")
    return role


def require_password(password: Any) -> str:
    password = str(password or "")
    if not password:
        raise ValueError("设置账号时必须提供密码")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
    return password


def parse_users_json(raw: str) -> dict[str, User]:
    data = json.loads(raw)
    users: dict[str, User] = {}
    for username, info in data.items():
        role = str(info.get("role", "user"))
        if role not in ROLE_PERMISSIONS:
            raise ValueError(f"不支持的角色：{role}")
        password_hash = str(info.get("password_hash", ""))
        if not password_hash and "password" in info:
            password_hash = hash_password(str(info["password"]))
        if not password_hash:
            raise ValueError(f"用户 {username} 缺少 password 或 password_hash")
        users[str(username)] = User(str(username), role, password_hash)
    return users


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_raw, digest_raw = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_raw.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_raw.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def drop_user_sessions(sessions: dict[str, dict[str, Any]], username: str) -> None:
    for token, item in list(sessions.items()):
        if item.get("username") == username:
            sessions.pop(token, None)


def parse_cookie(header: str | None) -> str:
    if not header:
        return ""
    jar = cookies.SimpleCookie()
    jar.load(header)
    morsel = jar.get(COOKIE_NAME)
    return morsel.value if morsel else ""


def login_cookie(token: str) -> str:
    jar = cookies.SimpleCookie()
    jar[COOKIE_NAME] = token
    jar[COOKIE_NAME]["path"] = "/"
    jar[COOKIE_NAME]["httponly"] = True
    jar[COOKIE_NAME]["samesite"] = "Strict"
    jar[COOKIE_NAME]["max-age"] = str(SESSION_SECONDS)
    return jar.output(header="").strip()


def logout_cookie() -> str:
    jar = cookies.SimpleCookie()
    jar[COOKIE_NAME] = ""
    jar[COOKIE_NAME]["path"] = "/"
    jar[COOKIE_NAME]["httponly"] = True
    jar[COOKIE_NAME]["samesite"] = "Strict"
    jar[COOKIE_NAME]["max-age"] = "0"
    return jar.output(header="").strip()
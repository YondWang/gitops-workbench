from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from http import cookies
from typing import Any


COOKIE_NAME = "gitops_session"
SESSION_SECONDS = 8 * 60 * 60
PBKDF2_ITERATIONS = 160_000

ROLE_PERMISSIONS = {
    "user": {"view", "create_feature"},
    "admin": {
        "view",
        "create_feature",
        "create_release",
        "create_bugfix",
        "create_tag",
        "admin",
    },
}


@dataclass(frozen=True)
class User:
    username: str
    role: str
    password_hash: str


class AuthManager:
    def __init__(self, users: dict[str, User], session_secret: str = "") -> None:
        if not users:
            raise ValueError("至少需要配置一个用户")
        self.users = users
        self.session_secret = session_secret or secrets.token_urlsafe(32)
        self.sessions: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_environment(cls) -> "AuthManager":
        raw_users = os.environ.get("GITOPS_USERS_JSON", "").strip()
        if raw_users:
            users = parse_users_json(raw_users)
        else:
            users = {
                "admin": User("admin", "admin", hash_password(os.environ.get("GITOPS_ADMIN_PASSWORD", "admin123"))),
                "user": User("user", "user", hash_password(os.environ.get("GITOPS_USER_PASSWORD", "user123"))),
            }
        return cls(users, os.environ.get("GITOPS_SESSION_SECRET", ""))

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

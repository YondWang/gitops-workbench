#!/usr/bin/env python3
"""Password hash generation for the GitOps Workbench account store.

Only prints the resulting PBKDF2 hash to stdout. It never writes to the
repository, never saves the plaintext password, and never changes running
configuration.

Usage:
    python3 webapp/manage_auth.py hash-password
    echo 'your-secret' | python3 webapp/manage_auth.py hash-password
    python3 webapp/manage_auth.py hash-password 'your-secret'
"""
from __future__ import annotations

import argparse
import getpass
import sys

from auth import hash_password


def read_password(explicit: str | None) -> str:
    if explicit:
        return explicit
    if not sys.stdin.isatty():
        value = sys.stdin.read()
        if value:
            # Strip a single trailing newline so the script is shell-friendly.
            return value.rstrip("\r\n")
    value = getpass.getpass("Password (echo is hidden):")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="manage_auth",
        description="非交互式生成账号密码哈希。命令行传入的明文不会保存，仅输出哈希。",
    )
    parser.add_argument("command", choices=["hash-password"], help="当前仅支持 hash-password")
    parser.add_argument("password", nargs="?", help="可选明文密码参数；缺省时从 stdin 或以交互方式读取")
    args = parser.parse_args()

    if args.command != "hash-password":
        parser.error("未知命令")
    password = read_password(args.password)
    if not password:
        parser.error("密码不能为空")
    print(hash_password(password))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
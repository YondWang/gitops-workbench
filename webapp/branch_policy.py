from __future__ import annotations

import re
from dataclasses import dataclass


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class FixBranch:
    name: str
    version: str
    rc: int


def require_version(version: str) -> str:
    value = version.strip()
    if not VERSION_RE.fullmatch(value):
        raise ValueError("版本号必须是四段式，例如 3.2.0.0")
    return value


def safe_ref_name(name: str) -> bool:
    if not name or len(name) > 160:
        return False
    if name.startswith(("-", "/", ".")) or name.endswith(("/", ".")):
        return False
    if ".." in name or "//" in name or "@{" in name:
        return False
    return SAFE_REF_RE.fullmatch(name) is not None


def require_ref_name(name: str, label: str = "分支名") -> str:
    value = name.strip()
    if not safe_ref_name(value):
        raise ValueError(f"{label}不合法：{name}")
    return value


def slug(value: str, fallback: str = "work") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text or fallback


def clean_ticket(value: str, fallback: str = "TASK") -> str:
    ticket = re.sub(r"[^A-Za-z0-9]+", "", value.strip().upper())
    return ticket or fallback


def baseline_branch(version: str) -> str:
    return f"baseline/{require_version(version)}"


def feature_branch(version: str, ticket: str, desc: str) -> str:
    return f"feature/{require_version(version)}/{clean_ticket(ticket)}-{slug(desc)}"


def fix_branch(version: str, rc_number: int) -> str:
    if rc_number < 1:
        raise ValueError("rc 序号必须大于 0")
    return f"fix_{require_version(version)}/rc{rc_number}"


def parse_baseline(name: str) -> str:
    match = re.fullmatch(r"baseline/(\d+\.\d+\.\d+\.\d+)", name.strip())
    if not match:
        raise ValueError("请选择 baseline/{version} 分支")
    return match.group(1)


def parse_fix(name: str) -> FixBranch:
    match = re.fullmatch(r"fix_(\d+\.\d+\.\d+\.\d+)/rc(\d+)", name.strip())
    if not match:
        raise ValueError("请选择 fix_{version}/rcN 分支")
    return FixBranch(name=name.strip(), version=match.group(1), rc=int(match.group(2)))


def next_fix_rc(version: str, branch_names: list[str]) -> int:
    require_version(version)
    prefix = f"fix_{version}/rc"
    max_rc = 0
    for name in branch_names:
        if not name.startswith(prefix):
            continue
        parsed = re.fullmatch(rf"fix_{re.escape(version)}/rc(\d+)", name)
        if parsed:
            max_rc = max(max_rc, int(parsed.group(1)))
    return max_rc + 1


def default_release_tag(fix_name: str) -> str:
    fix = parse_fix(fix_name)
    return f"v{fix.version}-rc{fix.rc}"


def classify_branch(name: str) -> str:
    if re.fullmatch(r"baseline/\d+\.\d+\.\d+\.\d+", name):
        return "baseline"
    if re.fullmatch(r"fix_\d+\.\d+\.\d+\.\d+/rc\d+", name):
        return "fix"
    if name.startswith("feature/"):
        return "feature"
    if name.startswith("bugfix/"):
        return "bugfix"
    if name.startswith("hotfix/"):
        return "hotfix"
    if name.startswith("stable/"):
        return "stable"
    return "other"

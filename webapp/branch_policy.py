from __future__ import annotations

import re
from dataclasses import dataclass


VERSION_RE = re.compile(r"^[Vv]?(\d{1,4})\.(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
VERSION_IN_TEXT_RE = re.compile(r"[Vv]?(\d{1,4})\.(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
MAX_VERSION_PART = 9999


@dataclass(frozen=True)
class FixBranch:
    name: str
    version: str
    rc: int


@dataclass(frozen=True, order=True)
class Version:
    major: int
    minor: int
    patch: int
    revision: int

    def normalized(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}.{self.revision}"

    def display(self) -> str:
        return f"V{self.normalized()}"


def require_version(version: str) -> str:
    return parse_version(version).normalized()


def parse_version(version: str) -> Version:
    value = version.strip()
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError("版本号必须是四段式，例如 3.2.0.0")
    parts = tuple(int(item) for item in match.groups())
    if any(part > MAX_VERSION_PART for part in parts):
        raise ValueError("版本号每段不能超过 4 位")
    return Version(*parts)


def bump_version(version: str, bump_type: str) -> str:
    current = parse_version(version)
    bump = (bump_type or "minor").strip().lower()
    if bump == "major":
        next_version = Version(current.major + 1, 0, 0, 0)
    elif bump == "minor":
        next_version = Version(current.major, current.minor + 1, 0, 0)
    elif bump == "patch":
        next_version = Version(current.major, current.minor, current.patch + 1, 0)
    elif bump in {"build", "revision"}:
        next_version = Version(current.major, current.minor, current.patch, current.revision + 1)
    else:
        raise ValueError("版本变更类型仅支持 major/minor/patch/build")
    return normalize_overflow(next_version).normalized()


def normalize_overflow(version: Version) -> Version:
    major, minor, patch, revision = version.major, version.minor, version.patch, version.revision
    if revision > MAX_VERSION_PART:
        patch += 1
        revision = 0
    if patch > MAX_VERSION_PART:
        minor += 1
        patch = 0
    if minor > MAX_VERSION_PART:
        major += 1
        minor = 0
    if major > MAX_VERSION_PART:
        raise ValueError("版本号已超过四段式最大范围")
    return Version(major, minor, patch, revision)


def extract_versions(values: list[str]) -> list[Version]:
    versions: list[Version] = []
    for value in values:
        for match in VERSION_IN_TEXT_RE.finditer(value):
            try:
                versions.append(Version(*(int(item) for item in match.groups())))
            except ValueError:
                continue
    return versions


def latest_version(values: list[str], default: str = "0.0.0.0") -> str:
    versions = extract_versions(values)
    if not versions:
        return require_version(default)
    return max(versions).normalized()


def version_suggestions(values: list[str], default: str = "0.0.0.0") -> dict[str, str]:
    latest = latest_version(values, default)
    return {
        "latest": latest,
        "major": bump_version(latest, "major"),
        "minor": bump_version(latest, "minor"),
        "patch": bump_version(latest, "patch"),
        "build": bump_version(latest, "build"),
    }


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

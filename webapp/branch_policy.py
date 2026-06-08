from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime


VERSION_RE = re.compile(r"^[Vv]?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


@dataclass(frozen=True)
class FeatureBranch:
    name: str
    ticket: str
    desc: str


def require_version(version: str) -> str:
    value = version.strip()
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError("版本号必须是三段式，例如 V1.0.0")
    return "V{}.{}.{}".format(*(int(item) for item in match.groups()))


def safe_ref_name(name: str) -> bool:
    if not name or len(name) > 160:
        return False
    if name.startswith(("-", "/", ".")) or name.endswith(("/", ".")):
        return False
    if ".." in name or "//" in name or "@{" in name:
        return False
    return SAFE_REF_RE.fullmatch(name) is not None


def require_ref_name(name: str, label: str = "引用名") -> str:
    value = name.strip()
    if not safe_ref_name(value):
        raise ValueError(f"{label}不合法：{name}")
    return value


def slug(value: str, fallback: str = "work") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text or fallback


def clean_ticket(value: str, fallback: str = "TASK") -> str:
    ticket = re.sub(r"[^A-Za-z0-9-]+", "-", value.strip().upper())
    ticket = re.sub(r"-{2,}", "-", ticket).strip("-")
    return ticket or fallback


def feature_branch(ticket: str, desc: str) -> str:
    return f"feature/{clean_ticket(ticket)}_{slug(desc)}"


def bugfix_branch(version: str) -> str:
    return f"bugfix/{require_version(version)}"


def parse_feature(name: str) -> FeatureBranch:
    match = re.fullmatch(r"feature/([A-Za-z0-9-]+)_([A-Za-z0-9._-]+)", name.strip())
    if not match:
        raise ValueError("请选择 feature/{TASKID}_{desc} 分支")
    return FeatureBranch(name=name.strip(), ticket=match.group(1), desc=match.group(2))


def tag_source_name(ref: str) -> str:
    value = require_ref_name(ref, "Tag 来源")
    return value.replace("/", "-")


def timestamp_now() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def default_tag_name(ref: str, timestamp: str | None = None) -> str:
    return f"{tag_source_name(ref)}-{timestamp or timestamp_now()}"


def classify_branch(name: str) -> str:
    if name == "release":
        return "release"
    if name == "fix":
        return "bugfix"
    if name.startswith("feature/"):
        return "feature"
    if re.fullmatch(r"bugfix/[Vv]?\d+\.\d+\.\d+", name):
        return "bugfix"
    return "other"

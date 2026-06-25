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


def source_slug(ref: str) -> str:
    value = ref.strip()
    if value == "release":
        return "release"
    if value == "fix":
        return "fix"
    if value.startswith("bugfix/"):
        return slug(value.split("/", 1)[1], "bugfix")
    return slug(value.replace("/", "-"), "src")


def feature_branch(ticket: str, desc: str, ref: str = "release") -> str:
    parts = [source_slug(ref)]
    cleaned_ticket = clean_ticket(ticket, "")
    if cleaned_ticket:
        parts.append(cleaned_ticket)
    parts.append(slug(desc))
    return f"feature/{'_'.join(parts)}"


def bugfix_branch(version: str) -> str:
    return f"bugfix/{require_version(version)}"


def parse_feature(name: str) -> FeatureBranch:
    match = re.fullmatch(r"feature/([A-Za-z0-9._-]+)_([A-Za-z0-9._-]+)", name.strip())
    if not match:
        raise ValueError("请选择 feature/{来源}_{功能描述} 分支")
    return FeatureBranch(name=name.strip(), ticket=match.group(1), desc=match.group(2))


def tag_source_name(ref: str) -> str:
    value = require_ref_name(ref, "Tag 来源")
    return value.replace("/", "-")


def tag_date_now() -> str:
    return datetime.now().strftime("%Y%m%d")


def default_tag_name(ref: str, version: str, stamp: str | None = None) -> str:
    normalized_version = version.strip().strip('"')
    if not normalized_version:
        raise ValueError("缺少 Tag 版本号")
    return f"{tag_source_name(ref)}_{normalized_version}_{stamp or tag_date_now()}"


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

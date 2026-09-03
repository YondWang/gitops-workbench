from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPO_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,62}$")
LEGACY_REVISION_BITS = {
    "simos": 1,
    "business": 2,
    "localization": 4,
    "mapengine": 8,
    "perception": 16,
    "pnc": 32,
}


@dataclass(frozen=True)
class RepositoryConfig:
    id: str
    name: str
    base_url: str
    project: str
    enabled: bool = True
    default_ref: str = "main"
    token_env: str = "GITLAB_TOKEN"
    ssl_verify: bool = True
    submodule_path: str = ""
    revision_bit: int = 0

    def public_dict(self, token_loaded: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data["token_loaded"] = token_loaded
        return data


class RepositoryStore:
    def __init__(self, path: Path, defaults: list[RepositoryConfig]) -> None:
        self.path = path
        self.defaults = self._normalize_all(defaults)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._save(self.defaults)
        else:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            repositories = self._normalize_all(raw.get("repositories", []))
            if raw.get("repositories", []) != [asdict(repository) for repository in repositories]:
                self._save(repositories)

    def list(self) -> list[RepositoryConfig]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._save(self.defaults)
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        return self._normalize_all(raw.get("repositories", []))

    def get(self, repo_id: str) -> RepositoryConfig:
        for repo in self.list():
            if repo.id == repo_id:
                return repo
        raise ValueError(f"仓库不存在：{repo_id}")

    def enabled(self) -> list[RepositoryConfig]:
        return [repo for repo in self.list() if repo.enabled]

    def add(self, payload: dict[str, Any]) -> RepositoryConfig:
        repos = self.list()
        repo = self._from_dict(payload, existing_repositories=repos)
        if any(item.id == repo.id for item in repos):
            raise ValueError(f"仓库 ID 已存在：{repo.id}")
        repos.append(repo)
        self._save(repos)
        return repo

    def update(self, repo_id: str, payload: dict[str, Any]) -> RepositoryConfig:
        repos = self.list()
        updated: RepositoryConfig | None = None
        next_repos: list[RepositoryConfig] = []
        for repo in repos:
            if repo.id == repo_id:
                merged = {**asdict(repo), **payload, "id": repo_id}
                updated = self._from_dict(merged, existing_repositories=repos)
                next_repos.append(updated)
            else:
                next_repos.append(repo)
        if updated is None:
            raise ValueError(f"仓库不存在：{repo_id}")
        self._save(next_repos)
        return updated

    def delete(self, repo_id: str) -> None:
        repos = self.list()
        next_repos = [repo for repo in repos if repo.id != repo_id]
        if len(next_repos) == len(repos):
            raise ValueError(f"仓库不存在：{repo_id}")
        if not next_repos:
            raise ValueError("至少保留一个仓库配置")
        self._save(next_repos)

    def _save(self, repos: list[RepositoryConfig]) -> None:
        data = {"repositories": [asdict(repo) for repo in repos]}
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _normalize_all(repositories: list[RepositoryConfig] | list[dict[str, Any]]) -> list[RepositoryConfig]:
        normalized: list[RepositoryConfig] = []
        for item in repositories:
            payload = asdict(item) if isinstance(item, RepositoryConfig) else item
            normalized.append(RepositoryStore._from_dict(payload, existing_repositories=normalized))
        return normalized

    @staticmethod
    def _from_dict(payload: dict[str, Any], existing_repositories: list[RepositoryConfig] | None = None) -> RepositoryConfig:
        repo_id = str(payload.get("id", "")).strip()
        if not REPO_ID_RE.fullmatch(repo_id):
            raise ValueError("仓库 ID 只能包含字母、数字、下划线和中划线，长度 2-63")
        name = str(payload.get("name") or repo_id).strip()
        base_url = str(payload.get("base_url", "")).strip().rstrip("/")
        project = str(payload.get("project", "")).strip().strip("/")
        token_env = str(payload.get("token_env") or "GITLAB_TOKEN").strip()
        default_ref = str(payload.get("default_ref") or "main").strip()
        submodule_path = str(payload.get("submodule_path") or "").strip().strip("/")
        revision_bit = RepositoryStore._revision_bit(payload.get("revision_bit"), repo_id, existing_repositories or [])
        if not name:
            raise ValueError("仓库名称不能为空")
        if not base_url:
            raise ValueError("GitLab 地址不能为空")
        if not project or "/" not in project:
            raise ValueError("项目路径必须类似 group/project")
        if not token_env:
            raise ValueError("Token 环境变量名不能为空")
        if submodule_path and (not submodule_path.startswith("src/") or ".." in submodule_path):
            raise ValueError("子模块路径必须是 src/... 且不能包含 ..")
        return RepositoryConfig(
            id=repo_id,
            name=name,
            base_url=base_url,
            project=project,
            enabled=bool(payload.get("enabled", True)),
            default_ref=default_ref,
            token_env=token_env,
            ssl_verify=bool(payload.get("ssl_verify", True)),
            submodule_path=submodule_path,
            revision_bit=revision_bit,
        )

    @staticmethod
    def _revision_bit(value: Any, repo_id: str, existing_repositories: list[RepositoryConfig]) -> int:
        if repo_id in LEGACY_REVISION_BITS:
            return LEGACY_REVISION_BITS[repo_id]
        if repo_id == "config":
            return 0
        try:
            revision_bit = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("版本修订位必须是 2 的正整数次幂") from exc
        if revision_bit <= 0:
            used = {repo.revision_bit for repo in existing_repositories if repo.revision_bit > 0}
            used.update(LEGACY_REVISION_BITS.values())
            revision_bit = 1
            while revision_bit in used:
                revision_bit *= 2
        if revision_bit & (revision_bit - 1):
            raise ValueError("版本修订位必须是 2 的正整数次幂")
        if any(repo.id != repo_id and repo.revision_bit == revision_bit for repo in existing_repositories):
            raise ValueError(f"版本修订位已被仓库占用：{revision_bit}")
        return revision_bit

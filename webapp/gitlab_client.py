from __future__ import annotations

import base64
import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GitLabError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.payload = payload


@dataclass(frozen=True)
class GitLabConfig:
    base_url: str
    project: str
    token: str
    ssl_verify: bool = True

    @property
    def project_id(self) -> str:
        return quote(self.project, safe="")


class GitLabClient:
    def __init__(self, config: GitLabConfig) -> None:
        self.config = config

    def public_config(self) -> dict[str, Any]:
        return {
            "base_url": self.config.base_url,
            "project": self.config.project,
            "project_api_id": self.config.project_id,
            "token_loaded": bool(self.config.token),
            "ssl_verify": self.config.ssl_verify,
        }

    def project_web_url(self) -> str:
        project_path = self.config.project.strip("/")
        return f"{self.config.base_url.rstrip('/')}/{project_path}"

    def project_api_path(self, suffix: str = "") -> str:
        suffix = suffix if suffix.startswith("/") or not suffix else f"/{suffix}"
        return f"/projects/{self.config.project_id}{suffix}"

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Any:
        if not self.config.base_url:
            raise GitLabError("缺少 GITLAB_BASE_URL 配置")
        if not self.config.project:
            raise GitLabError("缺少 GITLAB_PROJECT 配置")
        if not self.config.token:
            raise GitLabError("缺少 GITLAB_TOKEN 配置")

        url = f"{self.config.base_url.rstrip('/')}/api/v4{path}"
        if query:
            clean_query = {key: value for key, value in query.items() if value not in (None, "")}
            if clean_query:
                url = f"{url}?{urlencode(clean_query)}"

        body = None
        headers = {
            "PRIVATE-TOKEN": self.config.token,
            "Accept": "application/json",
        }
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(url, data=body, headers=headers, method=method)
        context = None if self.config.ssl_verify else ssl._create_unverified_context()
        try:
            with urlopen(request, timeout=30, context=context) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = raw
            raise GitLabError(self._format_error(exc.code, payload), exc.code, payload) from exc
        except URLError as exc:
            raise GitLabError(f"GitLab API 连接失败：{exc.reason}") from exc

    def paginated(self, path: str, query: dict[str, Any] | None = None) -> list[Any]:
        items: list[Any] = []
        page = 1
        while True:
            page_query = {"per_page": "100", "page": str(page)}
            if query:
                page_query.update(query)
            data = self.request("GET", path, query=page_query)
            if not isinstance(data, list):
                return items
            items.extend(data)
            if len(data) < 100:
                return items
            page += 1

    def project(self) -> dict[str, Any]:
        return self.request("GET", self.project_api_path())

    def branches(self, search: str = "") -> list[dict[str, Any]]:
        query = {"search": search} if search else None
        return self.paginated(self.project_api_path("/repository/branches"), query=query)

    def branch(self, branch: str) -> dict[str, Any]:
        return self.request("GET", self.project_api_path(f"/repository/branches/{quote(branch, safe='')}"))

    def tags(self, search: str = "") -> list[dict[str, Any]]:
        query = {"search": search} if search else None
        return self.paginated(self.project_api_path("/repository/tags"), query=query)

    def branch_names(self) -> list[str]:
        return [str(item.get("name", "")) for item in self.branches()]

    def tag_names(self) -> list[str]:
        return [str(item.get("name", "")) for item in self.tags()]

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        return self.request(
            "POST",
            self.project_api_path("/repository/branches"),
            payload={"branch": branch, "ref": ref},
        )

    def protect_branch(self, branch: str, push_access_level: int, merge_access_level: int) -> dict[str, Any]:
        return self.request(
            "POST",
            self.project_api_path("/protected_branches"),
            payload={
                "name": branch,
                "push_access_level": push_access_level,
                "merge_access_level": merge_access_level,
            },
        )

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        payload = {"tag_name": tag_name, "ref": ref}
        if message:
            payload["message"] = message
        return self.request("POST", self.project_api_path("/repository/tags"), payload=payload)

    def delete_tag(self, tag_name: str) -> dict[str, Any]:
        self.request("DELETE", self.project_api_path(f"/repository/tags/{quote(tag_name, safe='')}"))
        return {"name": tag_name, "deleted": True}

    def get_file_text(self, file_path: str, ref: str) -> str:
        data = self.request(
            "GET",
            self.project_api_path(f"/repository/files/{quote(file_path, safe='')}"),
            query={"ref": ref},
        )
        content = str(data.get("content", "")) if isinstance(data, dict) else ""
        return base64.b64decode(content).decode("utf-8")

    def file_exists(self, file_path: str, ref: str) -> bool:
        try:
            self.get_file_text(file_path, ref)
            return True
        except GitLabError as exc:
            if exc.status == 404:
                return False
            raise

    def create_commit(self, branch: str, commit_message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        return self.request(
            "POST",
            self.project_api_path("/repository/commits"),
            payload={
                "branch": branch,
                "commit_message": commit_message,
                "actions": actions,
            },
        )

    def update_file(self, file_path: str, branch: str, content: str, commit_message: str) -> dict[str, Any]:
        return self.request(
            "PUT",
            self.project_api_path(f"/repository/files/{quote(file_path, safe='')}"),
            payload={
                "branch": branch,
                "content": content,
                "commit_message": commit_message,
            },
        )

    def opened_merge_requests(self, source_branch: str, target_branch: str) -> list[dict[str, Any]]:
        return self.merge_requests(source_branch, target_branch, state="opened")

    def merge_requests(self, source_branch: str, target_branch: str, state: str = "all") -> list[dict[str, Any]]:
        return self.paginated(
            self.project_api_path("/merge_requests"),
            query={
                "state": state,
                "source_branch": source_branch,
                "target_branch": target_branch,
            },
        )

    def create_merge_request(self, source_branch: str, target_branch: str, title: str) -> dict[str, Any]:
        existing = self.opened_merge_requests(source_branch, target_branch)
        if existing:
            item = existing[0]
            item["_reused"] = True
            return item
        return self.request(
            "POST",
            self.project_api_path("/merge_requests"),
            payload={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "remove_source_branch": False,
            },
        )

    def accept_merge_request(self, iid: int) -> dict[str, Any]:
        return self.request(
            "PUT",
            self.project_api_path(f"/merge_requests/{iid}/merge"),
            payload={
                "should_remove_source_branch": False,
                "squash": False,
            },
        )

    @staticmethod
    def _format_error(status: int, payload: Any) -> str:
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("error") or payload
        else:
            message = payload or "未知错误"
        return f"GitLab API 返回 {status}：{message}"

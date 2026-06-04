#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from auth import AuthManager, login_cookie, logout_cookie, parse_cookie
from branch_policy import (
    baseline_branch,
    classify_branch,
    default_release_tag,
    feature_branch,
    fix_branch,
    next_fix_rc,
    parse_baseline,
    parse_fix,
    require_ref_name,
    require_version,
    version_suggestions,
)
from gitlab_client import GitLabClient, GitLabConfig, GitLabError
from repository_store import RepositoryConfig, RepositoryStore


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
REPOSITORIES_PATH = ROOT / "data" / "repositories.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "gitlab": {
        "base_url": "https://www.chancee-shanghai.cn:9900",
        "project": "software_hmi_app/business",
        "ssl_verify": True,
    },
    "repositories": [
        {
            "id": "business",
            "name": "business",
            "base_url": "https://www.chancee-shanghai.cn:9900",
            "project": "software_hmi_app/business",
            "enabled": True,
            "default_ref": "main",
            "token_env": "GITLAB_TOKEN",
            "ssl_verify": True,
        }
    ],
    "server": {
        "host": "127.0.0.1",
        "port": 8765,
    },
}


@dataclass(frozen=True)
class OperationTarget:
    repo: RepositoryConfig
    client: GitLabClient


class GitOpsApp:
    def __init__(self, store: RepositoryStore, auth: AuthManager) -> None:
        self.store = store
        self.auth = auth

    def public_config(self) -> dict[str, Any]:
        repos = self.repositories()["repositories"]
        default_repo = repos[0]["id"] if repos else ""
        return {
            "default_repository_id": default_repo,
            "repositories": repos,
            "roles": {
                "user": ["view", "create_feature"],
                "admin": ["view", "create_feature", "init_baseline", "create_fix", "release", "admin"],
            },
        }

    def repositories(self) -> dict[str, Any]:
        return {
            "ok": True,
            "repositories": [repo.public_dict(token_loaded=self.token_loaded(repo)) for repo in self.store.list()],
        }

    def add_repository(self, payload: dict[str, Any]) -> dict[str, Any]:
        repo = RepositoryStore._from_dict(payload)
        self.client_for(repo).project()
        repo = self.store.add(payload)
        return {"ok": True, "repository": repo.public_dict(token_loaded=self.token_loaded(repo))}

    def update_repository(self, repo_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get(repo_id)
        candidate = RepositoryStore._from_dict({**current.__dict__, **payload, "id": repo_id})
        self.client_for(candidate).project()
        updated = self.store.update(repo_id, payload)
        return {"ok": True, "repository": updated.public_dict(token_loaded=self.token_loaded(updated))}

    def delete_repository(self, repo_id: str) -> dict[str, Any]:
        self.store.delete(repo_id)
        return {"ok": True}

    def project(self, repo_id: str) -> dict[str, Any]:
        target = self.target(repo_id)
        return {"ok": True, "repository": target.repo.public_dict(self.token_loaded(target.repo)), "project": target.client.project()}

    def branches(self, repo_id: str, search: str = "") -> dict[str, Any]:
        target = self.target(repo_id)
        branches = [summarize_branch(item) for item in target.client.branches(search)]
        return {
            "ok": True,
            "repository": target.repo.public_dict(self.token_loaded(target.repo)),
            "branches": branches,
            "groups": group_branches(branches),
        }

    def tags(self, repo_id: str, search: str = "") -> dict[str, Any]:
        target = self.target(repo_id)
        tags = [summarize_tag(item) for item in target.client.tags(search)]
        return {
            "ok": True,
            "repository": target.repo.public_dict(self.token_loaded(target.repo)),
            "tags": tags,
        }

    def suggest_versions(self, repo_id: str) -> dict[str, Any]:
        target = self.target(repo_id)
        names = target.client.branch_names() + target.client.tag_names()
        return {
            "ok": True,
            "repository": target.repo.public_dict(self.token_loaded(target.repo)),
            "suggestions": version_suggestions(names),
        }

    def init_baseline(self, payload: dict[str, Any]) -> dict[str, Any]:
        requested_version = str(payload.get("version", "")).strip()
        bump_type = str(payload.get("bump_type", "minor")).strip() or "minor"
        explicit_version = require_version(requested_version) if requested_version else ""
        ref = require_ref_name(str(payload.get("ref", "")), "来源分支")
        if bump_type not in {"major", "minor", "patch", "build"}:
            raise ValueError("版本变更类型仅支持 major/minor/patch/build")

        def precheck(target: OperationTarget) -> dict[str, Any]:
            refs = set(target.client.branch_names()) | set(target.client.tag_names())
            if ref not in refs:
                raise ValueError(f"来源不存在：{ref}")
            version = explicit_version or version_suggestions(list(refs))[bump_type]
            branch = baseline_branch(version)
            if branch in refs:
                raise ValueError(f"目标 baseline 已存在：{branch}")
            return {"branch": branch, "ref": ref, "version": version, "bump_type": bump_type, "auto_generated": not explicit_version}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, "created": target.client.create_branch(context["branch"], ref)}

        return self.run_operation(payload, "init_baseline", precheck, execute)

    def create_fix(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_baseline = require_ref_name(str(payload.get("baseline", "")), "baseline 分支")
        version = parse_baseline(selected_baseline)

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            if selected_baseline not in branch_names:
                raise ValueError(f"baseline 不存在：{selected_baseline}")
            rc_number = next_fix_rc(version, branch_names)
            branch = fix_branch(version, rc_number)
            if branch in branch_names:
                raise ValueError(f"目标 fix 已存在：{branch}")
            return {"branch": branch, "ref": selected_baseline, "version": version, "rc_number": rc_number}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, "created": target.client.create_branch(context["branch"], selected_baseline)}

        return self.run_operation(payload, "create_fix", precheck, execute)

    def create_feature(self, payload: dict[str, Any]) -> dict[str, Any]:
        selected_baseline = require_ref_name(str(payload.get("baseline", "")), "baseline 分支")
        version = parse_baseline(selected_baseline)
        ticket = str(payload.get("ticket", ""))
        desc = str(payload.get("desc", ""))
        branch = feature_branch(version, ticket, desc)

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            if selected_baseline not in branch_names:
                raise ValueError(f"baseline 不存在：{selected_baseline}")
            if branch in branch_names:
                raise ValueError(f"目标 feature 已存在：{branch}")
            return {"branch": branch, "ref": selected_baseline, "version": version}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, "created": target.client.create_branch(branch, selected_baseline)}

        return self.run_operation(payload, "create_feature", precheck, execute)

    def release_from_fix(self, payload: dict[str, Any]) -> dict[str, Any]:
        fix_name = require_ref_name(str(payload.get("fix_branch", "")), "fix 分支")
        fix = parse_fix(fix_name)
        target_baseline = require_ref_name(str(payload.get("baseline") or baseline_branch(fix.version)), "baseline 分支")
        tag_name = require_ref_name(str(payload.get("tag_name") or default_release_tag(fix_name)), "Tag 名称")
        message = str(payload.get("message", "")).strip() or f"Release {tag_name} from {fix_name}"

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            tag_names = target.client.tag_names()
            if fix_name not in branch_names:
                raise ValueError(f"fix 分支不存在：{fix_name}")
            if target_baseline not in branch_names:
                raise ValueError(f"baseline 分支不存在：{target_baseline}")
            if tag_name in tag_names:
                raise ValueError(f"Tag 已存在：{tag_name}")
            existing_mrs = target.client.opened_merge_requests(fix_name, target_baseline)
            if existing_mrs:
                raise ValueError(f"已存在打开的 MR：{existing_mrs[0].get('web_url') or existing_mrs[0].get('iid')}")
            return {"fix_branch": fix_name, "baseline": target_baseline, "tag_name": tag_name}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            tag = target.client.create_tag(tag_name, fix_name, message)
            title = f"release: {fix_name} -> {target_baseline}"
            try:
                mr = target.client.create_merge_request(fix_name, target_baseline, title)
            except GitLabError as exc:
                return {
                    **context,
                    "ok": False,
                    "tag_created": True,
                    "tag": tag,
                    "error": str(exc),
                    "gitlab_status": exc.status,
                    "gitlab_payload": exc.payload,
                }
            try:
                merge = {
                    "ok": True,
                    "result": target.client.accept_merge_request(int(mr["iid"])),
                    "requires_manual_resolution": False,
                }
            except GitLabError as exc:
                merge = {
                    "ok": False,
                    "error": str(exc),
                    "gitlab_status": exc.status,
                    "gitlab_payload": exc.payload,
                    "requires_manual_resolution": True,
                }
            return {**context, "tag": tag, "merge_request": mr, "merge": merge}

        return self.run_operation(payload, "release", precheck, execute)

    def run_operation(
        self,
        payload: dict[str, Any],
        operation: str,
        precheck: Callable[[OperationTarget], dict[str, Any]],
        execute: Callable[[OperationTarget, dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        targets = self.targets(payload)
        precheck_results: list[dict[str, Any]] = []
        contexts: dict[str, dict[str, Any]] = {}
        for target in targets:
            try:
                target.client.project()
                context = precheck(target)
                contexts[target.repo.id] = context
                precheck_results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": True, "context": context})
            except Exception as exc:
                precheck_results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": False, "error": str(exc)})

        if any(not item["ok"] for item in precheck_results):
            return {"ok": False, "operation": operation, "phase": "precheck", "precheck": precheck_results, "results": []}

        results: list[dict[str, Any]] = []
        overall_ok = True
        for target in targets:
            try:
                result = execute(target, contexts[target.repo.id])
                result_ok = bool(result.get("ok", True))
                overall_ok = overall_ok and result_ok
                results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": result_ok, "result": result})
            except Exception as exc:
                overall_ok = False
                results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": False, "error": str(exc)})
        return {"ok": overall_ok, "operation": operation, "phase": "execute", "precheck": precheck_results, "results": results}

    def targets(self, payload: dict[str, Any]) -> list[OperationTarget]:
        scope = str(payload.get("scope", "single")).strip() or "single"
        if scope == "all":
            repos = self.store.enabled()
            if not repos:
                raise ValueError("没有启用的仓库")
            return [self.target(repo.id) for repo in repos]
        repo_id = str(payload.get("repository_id") or payload.get("repo_id") or "").strip()
        return [self.target(repo_id)]

    def target(self, repo_id: str) -> OperationTarget:
        if not repo_id:
            repos = self.store.list()
            if not repos:
                raise ValueError("没有配置仓库")
            repo = repos[0]
        else:
            repo = self.store.get(repo_id)
        return OperationTarget(repo=repo, client=self.client_for(repo))

    def client_for(self, repo: RepositoryConfig) -> GitLabClient:
        token = os.environ.get(repo.token_env, "")
        return GitLabClient(
            GitLabConfig(
                base_url=repo.base_url,
                project=repo.project,
                token=token,
                ssl_verify=repo.ssl_verify,
            )
        )

    @staticmethod
    def token_loaded(repo: RepositoryConfig) -> bool:
        token = os.environ.get(repo.token_env, "")
        return bool(token and token != "replace-with-a-gitlab-token")


def summarize_branch(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit") or {}
    return {
        "name": item.get("name", ""),
        "kind": classify_branch(str(item.get("name", ""))),
        "protected": bool(item.get("protected")),
        "default": bool(item.get("default")),
        "merged": bool(item.get("merged")),
        "web_url": item.get("web_url", ""),
        "commit_id": commit.get("short_id") or commit.get("id", "")[:12],
        "commit_title": commit.get("title", ""),
        "committed_date": commit.get("committed_date", ""),
    }


def summarize_tag(item: dict[str, Any]) -> dict[str, Any]:
    commit = item.get("commit") or {}
    return {
        "name": item.get("name", ""),
        "message": item.get("message") or "",
        "target": item.get("target") or "",
        "commit_id": commit.get("short_id") or commit.get("id", "")[:12],
        "commit_title": commit.get("title", ""),
    }


def group_branches(branches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {key: [] for key in ("baseline", "fix", "feature", "bugfix", "hotfix", "stable", "other")}
    for branch in branches:
        groups.setdefault(branch["kind"], []).append(branch)
    return groups


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_json_config(path: str | None) -> dict[str, Any]:
    config = DEFAULT_CONFIG
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            config = deep_merge(config, json.load(fh))
    return config


def default_repositories(config: dict[str, Any]) -> list[RepositoryConfig]:
    repositories = config.get("repositories") or []
    if not repositories and "gitlab" in config:
        gitlab = config["gitlab"]
        repositories = [
            {
                "id": "business",
                "name": "business",
                "base_url": os.environ.get("GITLAB_BASE_URL", gitlab["base_url"]),
                "project": os.environ.get("GITLAB_PROJECT", gitlab["project"]),
                "enabled": True,
                "default_ref": "main",
                "token_env": "GITLAB_TOKEN",
                "ssl_verify": os.environ.get("GITLAB_SSL_VERIFY", str(gitlab.get("ssl_verify", True))).lower() != "false",
            }
        ]
    return [RepositoryConfig(**item) for item in repositories]


def build_app(config_path: str | None) -> tuple[GitOpsApp, dict[str, Any]]:
    load_dotenv(ROOT / ".env.local")
    config = load_json_config(config_path)
    store = RepositoryStore(REPOSITORIES_PATH, default_repositories(config))
    return GitOpsApp(store, AuthManager.from_environment()), config


def make_handler(app: GitOpsApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GitOpsWorkbench/2.0"

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = {key: values[0] for key, values in parse_qs(parsed.query).items()}

            routes: dict[str, tuple[str, Callable[[], Any]]] = {
                "/api/session": ("public", lambda: {"ok": True, "session": self.current_session()}),
                "/api/config": ("view", app.public_config),
                "/api/repositories": ("view", app.repositories),
                "/api/project": ("view", lambda: app.project(query.get("repository_id", ""))),
                "/api/branches": ("view", lambda: app.branches(query.get("repository_id", ""), query.get("search", ""))),
                "/api/tags": ("view", lambda: app.tags(query.get("repository_id", ""), query.get("search", ""))),
                "/api/version/suggestions": ("view", lambda: app.suggest_versions(query.get("repository_id", ""))),
            }
            repo_route = match_repo_get(path)
            if repo_route:
                repo_id, resource = repo_route
                if resource == "branches":
                    routes[path] = ("view", lambda repo_id=repo_id: app.branches(repo_id, query.get("search", "")))
                elif resource == "tags":
                    routes[path] = ("view", lambda repo_id=repo_id: app.tags(repo_id, query.get("search", "")))
                elif resource == "project":
                    routes[path] = ("view", lambda repo_id=repo_id: app.project(repo_id))
            if path in routes:
                permission, action = routes[path]
                self.handle_api(permission, action)
                return
            self.serve_static(path)

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            payload = self.read_json()
            routes: dict[str, tuple[str, Callable[[], Any]]] = {
                "/api/login": ("public", lambda: self.login(payload)),
                "/api/logout": ("public", self.logout),
                "/api/repositories": ("admin", lambda: app.add_repository(payload)),
                "/api/baseline/init": ("init_baseline", lambda: app.init_baseline(payload)),
                "/api/fix/create": ("create_fix", lambda: app.create_fix(payload)),
                "/api/feature/create": ("create_feature", lambda: app.create_feature(payload)),
                "/api/release": ("release", lambda: app.release_from_fix(payload)),
            }
            self.dispatch_route(path, routes)

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            payload = self.read_json()
            repo_id = match_repository_path(path)
            if not repo_id:
                json_response(self, 404, {"ok": False, "error": "Unknown endpoint."})
                return
            self.handle_api("admin", lambda: app.update_repository(repo_id, payload))

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            repo_id = match_repository_path(path)
            if not repo_id:
                json_response(self, 404, {"ok": False, "error": "Unknown endpoint."})
                return
            self.handle_api("admin", lambda: app.delete_repository(repo_id))

        def dispatch_route(self, path: str, routes: dict[str, tuple[str, Callable[[], Any]]]) -> None:
            if path not in routes:
                json_response(self, 404, {"ok": False, "error": "Unknown endpoint."})
                return
            permission, action = routes[path]
            self.handle_api(permission, action)

        def handle_api(self, permission: str, action: Callable[[], Any]) -> None:
            try:
                if permission != "public":
                    app.auth.require(self.token(), permission)
                json_response(self, 200, action())
            except PermissionError as exc:
                status = 401 if str(exc) == "请先登录" else 403
                json_response(self, status, {"ok": False, "error": str(exc)})
            except ValueError as exc:
                json_response(self, 400, {"ok": False, "error": str(exc)})
            except GitLabError as exc:
                json_response(
                    self,
                    502,
                    {
                        "ok": False,
                        "error": str(exc),
                        "gitlab_status": exc.status,
                        "gitlab_payload": exc.payload,
                    },
                )
            except Exception as exc:
                json_response(self, 500, {"ok": False, "error": str(exc)})

        def login(self, payload: dict[str, Any]) -> dict[str, Any]:
            session = app.auth.login(str(payload.get("username", "")), str(payload.get("password", "")))
            self.extra_headers = {"Set-Cookie": login_cookie(session["token"])}
            return {"ok": True, "session": {"username": session["username"], "role": session["role"]}}

        def logout(self) -> dict[str, Any]:
            app.auth.logout(self.token())
            self.extra_headers = {"Set-Cookie": logout_cookie()}
            return {"ok": True}

        def current_session(self) -> dict[str, str] | None:
            return app.auth.session(self.token())

        def token(self) -> str:
            return parse_cookie(self.headers.get("Cookie"))

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

        def serve_static(self, path: str) -> None:
            target = "index.html" if path in ("", "/") else path.lstrip("/")
            file_path = (STATIC_ROOT / target).resolve()
            if not str(file_path).startswith(str(STATIC_ROOT.resolve())):
                json_response(self, 403, {"ok": False, "error": "Forbidden."})
                return
            if not file_path.exists() or not file_path.is_file():
                json_response(self, 404, {"ok": False, "error": "Not found."})
                return
            content_types = {
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
                ".js": "application/javascript; charset=utf-8",
            }
            text_response(self, 200, content_types.get(file_path.suffix, "application/octet-stream"), file_path.read_bytes())

    return Handler


def match_repository_path(path: str) -> str:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "repositories":
        return parts[2]
    return ""


def match_repo_get(path: str) -> tuple[str, str] | None:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "repositories":
        return parts[2], parts[3]
    return None


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in getattr(handler, "extra_headers", {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)
    handler.extra_headers = {}


def text_response(handler: BaseHTTPRequestHandler, status: int, content_type: str, body: bytes) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="GitLab branch management workbench")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    app, config = build_app(args.config)
    host = args.host or os.environ.get("GITOPS_HOST", config["server"]["host"])
    port = args.port or int(os.environ.get("GITOPS_PORT", config["server"]["port"]))
    server = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"GitLab Branch Workbench: http://{host}:{port}", flush=True)
    print(f"Repositories: {len(app.store.list())}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

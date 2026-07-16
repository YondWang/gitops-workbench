#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse, urlunparse
from zoneinfo import ZoneInfo

from auth import AuthManager, login_cookie, logout_cookie, parse_cookie
from branch_policy import (
    bugfix_branch,
    classify_branch,
    default_tag_name,
    feature_branch,
    require_ref_name,
    require_version,
    tag_source_name,
)
from gitlab_client import GitLabClient, GitLabConfig, GitLabError
from repository_store import RepositoryConfig, RepositoryStore


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
REPOSITORIES_PATH = ROOT / "data" / "repositories.json"
VERSION_SETTINGS_PATH = ROOT / "data" / "version-settings.json"
SCHEDULES_PATH = ROOT / "data" / "schedules.json"
SCHEDULE_RUNS_PATH = ROOT / "data" / "schedule-runs.json"
RELEASE_TASKS_PATH = Path(os.environ.get("GITOPS_RELEASE_TASKS_PATH", str(ROOT / "data" / "release_tasks.json")))
RELEASE_RUNS_PATH = Path(os.environ.get("GITOPS_RELEASE_RUNS_PATH", str(ROOT / "data" / "release_runs.json")))

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
        },
        {
            "id": "config",
            "name": "config",
            "base_url": "https://www.chancee-shanghai.cn:9900",
            "project": "OS/config",
            "enabled": False,
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

NO_ONE = 0
MAINTAINER = 40

BRANCH_PROTECTION = {
    "release": {"push_access_level": NO_ONE, "merge_access_level": MAINTAINER},
    "bugfix": {"push_access_level": NO_ONE, "merge_access_level": MAINTAINER},
}

VERSION_INFO_PATH = "version.info"
PKG_INFO_PATH = "pkg.info"
SOFTWARE_YAML_PATH = "software.yaml"
RESIDENT_ARTIFACT_ROOT = Path(os.environ.get("SIMOS_CI_ARTIFACT_ROOT", "/data/simos-ci/artifacts"))
RESIDENT_ARTIFACT_FILE = os.environ.get("SIMOS_CI_ARTIFACT_FILE", "resident.tar.gz")
RESIDENT_BUILD_INFO_FILE = os.environ.get("SIMOS_CI_BUILD_INFO_FILE", "build-info.json")
RESIDENT_ARTIFACT_JOB_NAMES = tuple(
    name.strip().lower()
    for name in os.environ.get("SIMOS_CI_ARTIFACT_JOB_NAMES", "resident,resident-package,package,build").split(",")
    if name.strip()
)
RESIDENT_TAG_PATTERN = re.compile(
    r"^(?:release(?:-[A-Za-z0-9._-]+)?|fix(?:-[A-Za-z0-9._-]+)?|bugfix-[A-Za-z0-9._-]+|[A-Za-z0-9._-]+)_[VvFfTt]?\d+(?:\.\d+)+_\d{12}$"
)
TAG_VERSION_HINT_RE = re.compile(r"[VvFfTt]?\d+(?:\.\d+)+")
VERSION_COMPONENTS = ("simos", "business", "localization", "perception", "mapengine", "pnc")
SUBMODULE_COMPONENTS = ("business", "localization", "mapengine", "perception", "pnc")
VERSION_COMPONENT_REVISIONS = {
    "simos": 1,
    "business": 2,
    "localization": 4,
    "mapengine": 8,
    "perception": 16,
    "pnc": 32,
}

DEFAULT_SCHEDULE: dict[str, Any] = {
    "id": "daily-simos-resident-release",
    "enabled": True,
    "name": "SimOS 每日 resident 自动构建",
    "timezone": "Asia/Shanghai",
    "cron": "0 16 * * *",
    "daily_time": "16:00",
    "source_ref_strategy": "fixed_ref",
    "default_ref": "fix",
    "config_ref": "",
    "config_matrix_enabled": True,
    "config_matrix": [
        {"config_ref": "SIMBOT_R6_A", "label": "360", "enabled": True},
        {"config_ref": "SIMBOT_R6_B", "label": "360s", "enabled": True},
    ],
    "version_source": "simos_version_info",
    "manual_version_number": "",
    "version_prefix_mode": "auto",
    "manual_version_prefix": "V",
    "cloud_category": "车机/CI自动构建",
    "execution_type": "full_release",
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
            "version_update": load_version_settings(),
            "roles": {
                "user": ["view", "create_feature"],
                "admin": ["view", "create_feature", "create_release", "create_bugfix", "create_tag", "admin"],
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

    def common_refs(self) -> dict[str, Any]:
        targets = [self.target(repo.id) for repo in self.store.enabled()]
        if not targets:
            raise ValueError("没有启用的仓库")

        repositories = [target.repo.public_dict(self.token_loaded(target.repo)) for target in targets]
        branch_maps: list[dict[str, dict[str, Any]]] = []
        tag_maps: list[dict[str, dict[str, Any]]] = []
        for target in targets:
            target.client.project()
            branch_maps.append({item["name"]: item for item in (summarize_branch(branch) for branch in target.client.branches())})
            tag_maps.append({item["name"]: item for item in (summarize_tag(tag) for tag in target.client.tags())})

        branch_names = sorted(set.intersection(*(set(items) for items in branch_maps))) if branch_maps else []
        tag_names = sorted(set.intersection(*(set(items) for items in tag_maps))) if tag_maps else []
        branches = [branch_maps[0][name] for name in branch_names]
        tags = [tag_maps[0][name] for name in tag_names]
        feature_sources = [branch for branch in branches if branch["kind"] in {"release", "bugfix"}]
        return {
            "ok": True,
            "repositories": repositories,
            "branches": branches,
            "tags": tags,
            "refs": branches + tags,
            "feature_sources": feature_sources,
        }

    def resident_package(self, tag: str) -> dict[str, Any]:
        tag = require_resident_tag(tag)
        local_package = self.local_resident_package(tag)
        if local_package.get("status") != "pending_or_missing":
            return local_package
        gitlab_package = self.gitlab_resident_package(tag)
        return gitlab_package or local_package

    def local_resident_package(self, tag: str) -> dict[str, Any]:
        artifact_dir = RESIDENT_ARTIFACT_ROOT / tag
        build_info_path = artifact_dir / RESIDENT_BUILD_INFO_FILE
        artifact_path = artifact_dir / RESIDENT_ARTIFACT_FILE
        manifest_path = artifact_dir / "manifest.json"
        if not build_info_path.exists():
            return {
                "ok": True,
                "status": "pending_or_missing",
                "tag": tag,
                "artifact_dir": str(artifact_dir),
                "artifact_path": str(artifact_path),
            }
        build_info = json.loads(build_info_path.read_text(encoding="utf-8"))
        package = dict(build_info)
        package.setdefault("tag", tag)
        package.setdefault("artifact_dir", str(artifact_dir))
        package.setdefault("artifact_path", str(artifact_path))
        package.setdefault("status", "ready")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                package.update({key: value for key, value in manifest.items() if key not in {"status"}})
                if package.get("cloud_dir"):
                    package["artifact_path"] = package["cloud_dir"]
                package.setdefault("artifact_source", "cloud")
                package.setdefault("message", "resident 包已发布到云盘")
        return {"ok": True, **package}

    def schedules(self) -> dict[str, Any]:
        data = self.release_tasks()
        return {"ok": True, "schedules": data["tasks"], "runs": data["runs"], "tasks": data["tasks"]}

    def release_tasks(self) -> dict[str, Any]:
        return {"ok": True, "tasks": load_release_tasks(), "runs": load_release_runs()}

    def save_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.save_release_task(payload)

    def save_release_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = normalize_release_task(payload)
        tasks = load_release_tasks()
        next_tasks = [item for item in tasks if item["id"] != task["id"]]
        next_tasks.append(task)
        save_release_tasks(sorted(next_tasks, key=lambda item: item["id"]))
        return {"ok": True, "task": task, "schedule": task, "tasks": load_release_tasks(), "runs": load_release_runs()}

    def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self.delete_release_task(schedule_id)

    def delete_release_task(self, task_id: str) -> dict[str, Any]:
        task_id = require_schedule_id(task_id)
        tasks = load_release_tasks()
        next_tasks = [item for item in tasks if item["id"] != task_id]
        if len(next_tasks) == len(tasks):
            raise ValueError(f"自动任务不存在：{task_id}")
        save_release_tasks(next_tasks)
        runs = [item for item in load_release_runs() if item.get("task_id") != task_id and item.get("schedule_id") != task_id]
        save_release_runs(runs)
        return {"ok": True, "deleted": task_id, "tasks": load_release_tasks(), "runs": load_release_runs()}

    def delete_release_run(self, run_id: str) -> dict[str, Any]:
        run_id = str(run_id or "").strip()
        if not run_id:
            raise ValueError("发版运行 ID 不能为空")
        runs = load_release_runs()
        next_runs = [item for item in runs if item.get("id") != run_id]
        if len(next_runs) == len(runs):
            raise ValueError(f"发版运行不存在：{run_id}")
        save_release_runs(next_runs)
        return {"ok": True, "deleted": run_id, "runs": load_release_runs()}

    def clear_release_runs(self) -> dict[str, Any]:
        deleted_count = len(load_release_runs())
        save_release_runs([])
        return {"ok": True, "deleted_count": deleted_count, "runs": []}

    def schedule_runs(self, schedule_id: str) -> dict[str, Any]:
        schedule_id = require_schedule_id(schedule_id)
        runs = [item for item in load_release_runs() if item.get("task_id") == schedule_id or item.get("schedule_id") == schedule_id]
        return {"ok": True, "schedule_id": schedule_id, "runs": sorted(runs, key=lambda item: item.get("started_at", ""), reverse=True)}

    def schedule_dry_run(self, schedule_id: str, now: str | None = None) -> dict[str, Any]:
        task = self.get_release_task(schedule_id)
        return {"ok": True, "schedule": task, "task": task, "plan": self.resolve_release_plan(task, now)}

    def schedule_run_now(self, schedule_id: str, now: str | None = None) -> dict[str, Any]:
        task = self.get_release_task(schedule_id)
        plan = self.resolve_release_plan(task, now)
        run = self.start_full_release_run(task, plan, trigger="manual")
        return {"ok": True, "schedule": task, "task": task, "plan": plan, "run": run}

    def manual_release_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        task = normalize_release_task(
            {
                **DEFAULT_SCHEDULE,
                "id": str(payload.get("task_id") or "manual-release"),
                "enabled": False,
                "name": str(payload.get("name") or "手动完整发版构建"),
                "default_ref": str(payload.get("source_ref") or payload.get("default_ref") or "fix"),
                "config_ref": str(payload.get("config_ref") or ""),
                "config_matrix_enabled": payload.get("config_matrix_enabled", DEFAULT_SCHEDULE["config_matrix_enabled"]),
                "config_matrix": payload.get("config_matrix", DEFAULT_SCHEDULE["config_matrix"]),
                "source_ref_strategy": "fixed_ref",
                "version_source": str(payload.get("version_source") or "simos_version_info"),
                "manual_version_number": str(payload.get("manual_version_number") or payload.get("manual_version") or ""),
                "version_prefix_mode": str(payload.get("version_prefix_mode") or "auto"),
                "manual_version_prefix": str(payload.get("manual_version_prefix") or payload.get("version_prefix") or "V"),
                "cloud_category": str(payload.get("cloud_category") or DEFAULT_SCHEDULE["cloud_category"]),
            }
        )
        plan = self.resolve_release_plan(task, str(payload.get("now") or "") or None)
        run = self.start_full_release_run(task, plan, trigger="manual")
        return {"ok": True, "task": task, "plan": plan, "run": run}

    def rerun_tag_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        tag = require_resident_tag(str(payload.get("tag_name") or payload.get("tag") or ""))
        now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
        package = self.resident_package(tag)
        status = "published" if package.get("status") in {"ready", "success"} else "building"
        run = {
            "id": new_release_run_id("rerun"),
            "task_id": "",
            "schedule_id": "",
            "trigger": "rerun_tag",
            "execution_type": "rerun_existing_tag",
            "status": status,
            "tag_name": tag,
            "source_ref": tag,
            "started_at": now,
            "updated_at": now,
            "finished_at": now if status == "published" else "",
            "package": package,
            "cloud_dir": package.get("cloud_dir") or package.get("artifact_path") or "",
            "pipeline_url": package.get("pipeline_url") or package.get("job_url") or "",
        }
        append_release_run(run)
        return {"ok": True, "run": run}

    def continue_release_run(self, run_id: str) -> dict[str, Any]:
        runs = load_release_runs()
        run = next((item for item in runs if item.get("id") == run_id), None)
        if run is None:
            raise ValueError(f"发版运行不存在：{run_id}")
        if run.get("execution_type") != "full_release":
            raise ValueError("只有完整发版运行可以继续")
        payload = dict(run.get("create_tag_payload") or {})
        if not payload:
            raise ValueError("运行记录缺少继续发版所需参数")
        result = self.create_tag(payload)
        updated = self.release_run_from_result(run.get("task") or {}, run.get("plan") or {}, result, str(run.get("trigger") or "manual"), run_id=run_id)
        save_release_runs([updated if item.get("id") == run_id else item for item in runs])
        return {"ok": True, "run": updated, "result": result}

    def run_due_schedules(self, now: datetime | None = None) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for task in load_release_tasks():
            if not task.get("enabled", False):
                continue
            local_now = coerce_schedule_now(task, now)
            due_key = schedule_due_key(task, local_now)
            if not due_key or schedule_already_ran(task["id"], due_key):
                continue
            try:
                result = self.schedule_run_now(task["id"], local_now.isoformat())
                result["run"]["due_key"] = due_key
                result["run"]["trigger"] = "schedule"
                append_release_run(result["run"])
                runs.append(result["run"])
            except Exception as exc:
                run = {
                    "id": new_release_run_id("failed"),
                    "task_id": task["id"],
                    "schedule_id": task["id"],
                    "trigger": "schedule",
                    "execution_type": "full_release",
                    "status": "failed",
                    "error": str(exc),
                    "due_key": due_key,
                    "started_at": local_now.isoformat(),
                    "finished_at": datetime.now(ZoneInfo(task.get("timezone", "Asia/Shanghai"))).isoformat(),
                }
                append_release_run(run)
                runs.append(run)
        return runs

    def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        return self.get_release_task(schedule_id)

    def get_release_task(self, schedule_id: str) -> dict[str, Any]:
        schedule_id = require_schedule_id(schedule_id)
        for schedule in load_release_tasks():
            if schedule["id"] == schedule_id:
                return schedule
        raise ValueError(f"自动任务不存在：{schedule_id}")

    def resolve_schedule_plan(self, schedule: dict[str, Any], now: str | None = None) -> dict[str, Any]:
        return self.resolve_release_plan(schedule, now)

    def resolve_release_plan(self, schedule: dict[str, Any], now: str | None = None) -> dict[str, Any]:
        target = self.optional_simos_target()
        if target is None:
            raise ValueError("发版任务需要启用 simos 仓库")
        local_now = coerce_schedule_now(schedule, parse_schedule_now(now) if now else None)
        ref = self.resolve_schedule_ref(schedule, target)
        version_prefix = resolve_version_prefix(ref, schedule)
        version_number = self.resolve_release_version_number(schedule, ref, version_prefix)
        if str(schedule.get("version_source") or "simos_version_info") == "manual":
            weekly_version = {
                "version_number": version_number,
                "requires_confirmation": False,
                "reason": "manual_version",
                "week": week_key(local_now),
            }
        else:
            weekly_version = resolve_weekly_version_policy(target.client.tag_names(), version_number, local_now)
            version_number = weekly_version["version_number"]
        release_version = apply_version_prefix(version_number, version_prefix)
        stamp = local_now.strftime("%Y%m%d%H%M")
        source_ref_slug = tag_source_name(ref)
        tag_name = default_tag_name(ref, release_version, stamp)
        cloud_date = local_now.strftime("%Y-%m-%d")
        cloud_category = str(schedule.get("cloud_category") or DEFAULT_SCHEDULE["cloud_category"]).strip("/")
        config_matrix = resolve_config_matrix(schedule)
        config_ref = config_matrix[0]["config_ref"] if config_matrix else self.resolve_config_ref(schedule)
        plan = {
            "schedule_id": schedule["id"],
            "task_id": schedule["id"],
            "repository_id": target.repo.id,
            "project": target.repo.project,
            "ref": ref,
            "source_ref": ref,
            "config_ref": config_ref,
            "source_ref_slug": source_ref_slug,
            "version": release_version,
            "version_number": version_number,
            "version_prefix": version_prefix,
            "release_version": release_version,
            "tag_name": tag_name,
            "message": release_tag_message("resident release build", cloud_category, config_matrix, config_ref),
            "cloud_category": cloud_category,
            "config_ref": config_ref,
            "config_matrix_enabled": bool(schedule.get("config_matrix_enabled", True)),
            "config_matrix": config_matrix,
            "config_refs": [item["config_ref"] for item in config_matrix],
            "config_matrix_value": config_matrix_value(config_matrix),
            "cloud_dir": f"/public/Versions/{cloud_date}_{release_version}/{cloud_category}",
            "planned_at": local_now.isoformat(),
            "repositories": [repo.public_dict(self.token_loaded(repo)) for repo in self.release_repositories()],
            "weekly_version": weekly_version,
        }
        if weekly_version.get("requires_confirmation"):
            plan["requires_weekly_version_confirmation"] = True
            plan["confirmation_message"] = "周五首次发版需要人工确认第三位版本号变更"
        return plan

    def resolve_schedule_ref(self, schedule: dict[str, Any], target: OperationTarget) -> str:
        strategy = str(schedule.get("source_ref_strategy") or schedule.get("ref_strategy") or "fixed_ref")
        if strategy == "latest_fix_rc":
            version = str(schedule.get("manual_version_number") or schedule.get("manual_version") or "").strip()
            branches = target.client.branch_names()
            prefix = f"fix/{version}-rc" if version else "fix/"
            candidates = [name for name in branches if name.startswith(prefix)]
            if not candidates:
                raise ValueError(f"未找到匹配的 fix RC 分支：{prefix}")
            return sorted(candidates, key=natural_ref_key)[-1]
        ref = str(schedule.get("default_ref") or "fix").strip()
        require_ref_name(ref, "自动任务来源 ref")
        if ref not in set(target.client.branch_names()) | set(target.client.tag_names()):
            raise ValueError(f"自动任务来源 ref 不存在：{ref}")
        return ref

    @staticmethod
    def resolve_config_ref(schedule: dict[str, Any]) -> str:
        config_ref = str(schedule.get("config_ref") or "").strip()
        return require_ref_name(config_ref, "config 分支") if config_ref else ""

    def resolve_schedule_version(self, schedule: dict[str, Any], target: OperationTarget, ref: str) -> str:
        source = str(schedule.get("version_source") or "simos_version_info")
        if source == "manual":
            version = normalize_version_number(str(schedule.get("manual_version_number") or schedule.get("manual_version") or ""))
            if not version:
                raise ValueError("自动任务使用手动版本时必须填写版本号")
            return version
        return normalize_version_number(self.version_from_version_info(target, ref))

    def resolve_release_version_number(self, schedule: dict[str, Any], ref: str, version_prefix: str) -> str:
        if str(schedule.get("version_source") or "simos_version_info") == "manual":
            return self.resolve_schedule_version(schedule, self.optional_simos_target() or self.target("simos"), ref)
        version = self.default_tag_version_for_request(
            {"scope": "all", "version_prefix": version_prefix},
            ref,
            True,
            "",
        )
        return normalize_version_number(version)

    def create_schedule_tag(self, schedule: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        return self.start_full_release_run(schedule, plan, trigger="manual")

    def start_full_release_run(self, task: dict[str, Any], plan: dict[str, Any], trigger: str) -> dict[str, Any]:
        payload = {
            "scope": "all",
            "ref": plan["source_ref"],
            "tag_name": plan["tag_name"],
            "message": plan["message"],
            "update_version": True,
            "base_version": plan["version_number"],
            "base_version_is_final": True,
            "version_prefix": plan["version_prefix"],
        }
        if bool(plan.get("config_matrix_enabled", True)) and not plan.get("config_matrix"):
            result = {
                "ok": False,
                "operation": "create_tag",
                "phase": "precheck",
                "message": "缺少 config 构建配置，请至少选择一个 OS/config 分支",
                "tag_name": plan.get("tag_name", ""),
                "results": [],
            }
            run = self.release_run_from_result(task, plan, result, trigger)
            append_release_run(run)
            return run
        if plan.get("requires_weekly_version_confirmation") and not plan.get("weekly_version_confirmed"):
            result = {
                "ok": False,
                "operation": "create_tag",
                "phase": "waiting_weekly_version_confirmation",
                "blocked": True,
                "tag_name": plan["tag_name"],
                "message": plan.get("confirmation_message") or "等待人工确认周版本号",
            }
            run = self.release_run_from_result(task, plan, result, trigger)
            append_release_run(run)
            return run
        result = self.create_tag(payload)
        run = self.release_run_from_result(task, plan, result, trigger)
        append_release_run(run)
        return run

    def release_run_from_result(
        self,
        task: dict[str, Any],
        plan: dict[str, Any],
        result: dict[str, Any],
        trigger: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(ZoneInfo(str(task.get("timezone") or "Asia/Shanghai"))).isoformat()
        phase = str(result.get("phase") or "")
        if result.get("ok"):
            status = "building"
        elif phase == "waiting_weekly_version_confirmation":
            status = "waiting_weekly_version_confirmation"
        elif phase == "waiting_version_mr":
            status = "waiting_version_mr"
        elif phase == "precheck":
            status = "precheck_failed"
        else:
            status = "failed"
        run = {
            "id": run_id or new_release_run_id("release"),
            "task_id": task.get("id", ""),
            "schedule_id": task.get("id", ""),
            "trigger": trigger,
            "execution_type": "full_release",
            "status": status,
            "source_ref": plan.get("source_ref") or plan.get("ref"),
            "config_ref": plan.get("config_ref", ""),
            "config_matrix": plan.get("config_matrix", []),
            "config_matrix_value": plan.get("config_matrix_value", ""),
            "source_ref_slug": plan.get("source_ref_slug", ""),
            "version_number": plan.get("version_number", ""),
            "version_prefix": plan.get("version_prefix", ""),
            "release_version": plan.get("release_version") or plan.get("version", ""),
            "version": plan.get("release_version") or plan.get("version", ""),
            "tag_name": plan.get("tag_name", ""),
            "cloud_dir": plan.get("cloud_dir", ""),
            "started_at": plan.get("planned_at", now),
            "updated_at": now,
            "finished_at": now if status in {"failed", "precheck_failed"} else "",
            "phase": phase,
            "result": result,
            "task": task,
            "plan": plan,
            "create_tag_payload": {
                "scope": "all",
                "ref": plan.get("source_ref") or plan.get("ref"),
                "tag_name": plan.get("tag_name"),
                "message": plan.get("message"),
                "update_version": True,
                "base_version": plan.get("version_number"),
                "base_version_is_final": True,
                "version_prefix": plan.get("version_prefix"),
            },
        }
        version_update = result.get("version_update") or {}
        if isinstance(version_update, dict):
            run["version_update_branch"] = version_update.get("branch", "")
        merge_request = result.get("merge_request") or version_update.get("merge_request") if isinstance(version_update, dict) else None
        if merge_request:
            run["merge_request"] = merge_request
        if not result.get("ok") and result.get("message"):
            run["error"] = result.get("message")
        return run

    def gitlab_resident_package(self, tag: str) -> dict[str, Any] | None:
        target = self.optional_simos_target()
        if target is None or not self.token_loaded(target.repo):
            return None
        pipelines = getattr(target.client, "pipelines", None)
        pipeline_jobs = getattr(target.client, "pipeline_jobs", None)
        artifact_url = getattr(target.client, "job_artifact_file_url", None)
        if not callable(pipelines) or not callable(pipeline_jobs) or not callable(artifact_url):
            return None
        try:
            pipeline = self.select_tag_pipeline(target.client.pipelines(ref=tag), tag)
        except GitLabError as exc:
            return self.resident_gitlab_error(tag, str(exc))
        if pipeline is None:
            return None

        pipeline_status = str(pipeline.get("status", ""))
        pipeline_url = str(pipeline.get("web_url", ""))
        pipeline_id = pipeline.get("id")
        if not pipeline_id:
            return self.resident_gitlab_status(tag, pipeline_status or "pending_or_missing", pipeline_url, message="未读取到 Tag Pipeline 编号")
        try:
            jobs = target.client.pipeline_jobs(pipeline_id)
        except GitLabError as exc:
            return self.resident_gitlab_error(tag, str(exc), pipeline_url=pipeline_url)

        job = self.select_resident_artifact_job(jobs)
        if job is not None:
            job_status = str(job.get("status", ""))
            job_url = str(job.get("web_url", ""))
            job_id = job.get("id")
            package = {
                "ok": True,
                "tag": tag,
                "pipeline": pipeline,
                "pipeline_url": pipeline_url,
                "job": job,
                "job_url": job_url,
                "artifact_source": "gitlab",
                "artifact_name": RESIDENT_ARTIFACT_FILE,
                "artifact_path": target.client.job_artifact_file_url(job_id, RESIDENT_ARTIFACT_FILE) if job_id else "",
                "artifact_url": target.client.job_artifact_file_url(job_id, RESIDENT_ARTIFACT_FILE) if job_id else "",
                "built_at": job.get("finished_at") or pipeline.get("updated_at") or pipeline.get("created_at") or "",
            }
            package.update(self.load_gitlab_build_info(target.client, job_id))
            if job_status == "success":
                package.setdefault("status", "success")
                package.setdefault("message", "resident 包已上传到 GitLab job artifacts")
                return package
            if job_status in {"failed", "canceled"}:
                package["status"] = "failed"
                package.setdefault("message", f"resident 产物 Job 状态：{job_status}")
                return package

        if pipeline_status == "success":
            return self.resident_gitlab_status(
                tag,
                "failed",
                pipeline_url,
                message=f"Tag Pipeline 已成功，但未找到 GitLab artifact：{RESIDENT_ARTIFACT_FILE}。请在目标仓库对应 job 的 artifacts.paths 中上传该文件",
            )
        if pipeline_status in {"failed", "canceled"}:
            return self.resident_gitlab_status(tag, "failed", pipeline_url, message=f"Tag Pipeline 状态：{pipeline_status}")
        return self.resident_gitlab_status(tag, "pending_or_missing", pipeline_url, message="正在等待云端 Runner 生成并上传 resident artifact")

    @staticmethod
    def select_tag_pipeline(pipelines: list[dict[str, Any]], tag: str) -> dict[str, Any] | None:
        items = [item for item in pipelines if str(item.get("ref", "")) == tag] or list(pipelines)
        if not items:
            return None
        def pipeline_score(item: dict[str, Any]) -> tuple[int, int]:
            source = str(item.get("source", ""))
            return (1 if source == "push" else 0, int(item.get("id") or 0))
        return max(items, key=pipeline_score)

    @staticmethod
    def select_resident_artifact_job(jobs: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for job in jobs:
            artifacts_file = job.get("artifacts_file") or {}
            filename = str(artifacts_file.get("filename", "")).strip()
            artifacts = job.get("artifacts") or []
            if not filename and not artifacts:
                continue
            name = str(job.get("name", "")).strip().lower()
            score = 0
            if name in RESIDENT_ARTIFACT_JOB_NAMES:
                score += 100
            elif any(pref in name for pref in RESIDENT_ARTIFACT_JOB_NAMES):
                score += 60
            if str(job.get("status", "")) == "success":
                score += 20
            if filename:
                score += 10
            candidates.append((score, int(job.get("id") or 0), job))
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item[0], item[1]))[2]

    @staticmethod
    def load_gitlab_build_info(client: GitLabClient, job_id: Any) -> dict[str, Any]:
        if not job_id:
            return {}
        loader = getattr(client, "job_artifact_file_text", None)
        if not callable(loader):
            return {}
        try:
            payload = client.job_artifact_file_text(job_id, RESIDENT_BUILD_INFO_FILE)
        except GitLabError as exc:
            if exc.status == 404:
                return {}
            return {"error": str(exc)}
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {"build_info_error": f"{RESIDENT_BUILD_INFO_FILE} 不是合法 JSON"}
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def resident_gitlab_status(tag: str, status: str, pipeline_url: str = "", message: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "tag": tag,
            "status": status,
            "pipeline_url": pipeline_url,
            "message": message,
        }

    @staticmethod
    def resident_gitlab_error(tag: str, error: str, pipeline_url: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "tag": tag,
            "status": "error",
            "pipeline_url": pipeline_url,
            "error": error,
        }

    def create_release(self, payload: dict[str, Any]) -> dict[str, Any]:
        ref = require_ref_name(str(payload.get("ref", "")), "来源分支或Tag")
        branch = "release"

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            refs = set(branch_names) | set(target.client.tag_names())
            if ref not in refs:
                raise ValueError(f"来源不存在：{ref}")
            if branch in branch_names:
                raise ValueError("release 分支已存在")
            return {"branch": branch, "ref": ref}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, **self.create_protected_branch(target, branch, ref, "release")}

        return self.run_operation(payload, "create_release", precheck, execute)

    def create_feature(self, payload: dict[str, Any]) -> dict[str, Any]:
        ticket = str(payload.get("ticket", ""))
        desc = str(payload.get("desc", ""))
        ref = require_ref_name(str(payload.get("ref", "release") or "release"), "来源分支")
        if ref != "release" and classify_branch(ref) != "bugfix":
            raise ValueError("Feature 分支只能从 release、bugfix/<版本号> 或迁移期 fix 拉出")
        branch = feature_branch(ticket, desc, ref)

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            if ref not in branch_names:
                raise ValueError(f"来源分支不存在：{ref}")
            if branch in branch_names:
                raise ValueError(f"目标 feature 已存在：{branch}")
            return {"branch": branch, "ref": ref}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, "created": target.client.create_branch(branch, ref)}

        return self.run_operation(payload, "create_feature", precheck, execute)

    def create_bugfix(self, payload: dict[str, Any]) -> dict[str, Any]:
        version = require_version(str(payload.get("version", "")))
        ref = require_ref_name(str(payload.get("ref", "release") or "release"), "来源分支或Tag")
        branch = bugfix_branch(version)

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            refs = set(branch_names) | set(target.client.tag_names())
            if ref not in refs:
                raise ValueError(f"来源不存在：{ref}")
            if branch in branch_names:
                raise ValueError(f"目标 bugfix 分支已存在：{branch}")
            return {"branch": branch, "ref": ref, "version": version}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            return {**context, **self.create_protected_branch(target, context["branch"], context["ref"], "bugfix")}

        return self.run_operation(payload, "create_bugfix", precheck, execute)

    def create_tag(self, payload: dict[str, Any]) -> dict[str, Any]:
        ref = require_ref_name(str(payload.get("ref", "")), "Tag 来源")
        update_version = truthy(payload.get("update_version", False))
        base_version = normalize_optional_version(str(payload.get("base_version", ""))) if update_version else ""
        base_version_is_final = truthy(payload.get("base_version_is_final", payload.get("version_number_is_final", False))) if update_version else False
        requested_version_prefix = normalize_version_prefix(str(payload.get("version_prefix") or resolve_version_prefix(ref))) if update_version else ""
        scope = str(payload.get("scope", "single")).strip() or "single"
        if update_version and scope != "all":
            raise ValueError("打 Tag 前更新版本号只能在全部启用仓库范围使用")
        raw_tag_name = str(payload.get("tag_name", "")).strip()
        tag_name = require_ref_name(raw_tag_name or self.default_tag_name_for_request(payload, ref, update_version, base_version), "Tag 名称")
        message = str(payload.get("message", "")).strip() or f"Tag {tag_name} from {ref}"

        def precheck(target: OperationTarget) -> dict[str, Any]:
            branch_names = target.client.branch_names()
            tag_names = target.client.tag_names()
            refs = set(branch_names) | set(tag_names)
            target_ref = ref
            context: dict[str, Any] = {"source_ref": ref, "tag_name": tag_name, "message": message, "update_version": update_version}
            if target_ref not in refs:
                raise ValueError(f"Tag 来源不存在：{target_ref}")
            if tag_name in tag_names:
                raise ValueError(f"Tag 已存在：{tag_name}")
            context["ref"] = target_ref
            if update_version and is_simos_repo(target.repo):
                if ref not in branch_names:
                    raise ValueError("更新版本号时 Tag 来源必须是分支")
                version_plan = self.plan_version_update(target, ref, tag_name, base_version, requested_version_prefix, base_version_is_final)
                context["_version_plan"] = version_plan
                context["version_update"] = version_plan["summary"]
            return context

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            result: dict[str, Any] = {
                "ref": context["ref"],
                "tag_name": context["tag_name"],
                "message": context["message"],
                "update_version": context["update_version"],
            }
            result["tag"] = target.client.create_tag(context["tag_name"], context["ref"], context["message"])
            return result

        if update_version:
            return self.run_tag_with_simos_version_update(payload, precheck)

        return self.run_operation(payload, "create_tag", precheck, execute)

    def delete_tags(self, payload: dict[str, Any]) -> dict[str, Any]:
        tag_names = parse_tag_names(payload.get("tags", ""))

        def precheck(target: OperationTarget) -> dict[str, Any]:
            return {"tags": tag_names}

        def execute(target: OperationTarget, context: dict[str, Any]) -> dict[str, Any]:
            existing_tags = set(target.client.tag_names())
            deleted_tags: list[dict[str, Any]] = []
            missing_tags: list[str] = []
            for tag_name in context["tags"]:
                if tag_name not in existing_tags:
                    missing_tags.append(tag_name)
                    continue
                deleted_tags.append(target.client.delete_tag(tag_name))
                existing_tags.discard(tag_name)
            return {
                "ok": not missing_tags,
                "requested_tags": context["tags"],
                "deleted_tags": deleted_tags,
                "missing_tags": missing_tags,
                "deleted_count": len(deleted_tags),
            }

        return self.run_operation(payload, "delete_tags", precheck, execute)

    def run_tag_with_simos_version_update(
        self,
        payload: dict[str, Any],
        precheck: Callable[[OperationTarget], dict[str, Any]],
    ) -> dict[str, Any]:
        targets = self.targets(payload)
        precheck_results: list[dict[str, Any]] = []
        contexts: dict[str, dict[str, Any]] = {}
        simos_target: OperationTarget | None = None
        for target in targets:
            if is_simos_repo(target.repo):
                simos_target = target
            try:
                target.client.project()
                context = precheck(target)
                contexts[target.repo.id] = context
                precheck_results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": True, "context": public_context(context)})
            except Exception as exc:
                precheck_results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": False, "error": str(exc)})

        if simos_target is None:
            precheck_results.append({"repository": {"id": "simos", "name": "simos"}, "ok": False, "error": "打 Tag 前更新版本号需要启用 simos 仓库"})

        if any(not item["ok"] for item in precheck_results):
            return {"ok": False, "operation": "create_tag", "phase": "precheck", "precheck": precheck_results, "results": []}

        assert simos_target is not None
        simos_context = contexts[simos_target.repo.id]
        version_plan = self.complete_version_update_plan(simos_context["_version_plan"], targets, contexts)
        simos_context["_version_plan"] = version_plan
        simos_context["version_update"] = version_plan["summary"]
        existing_mr = self.find_version_update_mr(simos_target, version_plan)
        if version_plan["changed"] and is_merge_request_closed(existing_mr):
            version_update = self.version_update_wait_result(version_plan, existing_mr)
            return {
                "ok": False,
                "operation": "create_tag",
                "phase": "version_update_aborted",
                "terminated": True,
                "tag_name": version_plan["tag_name"],
                "message": "版本号更新 MR 已关闭，已终止发版或打 Tag 流程",
                "precheck": precheck_results,
                "version_update": version_update,
                "merge_request": version_update.get("merge_request"),
                "results": [],
            }
        if version_plan["changed"] and not is_merge_request_merged(existing_mr):
            if existing_mr is None:
                version_update = self.create_version_update_merge_request(simos_target, version_plan)
            else:
                version_update = self.version_update_wait_result(version_plan, existing_mr)
            self.save_version_update_default(version_update)
            return {
                "ok": False,
                "operation": "create_tag",
                "phase": "waiting_version_mr",
                "blocked": True,
                "tag_name": version_plan["tag_name"],
                "message": "已创建 simos 版本号更新 MR，请合并后再次创建 Tag",
                "precheck": precheck_results,
                "version_update": version_update,
                "merge_request": version_update.get("merge_request"),
                "results": [],
            }
        results: list[dict[str, Any]] = []
        try:
            version_update = self.commit_version_update(simos_target, version_plan)
        except Exception as exc:
            return {
                "ok": False,
                "operation": "create_tag",
                "phase": "execute",
                "precheck": precheck_results,
                "results": [{"repository": simos_target.repo.public_dict(self.token_loaded(simos_target.repo)), "ok": False, "error": str(exc)}],
            }

        overall_ok = True
        for target in targets:
            context = contexts[target.repo.id]
            tag_ref = version_update.get("tag_ref") if is_simos_repo(target.repo) else context["ref"]
            try:
                result = {
                    "ref": context["ref"],
                    "tag_name": context["tag_name"],
                    "message": context["message"],
                    "update_version": context["update_version"],
                    "tag": target.client.create_tag(context["tag_name"], tag_ref or context["ref"], context["message"]),
                }
                if is_simos_repo(target.repo):
                    self.save_version_update_default(version_update)
                    result["version_update"] = version_update
                results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": True, "result": result})
            except Exception as exc:
                overall_ok = False
                results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": False, "error": str(exc)})
        return {"ok": overall_ok, "operation": "create_tag", "phase": "execute", "precheck": precheck_results, "results": results}

    def plan_version_update(
        self,
        target: OperationTarget,
        ref: str,
        tag_name: str,
        base_version: str = "",
        requested_version_prefix: str = "",
        base_version_is_final: bool = False,
    ) -> dict[str, Any]:
        branch = target.client.branch(ref)
        commit = branch.get("commit") or {}
        source_commit_id = str(commit.get("id") or commit.get("short_id") or "")
        if not source_commit_id:
            raise ValueError(f"无法读取分支 {ref} 的提交信息")
        parent_ids = [str(parent_id) for parent_id in commit.get("parent_ids", [])]

        try:
            previous_version_info = target.client.get_file_text(VERSION_INFO_PATH, ref)
        except GitLabError as exc:
            if exc.status == 404:
                raise ValueError(f"{VERSION_INFO_PATH} 不存在，无法更新版本号") from exc
            raise

        fields = parse_version_info(previous_version_info)
        component = version_component(target.repo)
        previous_version = require_existing_version(fields)
        previous_component_commit = fields.get(f"{component}_commitid", "")
        version_already_current = previous_component_commit == source_commit_id or previous_component_commit in parent_ids
        prefix = normalize_version_prefix(requested_version_prefix or version_prefix(previous_version) or resolve_version_prefix(ref))
        new_version = apply_version_prefix(base_version, prefix) if base_version and base_version_is_final else apply_version_prefix(bump_version(previous_version), prefix)
        summary = {
            "updated": not version_already_current,
            "component": component,
            "previous_version": previous_version,
            "version": new_version,
            "version_number": normalize_version_number(new_version),
            "version_prefix": prefix,
            "previous_commit": previous_component_commit,
            "source_commit": source_commit_id,
            "files": [],
        }
        if base_version:
            summary["base_version"] = base_version
        if base_version_is_final:
            summary["base_version_is_final"] = True
        return {
            "changed": not version_already_current,
            "ref": ref,
            "tag_name": tag_name,
            "version_info_fields": fields,
            "source_parent_ids": parent_ids,
            "base_version": base_version,
            "base_version_is_final": base_version_is_final,
            "version_prefix": prefix,
            "summary": summary,
        }

    def complete_version_update_plan(
        self,
        version_plan: dict[str, Any],
        targets: list[OperationTarget],
        contexts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tag_name = version_plan["tag_name"]
        component_refs = {
            component: {
                "ref": self.version_file_component_ref(target, contexts[target.repo.id], tag_name),
                "commit_id": self.context_commit_id(target, contexts[target.repo.id]),
            }
            for target in targets
            for component in [version_component(target.repo)]
            if component in VERSION_COMPONENTS
        }
        changed_components = [
            component
            for component, item in component_refs.items()
            if component_commit_changed(component, item["commit_id"], version_plan)
        ]
        if not changed_components:
            summary = dict(version_plan["summary"])
            summary["updated"] = False
            summary["reason"] = "all component commit ids already recorded"
            summary["tag_ref"] = summary["source_commit"]
            return {**version_plan, "changed": False, "summary": summary, "component_refs": component_refs}
        forced_version = ""
        if version_plan.get("base_version_is_final") and version_plan.get("base_version"):
            forced_version = apply_version_prefix(str(version_plan["base_version"]), str(version_plan.get("version_prefix") or "V"))
            package_version = ""
        else:
            previous_pkg_info = self.optional_file_text(targets, PKG_INFO_PATH, version_plan["ref"])
            package_version = version_plan.get("base_version") or parse_pkg_info_version(previous_pkg_info)
        new_version, next_version_info = render_version_info_full(
            version_plan["version_info_fields"],
            component_refs,
            current_time,
            changed_components,
            package_version,
            str(version_plan.get("version_prefix") or "V"),
            forced_version,
        )
        actions = [
            {"action": "update", "file_path": VERSION_INFO_PATH, "content": next_version_info},
            {"action": "update", "file_path": SOFTWARE_YAML_PATH, "content": render_software_yaml(new_version, component_refs, current_time)},
        ]
        summary = dict(version_plan["summary"])
        summary["updated"] = True
        summary["version"] = new_version
        summary["version_number"] = normalize_version_number(new_version)
        summary["version_prefix"] = version_prefix(new_version)
        summary["files"] = [action["file_path"] for action in actions]
        summary["changed_components"] = changed_components
        if package_version:
            summary["base_version"] = package_version
        return {**version_plan, "changed": True, "actions": actions, "summary": summary, "component_refs": component_refs}

    def latest_tag_for_source_ref(self, target: OperationTarget, source_ref: str) -> dict[str, Any]:
        source_slug = tag_source_name(source_ref)
        pattern = re.compile(rf"^{re.escape(source_slug)}_[VvFfTt]?\d+(?:\.\d+)+_\d{{12}}$")
        tag_reader = getattr(target.client, "tags", None)
        if callable(tag_reader):
            candidates = [item for item in tag_reader(f"{source_slug}_") if pattern.fullmatch(str(item.get("name") or ""))]
        else:
            candidates = [
                {"name": name}
                for name in target.client.tag_names()
                if pattern.fullmatch(str(name))
            ]
        if not candidates:
            raise ValueError(f"{target.repo.id} 没有找到来源 {source_ref} 对应的发版 Tag")
        return sorted(candidates, key=lambda item: latest_release_tag_key(str(item.get("name") or "")))[-1]

    @staticmethod
    def version_file_component_ref(target: OperationTarget, context: dict[str, Any], tag_name: str) -> str:
        return tag_name

    @staticmethod
    def context_commit_id(target: OperationTarget, context: dict[str, Any]) -> str:
        if "_version_plan" in context:
            return str(context["_version_plan"]["summary"]["source_commit"])
        if context.get("ref_commit_id"):
            return str(context["ref_commit_id"])
        branch = target.client.branch(context["ref"])
        commit = branch.get("commit") or {}
        commit_id = str(commit.get("id") or commit.get("short_id") or "")
        if not commit_id:
            raise ValueError(f"无法读取 {target.repo.id} 分支 {context['ref']} 的提交信息")
        return commit_id

    @staticmethod
    def optional_file_text(targets: list[OperationTarget], file_path: str, ref: str) -> str:
        for target in targets:
            if not is_simos_repo(target.repo):
                continue
            try:
                return target.client.get_file_text(file_path, ref)
            except GitLabError as exc:
                if exc.status == 404:
                    return ""
                raise
        return ""

    @staticmethod
    def version_update_branch(tag_name: str) -> str:
        safe = []
        for ch in tag_name:
            if "A" <= ch <= "Z" or "a" <= ch <= "z" or "0" <= ch <= "9" or ch in {".", "_", "-"}:
                safe.append(ch)
            else:
                safe.append("-")
        name = "".join(safe).strip(".-_") or datetime.now().strftime("%Y%m%d%H%M%S")
        return f"automation/version-info/{name}"

    def find_version_update_mr(self, target: OperationTarget, version_plan: dict[str, Any]) -> dict[str, Any] | None:
        branch = self.version_update_branch(version_plan["tag_name"])
        merge_requests = getattr(target.client, "merge_requests", None)
        if callable(merge_requests):
            mrs = merge_requests(branch, version_plan["ref"], state="all")
        else:
            mrs = target.client.opened_merge_requests(branch, version_plan["ref"])
        if mrs:
            return mrs[0]
        return None

    def create_version_update_merge_request(self, target: OperationTarget, version_plan: dict[str, Any]) -> dict[str, Any]:
        branch = self.version_update_branch(version_plan["tag_name"])
        commit_message = f"Update version info before tag {version_plan['tag_name']}"
        if isinstance(target.client, GitLabClient) and shutil.which("git"):
            commit = self.commit_version_update_with_git(target, version_plan, branch, commit_message)
        else:
            target.client.create_branch(branch, version_plan["ref"])
            commit = target.client.create_commit(branch, commit_message, version_plan["actions"])
        merge_request = target.client.create_merge_request(branch, version_plan["ref"], commit_message)
        return {
            **dict(version_plan["summary"]),
            "branch": branch,
            "commit": commit,
            "merge_request": merge_request,
        }

    def commit_version_update_with_git(
        self,
        target: OperationTarget,
        version_plan: dict[str, Any],
        branch: str,
        commit_message: str,
    ) -> dict[str, Any]:
        repo_url = self.project_git_url(target.client)
        component_refs = version_plan.get("component_refs") or {}
        with tempfile.TemporaryDirectory(prefix="gitops-version-") as tmp:
            tmp_path = Path(tmp)
            repo_dir = tmp_path / "repo"
            askpass = tmp_path / "git-askpass.sh"
            askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$GITOPS_GIT_PASSWORD\"\n", encoding="utf-8")
            askpass.chmod(0o700)
            git_env = {
                "GIT_ASKPASS": str(askpass),
                "GIT_TERMINAL_PROMPT": "0",
                "GITOPS_GIT_PASSWORD": target.client.config.token,
            }
            self.run_git(["clone", "--depth", "1", "--branch", version_plan["ref"], repo_url, str(repo_dir)], cwd=None, env=git_env)
            self.run_git(["checkout", "-B", branch], cwd=str(repo_dir), env=git_env)
            for action in version_plan["actions"]:
                path = repo_dir / action["file_path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(action["content"], encoding="utf-8")
            for component, path in submodule_update_paths(component_refs).items():
                commit_id = component_refs[component]["commit_id"]
                self.run_git(["update-index", "--add", "--cacheinfo", "160000", commit_id, path], cwd=str(repo_dir), env=git_env)
            self.run_git(["config", "user.name", "GitOps Workbench"], cwd=str(repo_dir), env=git_env)
            self.run_git(["config", "user.email", "gitops-workbench@local"], cwd=str(repo_dir), env=git_env)
            self.run_git(["add", VERSION_INFO_PATH, SOFTWARE_YAML_PATH], cwd=str(repo_dir), env=git_env)
            self.run_git(["commit", "-m", commit_message], cwd=str(repo_dir), env=git_env)
            commit_id = self.run_git(["rev-parse", "HEAD"], cwd=str(repo_dir), env=git_env).strip()
            self.run_git(["push", "origin", f"HEAD:{branch}"], cwd=str(repo_dir), env=git_env)
        return {"id": commit_id, "short_id": commit_id[:12], "branch": branch}

    @staticmethod
    def run_git(args: list[str], cwd: str | None, env: dict[str, str] | None = None) -> str:
        run_env = os.environ.copy()
        if env:
            run_env.update(env)
        try:
            completed = subprocess.run(
                ["git", *args],
                cwd=cwd,
                env=run_env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            return completed.stdout
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip().splitlines()[-3:]
            raise RuntimeError(f"Git 命令失败：git {args[0]}；{'; '.join(stderr)}") from exc

    @staticmethod
    def project_git_url(client: GitLabClient) -> str:
        base = client.config.base_url.rstrip("/")
        project = client.config.project.strip("/")
        token = client.config.token
        if not token:
            raise ValueError("缺少 GitLab Token，无法推送版本号 MR 分支")
        parsed = urlparse(base)
        netloc = f"oauth2@{parsed.netloc}"
        return urlunparse((parsed.scheme, netloc, f"/{project}.git", "", "", ""))

    def version_update_wait_result(self, version_plan: dict[str, Any], merge_request: dict[str, Any]) -> dict[str, Any]:
        return {
            **dict(version_plan["summary"]),
            "branch": self.version_update_branch(version_plan["tag_name"]),
            "merge_request": merge_request,
        }

    def save_version_update_default(self, version_update: dict[str, Any]) -> None:
        base_version = normalize_optional_version(str(version_update.get("base_version", "")))
        if base_version:
            save_version_settings({"base_version": base_version})

    def commit_version_update(self, target: OperationTarget, version_plan: dict[str, Any]) -> dict[str, Any]:
        summary = dict(version_plan["summary"])
        if not version_plan["changed"]:
            summary["commit_id"] = summary["source_commit"]
            summary["tag_ref"] = summary["source_commit"]
            return summary
        existing_mr = self.find_version_update_mr(target, version_plan)
        if is_merge_request_merged(existing_mr):
            branch = target.client.branch(version_plan["ref"])
            commit = branch.get("commit") or {}
            commit_id = str(commit.get("id") or commit.get("short_id") or "")
            if not commit_id:
                raise ValueError("版本信息 MR 已合并但未读取到 release 分支 commit id，无法继续打 Tag")
            summary["commit"] = commit
            summary["commit_id"] = commit_id
            summary["tag_ref"] = commit_id
            summary["merge_request"] = existing_mr
            return summary

        commit_message = f"Update version info before tag {version_plan['tag_name']}"
        try:
            commit = target.client.create_commit(version_plan["ref"], commit_message, version_plan["actions"])
        except GitLabError as exc:
            if not self.should_fallback_to_file_update(exc, version_plan):
                raise
            return self.commit_version_update_with_file_api(target, version_plan, summary, commit_message)

        commit_id = str(commit.get("id") or commit.get("short_id") or "")
        if not commit_id:
            raise ValueError("版本信息提交成功但未返回 commit id，无法继续打 Tag")
        summary["commit"] = commit
        summary["commit_id"] = commit_id
        summary["tag_ref"] = commit_id
        return summary

    @staticmethod
    def should_fallback_to_file_update(exc: GitLabError, version_plan: dict[str, Any]) -> bool:
        actions = version_plan.get("actions") or []
        return (
            exc.status is not None
            and exc.status >= 500
            and len(actions) == 1
            and actions[0].get("action") == "update"
            and actions[0].get("file_path") == VERSION_INFO_PATH
        )

    def commit_version_update_with_file_api(
        self,
        target: OperationTarget,
        version_plan: dict[str, Any],
        summary: dict[str, Any],
        commit_message: str,
    ) -> dict[str, Any]:
        action = version_plan["actions"][0]
        file_update = target.client.update_file(VERSION_INFO_PATH, version_plan["ref"], action["content"], commit_message)
        branch = target.client.branch(version_plan["ref"])
        commit = branch.get("commit") or {}
        commit_id = str(commit.get("id") or commit.get("short_id") or "")
        if not commit_id:
            raise ValueError("版本信息 fallback 更新成功但未读取到分支 commit id，无法继续打 Tag")
        summary["commit"] = commit
        summary["commit_id"] = commit_id
        summary["tag_ref"] = commit_id
        summary["fallback"] = {
            "method": "repository_file_update",
            "file_update": file_update,
        }
        return summary

    def default_tag_name_for_request(self, payload: dict[str, Any], ref: str, update_version: bool, base_version: str) -> str:
        version = self.default_tag_version_for_request(payload, ref, update_version, base_version)
        return default_tag_name(ref, version)

    def default_tag_version_for_request(self, payload: dict[str, Any], ref: str, update_version: bool, base_version: str) -> str:
        targets = self.targets(payload)
        simos_target = next((target for target in targets if is_simos_repo(target.repo)), None) or self.optional_simos_target()
        requested_prefix = normalize_version_prefix(str(payload.get("version_prefix") or resolve_version_prefix(ref))) if update_version else ""
        if update_version:
            if simos_target is None:
                raise ValueError("打 Tag 前更新版本号需要启用 simos 仓库")
            version_plan = self.plan_version_update(simos_target, ref, "<auto>", base_version, requested_prefix)
            contexts: dict[str, dict[str, Any]] = {}
            for target in targets:
                context: dict[str, Any] = {"ref": ref}
                if target.repo.id == simos_target.repo.id:
                    context["_version_plan"] = version_plan
                contexts[target.repo.id] = context
            completed_plan = self.complete_version_update_plan(version_plan, targets, contexts)
            version = normalize_optional_version(str(completed_plan["summary"].get("version") or completed_plan["summary"].get("previous_version") or ""))
            if version:
                return version

        primary_target = self.target_for_tag_version(targets, payload)
        if primary_target is not None:
            version = self.version_from_version_info(primary_target, ref)
            if version:
                return version
        if simos_target is not None and (primary_target is None or primary_target.repo.id != simos_target.repo.id):
            version = self.version_from_version_info(simos_target, ref)
            if version:
                return version

        saved_version = load_version_settings().get("base_version", "")
        if saved_version:
            return saved_version

        hinted_version = version_hint_from_ref(ref)
        if hinted_version:
            return hinted_version

        raise ValueError("无法自动生成 Tag 名称：未识别到版本号，请手动填写 Tag 名称或先设置本周基线版本")

    @staticmethod
    def target_for_tag_version(targets: list[OperationTarget], payload: dict[str, Any]) -> OperationTarget | None:
        simos_target = next((target for target in targets if is_simos_repo(target.repo)), None)
        scope = str(payload.get("scope", "single")).strip() or "single"
        if scope == "all" and simos_target is not None:
            return simos_target

        repository_id = str(payload.get("repository_id", "")).strip()
        if repository_id:
            for target in targets:
                if target.repo.id == repository_id:
                    return target

        return simos_target or (targets[0] if targets else None)

    def optional_simos_target(self) -> OperationTarget | None:
        for repo in self.store.enabled():
            if is_simos_repo(repo):
                return OperationTarget(repo, self.client_for(repo))
        return None

    @staticmethod
    def version_from_version_info(target: OperationTarget, ref: str) -> str:
        try:
            version_info_text = target.client.get_file_text(VERSION_INFO_PATH, ref)
        except GitLabError as exc:
            if exc.status == 404:
                return ""
            raise
        return normalize_optional_version(require_existing_version(parse_version_info(version_info_text)))

    def create_protected_branch(self, target: OperationTarget, branch: str, ref: str, kind: str) -> dict[str, Any]:
        created = target.client.create_branch(branch, ref)
        protection = BRANCH_PROTECTION[kind]
        protected = target.client.protect_branch(branch, **protection)
        return {"created": created, "protected": protected, "protection": protection}

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
                precheck_results.append({"repository": target.repo.public_dict(self.token_loaded(target.repo)), "ok": True, "context": public_context(context)})
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

    def release_repositories(self) -> list[RepositoryConfig]:
        return [repo for repo in self.store.enabled() if not is_config_repo(repo)]

    def targets(self, payload: dict[str, Any]) -> list[OperationTarget]:
        scope = str(payload.get("scope", "single")).strip() or "single"
        if scope == "all":
            repos = self.release_repositories()
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
    groups = {key: [] for key in ("release", "feature", "bugfix", "other")}
    for branch in branches:
        groups.setdefault(branch["kind"], []).append(branch)
    return groups


def parse_tag_names(value: Any) -> list[str]:
    tags: list[str] = []
    values = value if isinstance(value, list) else [value]
    for item in values:
        normalized = str(item or "").replace(",", chr(10))
        for raw in normalized.splitlines():
            tag = raw.strip()
            if tag and tag not in tags:
                tags.append(require_ref_name(tag, "Tag 名称"))
    if not tags:
        raise ValueError("请至少输入一个 Tag")
    return tags


def require_resident_tag(value: str) -> str:
    tag = value.strip()
    if not tag:
        raise ValueError("缺少 Tag 名称")
    if not RESIDENT_TAG_PATTERN.fullmatch(tag):
        raise ValueError("Tag 不属于 resident 自动构建范围")
    return tag


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def public_context(context: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in context.items() if not key.startswith("_")}


def parse_version_info(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def require_existing_version(fields: dict[str, str]) -> str:
    version = fields.get("Version") or fields.get("version") or ""
    version = version.strip().strip('"')
    if not version:
        raise ValueError(f"{VERSION_INFO_PATH} 缺少 Version 字段，无法更新版本号")
    return version


def normalize_optional_version(value: str) -> str:
    version = value.strip().strip('"')
    if not version:
        return ""
    raw = strip_version_prefix(version)
    parts = raw.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"版本基线格式无法识别：{value}")
    return f"{version_prefix(version)}{raw}" if version_prefix(version) else raw


def version_prefix(value: str) -> str:
    first = value.strip().strip('"')[:1].upper()
    return first if first in {"V", "F", "T"} else ""


def strip_version_prefix(value: str) -> str:
    version = value.strip().strip('"')
    return version[1:] if version[:1].upper() in {"V", "F", "T"} else version


def normalize_version_number(value: str) -> str:
    version = strip_version_prefix(value)
    if not version:
        return ""
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"版本号格式无法识别：{value}")
    return version


def apply_version_prefix(value: str, prefix: str) -> str:
    normalized_prefix = normalize_version_prefix(prefix)
    return f"{normalized_prefix}{normalize_version_number(value)}"


def normalize_version_prefix(value: str) -> str:
    prefix = str(value or "").strip().upper()
    if prefix not in {"V", "F", "T"}:
        raise ValueError("版本类型只能是 V、F 或 T")
    return prefix


def resolve_weekly_version_policy(tag_names: list[str], computed_version_number: str, local_now: datetime) -> dict[str, Any]:
    version_number = normalize_version_number(computed_version_number)
    parts = version_number.split(".")
    if len(parts) < 4:
        return {"version_number": version_number, "requires_confirmation": False, "reason": "version_has_less_than_four_parts"}
    current = [int(part) for part in parts[:4]]
    fourth_width = max(len(parts[3]), 3)
    week_versions = versions_from_tags_in_week(tag_names, local_now)
    if week_versions:
        third = max(item[2] for item in week_versions)
        fourth = max(item[3] for item in week_versions if item[2] == third) + 1
        current[2] = third
        current[3] = fourth
        return {
            "version_number": format_four_part_version(current, fourth_width),
            "requires_confirmation": False,
            "reason": "current_week_existing_third",
            "week": week_key(local_now),
        }
    if local_now.weekday() == 4:
        current[2] += 1
        return {
            "version_number": format_four_part_version(current, fourth_width),
            "requires_confirmation": True,
            "reason": "friday_first_weekly_third_bump",
            "week": week_key(local_now),
        }
    return {
        "version_number": version_number,
        "requires_confirmation": False,
        "reason": "not_friday_no_weekly_bump",
        "week": week_key(local_now),
    }


def versions_from_tags_in_week(tag_names: list[str], local_now: datetime) -> list[tuple[int, int, int, int]]:
    values: list[tuple[int, int, int, int]] = []
    target_week = week_key(local_now)
    for tag in tag_names:
        match = re.search(r"_([VvFfTt]?\d+(?:\.\d+)+)_(\d{12})$", tag)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(2), "%Y%m%d%H%M").replace(tzinfo=local_now.tzinfo)
        except ValueError:
            continue
        if week_key(stamp) != target_week:
            continue
        parts = normalize_version_number(match.group(1)).split(".")
        if len(parts) < 4 or not all(part.isdigit() for part in parts[:4]):
            continue
        values.append(tuple(int(part) for part in parts[:4]))
    return values


def week_key(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def format_four_part_version(parts: list[int], fourth_width: int) -> str:
    return ".".join([str(parts[0]), str(parts[1]), str(parts[2]), str(parts[3]).zfill(fourth_width)])


def resolve_version_prefix(ref: str, task: dict[str, Any] | None = None) -> str:
    task = task or {}
    mode = str(task.get("version_prefix_mode") or "auto")
    if mode == "manual":
        return normalize_version_prefix(str(task.get("manual_version_prefix") or "V"))
    if ref == "release":
        return "F"
    if ref == "fix" or ref.startswith("bugfix/"):
        return "V"
    return "V"


def config_matrix_value(config_matrix: list[dict[str, Any]]) -> str:
    return ",".join(f"{item['config_ref']}:{item['label']}" for item in config_matrix)


def config_refs_value(config_matrix: list[dict[str, Any]]) -> str:
    return ",".join(item["config_ref"] for item in config_matrix)


def release_tag_message(
    message: str,
    cloud_category: str,
    config_matrix: list[dict[str, Any]] | None = None,
    config_ref: str = "",
) -> str:
    base = str(message or "resident release build").strip() or "resident release build"
    category = str(cloud_category or DEFAULT_SCHEDULE["cloud_category"]).strip("/")
    lines = [base, "", f"SIMOS_CLOUD_CATEGORY={category}"]
    matrix = config_matrix or []
    if matrix:
        lines.append(f"SIMOS_CONFIG_MATRIX={config_matrix_value(matrix)}")
        lines.append(f"SIMOS_CONFIG_REFS={config_refs_value(matrix)}")
        lines.append(f"SIMOS_CONFIG_REF={matrix[0]['config_ref']}")
    else:
        config_value = str(config_ref or "").strip()
        if config_value:
            lines.append(f"SIMOS_CONFIG_REF={config_value}")
    return "\n".join(lines)


def version_hint_from_ref(value: str) -> str:
    match = TAG_VERSION_HINT_RE.search(value.strip())
    if not match:
        return ""
    return normalize_optional_version(match.group(0))


def load_version_settings() -> dict[str, str]:
    try:
        data = json.loads(VERSION_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    base_version = normalize_optional_version(str(data.get("base_version", "")))
    return {"base_version": base_version}


def save_version_settings(settings: dict[str, str]) -> None:
    current = load_version_settings()
    current.update({key: value for key, value in settings.items() if value})
    VERSION_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(current, ensure_ascii=False, indent=2)
    VERSION_SETTINGS_PATH.write_text(payload + chr(10), encoding="utf-8")


def bump_version(value: str) -> str:
    version = value.strip().strip('"')
    prefix = version_prefix(version)
    version = strip_version_prefix(version)
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"版本号格式无法自动递增：{value}")
    last_part_width = len(parts[-1])
    parts[-1] = str(int(parts[-1]) + 1).zfill(last_part_width)
    return f"{prefix}{'.'.join(parts)}"


def version_from_changed_components(
    previous_version: str,
    changed_components: list[str] | tuple[str, ...],
    next_version: str = "",
    previous_baseline_version: str = "",
    package_version: str = "",
) -> str:
    components = [component for component in changed_components if component in VERSION_COMPONENT_REVISIONS]
    normalized_package_version = package_version.strip().strip('"')
    if not components:
        return previous_version.strip().strip('"')
    if normalized_package_version and len(components) == 1 and explicit_fourth_part(normalized_package_version):
        return normalized_package_version
    if len(components) == 1:
        return bump_version(previous_version)
    normalized_baseline_version = previous_baseline_version.strip().strip('"')
    normalized_next_version = next_version.strip().strip('"')
    revision_components = components if not normalized_package_version else [component for component in components if component != "simos"]
    revision = sum(VERSION_COMPONENT_REVISIONS[component] for component in revision_components)
    base_source = normalized_package_version or normalized_baseline_version or normalized_next_version or previous_version
    return append_component_revision(version_revision_base(base_source), revision)


def append_component_revision(base_version: str, revision: int) -> str:
    return f"{base_version}{revision}"


def explicit_fourth_part(value: str) -> bool:
    parts = normalize_version_number(value).split(".")
    return len(parts) >= 4 and parts[3].isdigit() and int(parts[3]) > 0


def version_revision_base(value: str) -> str:
    version = value.strip().strip('"')
    prefix = version_prefix(version)
    version = strip_version_prefix(version)
    parts = version.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        raise ValueError(f"版本号格式无法计算模块修订位：{value}")
    parts[-1] = "0"
    return f"{prefix}{'.'.join(parts)}"


def parse_pkg_info_version(text: str) -> str:
    for line in text.splitlines():
        if line.strip().startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    return ""


def version_component(repo: RepositoryConfig) -> str:
    for value in (repo.id, repo.name, repo.project.rsplit("/", 1)[-1]):
        component = clean_component(value)
        if component in VERSION_COMPONENTS:
            return component
    return clean_component(repo.id) or "simos"


def is_simos_repo(repo: RepositoryConfig) -> bool:
    return version_component(repo) == "simos"


def is_config_repo(repo: RepositoryConfig) -> bool:
    project = repo.project.strip("/").lower()
    return repo.id == "config" or project == "os/config"


def clean_component(value: str) -> str:
    chars: list[str] = []
    for ch in value:
        if "A" <= ch <= "Z" or "a" <= ch <= "z" or "0" <= ch <= "9":
            chars.append(ch.lower())
        else:
            chars.append("_")
    component = "".join(chars).strip("_")
    while "__" in component:
        component = component.replace("__", "_")
    return component


def ordered_components(component: str, fields: dict[str, str]) -> list[str]:
    components: list[str] = []

    def add(value: str) -> None:
        if value and value not in components:
            components.append(value)

    for value in VERSION_COMPONENTS:
        add(value)
    add(component)
    for key in fields:
        if key.endswith("_commitid"):
            add(key[: -len("_commitid")])
        elif key.endswith("_branch"):
            add(key[: -len("_branch")])
    return components


def render_version_info(
    fields: dict[str, str],
    component: str,
    branch: str,
    commit_id: str,
    current_time: str,
) -> tuple[str, str]:
    previous_version = require_existing_version(fields)
    next_version = bump_version(previous_version)
    next_fields = dict(fields)
    next_fields[f"{component}_commitid"] = commit_id
    next_fields[f"{component}_branch"] = branch

    lines = [
        f"Version:{next_version}",
        'NextVersion:""',
        f"PreVersion:{previous_version}",
        "",
    ]
    for item in ordered_components(component, next_fields):
        lines.append(f"{item}_commitid:{next_fields.get(f'{item}_commitid', '')}")
    lines.append("")
    for item in ordered_components(component, next_fields):
        lines.append(f"{item}_branch:{next_fields.get(f'{item}_branch', '')}")
    lines.extend(["", f"Date:{current_time}"])
    return next_version, "\n".join(lines) + "\n"


def render_version_info_full(
    fields: dict[str, str],
    component_refs: dict[str, dict[str, str]],
    current_time: str,
    changed_components: list[str] | tuple[str, ...] | None = None,
    package_version: str = "",
    requested_version_prefix: str = "",
    forced_version: str = "",
) -> tuple[str, str]:
    previous_version = require_existing_version(fields)
    if forced_version:
        next_version_number = normalize_version_number(forced_version)
    else:
        next_version_number = normalize_version_number(
            version_from_changed_components(
                previous_version,
                changed_components or tuple(component_refs),
                fields.get("NextVersion", ""),
                fields.get("PreVersion", ""),
                package_version,
            )
        )
    next_prefix = normalize_version_prefix(requested_version_prefix or version_prefix(previous_version) or "V")
    next_version = apply_version_prefix(next_version_number, next_prefix)
    previous_version_for_file = apply_version_prefix(previous_version, next_prefix)
    next_fields = dict(fields)
    for component, item in component_refs.items():
        next_fields[f"{component}_commitid"] = item["commit_id"]
        next_fields[f"{component}_branch"] = item["ref"]

    lines = [
        f"Version:{next_version}",
        'NextVersion:""',
        f"PreVersion:{previous_version_for_file}",
        "",
    ]
    components = ordered_components("simos", next_fields)
    for item in components:
        lines.append(f"{item}_commitid:{next_fields.get(f'{item}_commitid', '')}")
    lines.append("")
    for item in components:
        lines.append(f"{item}_branch:{next_fields.get(f'{item}_branch', '')}")
    lines.extend(["", f"Date:{current_time}"])
    return next_version, "\n".join(lines) + "\n"


def render_pkg_info(version: str, previous_text: str = "") -> str:
    parts = version.split(".")
    pkg_version = ".".join(parts[:3] + ["0"]) if len(parts) >= 3 else version
    if not previous_text:
        return f"version:{pkg_version}\n"
    lines = previous_text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("version:"):
            prefix = line[: len(line) - len(line.lstrip())]
            lines[index] = f"{prefix}version:{pkg_version}"
            replaced = True
            break
    if not replaced:
        lines.append(f"version:{pkg_version}")
    return "\n".join(lines) + "\n"


def render_software_yaml(version: str, component_refs: dict[str, dict[str, str]], current_time: str) -> str:
    lines = [
        f'version: "{version}"',
        "components:",
        f'  main: "{version}"',
    ]
    for component in VERSION_COMPONENTS:
        if component == "simos":
            continue
        if component in component_refs:
            lines.append(f'  {component}: "{component_refs[component]["ref"]}"')
    lines.append("commits:")
    main_commit = component_refs.get("simos", {}).get("commit_id", "")
    lines.append(f'  main: "{main_commit}"')
    for component in VERSION_COMPONENTS:
        if component == "simos":
            continue
        if component in component_refs:
            lines.append(f'  {component}: "{component_refs[component]["commit_id"]}"')
    lines.append(f'date: "{current_time}"')
    return "\n".join(lines) + "\n"


def submodule_update_paths(component_refs: dict[str, dict[str, str]]) -> dict[str, str]:
    return {component: f"src/{component}" for component in SUBMODULE_COMPONENTS if component in component_refs}


def component_commit_changed(component: str, current_commit_id: str, version_plan: dict[str, Any]) -> bool:
    recorded = version_plan.get("version_info_fields", {}).get(f"{component}_commitid", "")
    if recorded == current_commit_id:
        return False
    if component == "simos" and recorded in (version_plan.get("source_parent_ids") or []):
        return False
    return True


def is_merge_request_merged(merge_request: dict[str, Any] | None) -> bool:
    return bool(merge_request and str(merge_request.get("state", "")).lower() == "merged")


def is_merge_request_closed(merge_request: dict[str, Any] | None) -> bool:
    return bool(merge_request and str(merge_request.get("state", "")).lower() in {"closed", "canceled", "cancelled"})


def require_schedule_id(value: str) -> str:
    schedule_id = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,62}", schedule_id):
        raise ValueError("自动任务 ID 只能包含字母、数字、下划线和中划线，长度 2-63")
    return schedule_id


def normalize_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_release_task(payload)


def default_config_matrix() -> list[dict[str, Any]]:
    return [dict(item) for item in DEFAULT_SCHEDULE["config_matrix"]]


def normalize_config_matrix_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            config_ref = item.strip()
            label = "360" if config_ref == "SIMBOT_R6_A" else "360s" if config_ref == "SIMBOT_R6_B" else config_ref
            enabled = True
        elif isinstance(item, dict):
            config_ref = str(item.get("config_ref") or item.get("ref") or item.get("id") or "").strip()
            label = str(item.get("label") or item.get("target") or "").strip()
            enabled = truthy(item.get("enabled", True))
        else:
            continue
        if not config_ref:
            continue
        config_ref = require_ref_name(config_ref, "config 分支")
        label = label or ("360" if config_ref == "SIMBOT_R6_A" else "360s" if config_ref == "SIMBOT_R6_B" else config_ref)
        if not re.match(r"^[A-Za-z0-9._-]+$", label):
            raise ValueError("config label 只能包含字母、数字、点、下划线和横线")
        normalized.append({"config_ref": config_ref, "label": label, "enabled": enabled})
    return normalized


def resolve_config_matrix(task: dict[str, Any]) -> list[dict[str, str]]:
    if not truthy(task.get("config_matrix_enabled", True)):
        config_ref = str(task.get("config_ref") or "").strip()
        return [{"config_ref": require_ref_name(config_ref, "config 分支"), "label": config_ref}] if config_ref else []
    matrix = normalize_config_matrix_items(task.get("config_matrix"))
    if not matrix:
        legacy_ref = str(task.get("config_ref") or "").strip()
        if legacy_ref:
            matrix = normalize_config_matrix_items([legacy_ref])
        else:
            matrix = normalize_config_matrix_items(default_config_matrix())
    return [{"config_ref": item["config_ref"], "label": item["label"]} for item in matrix if item.get("enabled", True)]


def normalize_release_task(payload: dict[str, Any]) -> dict[str, Any]:
    task = {**DEFAULT_SCHEDULE, **payload}
    task["id"] = require_schedule_id(str(task.get("id") or DEFAULT_SCHEDULE["id"]))
    task["enabled"] = truthy(task.get("enabled", True))
    task["name"] = str(task.get("name") or task["id"]).strip()
    task["timezone"] = str(task.get("timezone") or "Asia/Shanghai").strip()
    ZoneInfo(task["timezone"])
    daily_time = str(task.get("daily_time") or time_from_cron(str(task.get("cron") or DEFAULT_SCHEDULE["cron"]))).strip()
    task["daily_time"] = normalize_daily_time(daily_time)
    task["cron"] = cron_from_daily_time(task["daily_time"])
    strategy = str(task.get("source_ref_strategy") or task.get("ref_strategy") or "fixed_ref").strip()
    if strategy == "editable":
        strategy = "fixed_ref"
    if strategy not in {"fixed_ref", "latest_fix_rc", "manual_ref"}:
        raise ValueError("source_ref_strategy 只能是 fixed_ref、latest_fix_rc 或 manual_ref")
    task["source_ref_strategy"] = strategy
    task["ref_strategy"] = "latest_fix_rc" if strategy == "latest_fix_rc" else "editable"
    task["default_ref"] = str(task.get("default_ref") or "fix").strip()
    config_ref = str(task.get("config_ref") or "").strip()
    task["config_ref"] = require_ref_name(config_ref, "config 分支") if config_ref else ""
    task["config_matrix_enabled"] = truthy(task.get("config_matrix_enabled", True))
    if "config_matrix" not in payload and config_ref:
        task["config_matrix"] = normalize_config_matrix_items([config_ref])
    else:
        task["config_matrix"] = normalize_config_matrix_items(task.get("config_matrix")) or default_config_matrix()
    task.pop("dependency_ref", None)
    task["version_source"] = str(task.get("version_source") or "simos_version_info").strip()
    if task["version_source"] not in {"simos_version_info", "manual"}:
        raise ValueError("version_source 只能是 simos_version_info 或 manual")
    task["manual_version_number"] = normalize_version_number(str(task.get("manual_version_number") or task.get("manual_version") or ""))
    task["manual_version"] = task["manual_version_number"]
    task["version_prefix_mode"] = str(task.get("version_prefix_mode") or "auto").strip()
    if task["version_prefix_mode"] not in {"auto", "manual"}:
        raise ValueError("version_prefix_mode 只能是 auto 或 manual")
    task["manual_version_prefix"] = normalize_version_prefix(str(task.get("manual_version_prefix") or "V"))
    task["cloud_category"] = str(task.get("cloud_category") or DEFAULT_SCHEDULE["cloud_category"]).strip("/")
    if not task["cloud_category"] or ".." in task["cloud_category"] or task["cloud_category"].startswith("/"):
        raise ValueError("云盘分类目录非法")
    task["execution_type"] = "full_release"
    return task


def normalize_daily_time(value: str) -> str:
    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", value.strip())
    if not match:
        raise ValueError("执行时间格式应为 HH:mm")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("执行时间超出范围")
    return f"{hour:02d}:{minute:02d}"


def cron_from_daily_time(value: str) -> str:
    hour, minute = normalize_daily_time(value).split(":")
    return f"{int(minute)} {int(hour)} * * *"


def time_from_cron(value: str) -> str:
    minute, hour, *_ = normalize_daily_cron(value).split()
    return f"{int(hour):02d}:{int(minute):02d}"


def normalize_daily_cron(value: str) -> str:
    parts = value.split()
    if len(parts) != 5 or parts[2:] != ["*", "*", "*"]:
        raise ValueError("自动任务当前支持每日 cron，格式例如：0 16 * * *")
    minute = int(parts[0])
    hour = int(parts[1])
    if not (0 <= minute <= 59 and 0 <= hour <= 23):
        raise ValueError("cron 小时或分钟超出范围")
    return f"{minute} {hour} * * *"


def load_schedules() -> list[dict[str, Any]]:
    return load_release_tasks()


def save_schedules(schedules: list[dict[str, Any]]) -> None:
    save_release_tasks(schedules)


def load_release_tasks() -> list[dict[str, Any]]:
    if RELEASE_TASKS_PATH.exists():
        raw = json.loads(RELEASE_TASKS_PATH.read_text(encoding="utf-8"))
        items = raw.get("tasks", raw.get("schedules", raw if isinstance(raw, list) else []))
        return [normalize_release_task(item) for item in items]
    if SCHEDULES_PATH.exists():
        raw = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        items = raw.get("schedules", raw if isinstance(raw, list) else [])
        tasks = [normalize_release_task(item) for item in items]
        save_release_tasks(tasks)
        return tasks
    tasks = [dict(DEFAULT_SCHEDULE)]
    save_release_tasks(tasks)
    return [normalize_release_task(item) for item in tasks]


def save_release_tasks(tasks: list[dict[str, Any]]) -> None:
    RELEASE_TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    normalized = [normalize_release_task(item) for item in tasks]
    RELEASE_TASKS_PATH.write_text(json.dumps({"tasks": normalized}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_schedule_runs() -> list[dict[str, Any]]:
    return load_release_runs()


def save_schedule_runs(runs: list[dict[str, Any]]) -> None:
    save_release_runs(runs)


def append_schedule_run(run: dict[str, Any]) -> None:
    append_release_run(run)


def load_release_runs() -> list[dict[str, Any]]:
    if RELEASE_RUNS_PATH.exists():
        raw = json.loads(RELEASE_RUNS_PATH.read_text(encoding="utf-8"))
        items = raw.get("runs", raw if isinstance(raw, list) else [])
        return [item for item in items if isinstance(item, dict)]
    if SCHEDULE_RUNS_PATH.exists():
        raw = json.loads(SCHEDULE_RUNS_PATH.read_text(encoding="utf-8"))
        items = raw.get("runs", raw if isinstance(raw, list) else [])
        runs = [item for item in items if isinstance(item, dict)]
        save_release_runs(runs)
        return runs
    return []


def save_release_runs(runs: list[dict[str, Any]]) -> None:
    RELEASE_RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RELEASE_RUNS_PATH.write_text(json.dumps({"runs": runs[-300:]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_release_run(run: dict[str, Any]) -> None:
    runs = [item for item in load_release_runs() if item.get("id") != run.get("id")]
    save_release_runs(runs + [run])


def new_release_run_id(prefix: str = "run") -> str:
    return f"{prefix}-{datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d%H%M%S%f')}"


def parse_schedule_now(value: str) -> datetime:
    return datetime.fromisoformat(value)


def coerce_schedule_now(schedule: dict[str, Any], now: datetime | None = None) -> datetime:
    tz = ZoneInfo(str(schedule.get("timezone") or "Asia/Shanghai"))
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        return current.replace(tzinfo=tz)
    return current.astimezone(tz)


def schedule_due_key(schedule: dict[str, Any], local_now: datetime) -> str:
    minute_s, hour_s, *_ = normalize_daily_cron(str(schedule.get("cron") or "0 16 * * *")).split()
    scheduled = local_now.replace(hour=int(hour_s), minute=int(minute_s), second=0, microsecond=0)
    if local_now < scheduled or local_now >= scheduled + timedelta(minutes=5):
        return ""
    return f"{schedule['id']}:{scheduled.strftime('%Y-%m-%dT%H:%M')}"


def schedule_already_ran(schedule_id: str, due_key: str) -> bool:
    return any(item.get("schedule_id") == schedule_id and item.get("due_key") == due_key for item in load_release_runs())


def render_schedule_tag_name(template: str, version: str, stamp: str) -> str:
    value = template.replace("{version}", version).replace("{yyyyMMddHHmm}", stamp)
    return require_ref_name(value, "自动任务 Tag 名称")


def latest_release_tag_key(value: str) -> tuple[str, tuple[Any, ...]]:
    match = re.search(r"_(\d{12})$", value)
    stamp = match.group(1) if match else ""
    return stamp, natural_ref_key(value)


def natural_ref_key(value: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for item in re.split(r"(\d+)", value):
        parts.append(int(item) if item.isdigit() else item)
    return tuple(parts)


def start_schedule_worker(app: GitOpsApp) -> None:
    if os.environ.get("GITOPS_SCHEDULER_ENABLED", "true").lower() in {"0", "false", "no"}:
        return

    def loop() -> None:
        while True:
            try:
                app.run_due_schedules()
            except Exception as exc:
                print(f"[schedule] {exc}", file=sys.stderr, flush=True)
            time.sleep(60)

    thread = threading.Thread(target=loop, name="gitops-schedule-worker", daemon=True)
    thread.start()


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


def configure_tls(server: ThreadingHTTPServer) -> bool:
    cert_path = os.environ.get("GITOPS_TLS_CERT", "").strip()
    key_path = os.environ.get("GITOPS_TLS_KEY", "").strip()
    if not cert_path and not key_path:
        return False
    if not cert_path or not key_path:
        raise ValueError("GITOPS_TLS_CERT and GITOPS_TLS_KEY must be configured together")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    return True


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
                "/api/common-refs": ("view", app.common_refs),
                "/api/resident-packages": ("view", lambda: app.resident_package(query.get("tag", ""))),
                "/api/schedules": ("view", app.schedules),
                "/api/release-tasks": ("view", app.release_tasks),
                "/api/release-runs": ("view", lambda: {"ok": True, "runs": load_release_runs()}),
            }
            schedule_route = match_schedule_path(path)
            if schedule_route and schedule_route[1] == "runs":
                routes[path] = ("view", lambda schedule_id=schedule_route[0]: app.schedule_runs(schedule_id))
            release_task_route = match_release_task_path(path)
            if release_task_route and release_task_route[1] == "runs":
                routes[path] = ("view", lambda task_id=release_task_route[0]: app.schedule_runs(task_id))
            release_run_id = match_release_run_path(path)
            if release_run_id:
                routes[path] = ("view", lambda run_id=release_run_id: {"ok": True, "run": next((item for item in load_release_runs() if item.get("id") == run_id), None)})
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
                "/api/release/create": ("create_release", lambda: app.create_release(payload)),
                "/api/feature/create": ("create_feature", lambda: app.create_feature(payload)),
                "/api/bugfix/create": ("create_bugfix", lambda: app.create_bugfix(payload)),
                "/api/tags/create": ("create_tag", lambda: app.create_tag(payload)),
                "/api/tags/delete": ("create_tag", lambda: app.delete_tags(payload)),
                "/api/schedules": ("admin", lambda: app.save_schedule(payload)),
                "/api/release-tasks": ("admin", lambda: app.save_release_task(payload)),
                "/api/release-runs/manual": ("create_tag", lambda: app.manual_release_run(payload)),
                "/api/release-runs/rerun-tag": ("create_tag", lambda: app.rerun_tag_release(payload)),
            }
            schedule_route = match_schedule_path(path)
            if schedule_route:
                schedule_id, action = schedule_route
                if action == "dry-run":
                    routes[path] = ("create_tag", lambda schedule_id=schedule_id: app.schedule_dry_run(schedule_id))
                elif action == "run-now":
                    routes[path] = ("create_tag", lambda schedule_id=schedule_id: app.schedule_run_now(schedule_id))
            release_task_route = match_release_task_path(path)
            if release_task_route:
                task_id, action = release_task_route
                if action == "dry-run":
                    routes[path] = ("create_tag", lambda task_id=task_id: app.schedule_dry_run(task_id))
                elif action == "run-now":
                    routes[path] = ("create_tag", lambda task_id=task_id: app.schedule_run_now(task_id))
            release_run_action = match_release_run_action_path(path)
            if release_run_action:
                run_id, action = release_run_action
                if action == "continue":
                    routes[path] = ("create_tag", lambda run_id=run_id: app.continue_release_run(run_id))
            self.dispatch_route(path, routes)

        def do_PUT(self) -> None:
            path = urlparse(self.path).path
            payload = self.read_json()
            schedule_route = match_schedule_path(path)
            if schedule_route and not schedule_route[1]:
                schedule_id = schedule_route[0]
                self.handle_api("admin", lambda schedule_id=schedule_id: app.save_schedule({**payload, "id": schedule_id}))
                return
            release_task_route = match_release_task_path(path)
            if release_task_route and not release_task_route[1]:
                task_id = release_task_route[0]
                self.handle_api("admin", lambda task_id=task_id: app.save_release_task({**payload, "id": task_id}))
                return
            repo_id = match_repository_path(path)
            if not repo_id:
                json_response(self, 404, {"ok": False, "error": "Unknown endpoint."})
                return
            self.handle_api("admin", lambda: app.update_repository(repo_id, payload))

        def do_DELETE(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/release-runs":
                self.handle_api("admin", app.clear_release_runs)
                return
            release_run_id = match_release_run_path(path)
            if release_run_id:
                self.handle_api("admin", lambda run_id=release_run_id: app.delete_release_run(run_id))
                return
            schedule_route = match_schedule_path(path)
            if schedule_route and not schedule_route[1]:
                schedule_id = schedule_route[0]
                self.handle_api("admin", lambda schedule_id=schedule_id: app.delete_schedule(schedule_id))
                return
            release_task_route = match_release_task_path(path)
            if release_task_route and not release_task_route[1]:
                task_id = release_task_route[0]
                self.handle_api("admin", lambda task_id=task_id: app.delete_release_task(task_id))
                return
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


def match_schedule_path(path: str) -> tuple[str, str] | None:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "schedules":
        return parts[2], ""
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "schedules":
        return parts[2], parts[3]
    return None


def match_release_task_path(path: str) -> tuple[str, str] | None:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "release-tasks":
        return parts[2], ""
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "release-tasks":
        return parts[2], parts[3]
    return None


def match_release_run_path(path: str) -> str:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 3 and parts[0] == "api" and parts[1] == "release-runs":
        return parts[2]
    return ""


def match_release_run_action_path(path: str) -> tuple[str, str] | None:
    parts = [item for item in path.split("/") if item]
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "release-runs":
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
    tls_enabled = configure_tls(server)
    scheme = "https" if tls_enabled else "http"
    print(f"GitLab Branch Workbench: {scheme}://{host}:{port}", flush=True)
    print(f"Repositories: {len(app.store.list())}", flush=True)
    start_schedule_worker(app)
    server.serve_forever()


if __name__ == "__main__":
    main()

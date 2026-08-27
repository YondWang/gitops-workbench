from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from gitlab_client import GitLabError


class SimulatedGitLabClient:
    """Small persistent GitLab substitute for local UI and operation testing."""

    def __init__(
        self,
        state_path: Path,
        *,
        repo_id: str,
        project: str,
        initial: dict[str, Any] | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.repo_id = repo_id
        self.project_path = project
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = self._load()
        if repo_id not in state["repositories"]:
            state["repositories"][repo_id] = self._normalize_repository(initial or {})
            state["baseline_repositories"][repo_id] = copy.deepcopy(state["repositories"][repo_id])
            self._save(state)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"repositories": {}, "baseline_repositories": {}, "pipelines": [], "next_pipeline_id": 1}
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        raw.setdefault("repositories", {})
        raw.setdefault("baseline_repositories", {})
        raw.setdefault("pipelines", [])
        raw.setdefault("next_pipeline_id", 1)
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _normalize_repository(initial: dict[str, Any]) -> dict[str, Any]:
        branches = copy.deepcopy(initial.get("branches") or {})
        branches.setdefault("main", {"commit_id": "main-commit", "files": {}})
        for name, branch in list(branches.items()):
            if isinstance(branch, str):
                branches[name] = {"commit_id": branch, "files": {}}
            else:
                branches[name].setdefault("commit_id", f"{name}-commit")
                branches[name].setdefault("files", {})
        tags = []
        for item in initial.get("tags") or []:
            if isinstance(item, str):
                tags.append({"name": item, "target": ""})
            else:
                tags.append({"name": str(item.get("name") or ""), "target": str(item.get("target") or "")})
        return {"branches": branches, "tags": tags}

    def _repo(self, state: dict[str, Any]) -> dict[str, Any]:
        try:
            return state["repositories"][self.repo_id]
        except KeyError as exc:
            raise GitLabError(f"模拟仓库不存在：{self.repo_id}", status=404, payload={}) from exc

    def project(self) -> dict[str, Any]:
        return {"id": self.repo_id, "path_with_namespace": self.project_path}

    def branch_names(self) -> list[str]:
        return list(self._repo(self._load())["branches"])

    def tag_names(self) -> list[str]:
        return [item["name"] for item in self._repo(self._load())["tags"]]

    def branches(self, search: str = "") -> list[dict[str, Any]]:
        state = self._load()
        return [
            {"name": name, "commit": {"id": item["commit_id"]}}
            for name, item in self._repo(state)["branches"].items()
            if not search or search in name
        ]

    def tags(self, search: str = "") -> list[dict[str, Any]]:
        state = self._load()
        return [
            {"name": item["name"], "commit": {"id": item["target"]}}
            for item in self._repo(state)["tags"]
            if not search or search in item["name"]
        ]

    def branch(self, name: str) -> dict[str, Any]:
        state = self._load()
        item = self._repo(state)["branches"].get(name)
        if item is None:
            raise GitLabError(f"分支不存在：{name}", status=404, payload={})
        return {"name": name, "commit": {"id": item["commit_id"], "parent_ids": item.get("parent_ids", [])}}

    def get_file_text(self, file_path: str, ref: str) -> str:
        state = self._load()
        branch = self._repo(state)["branches"].get(ref)
        if branch is None or file_path not in branch.get("files", {}):
            raise GitLabError(f"文件不存在：{file_path}@{ref}", status=404, payload={})
        return str(branch["files"][file_path])

    def create_branch(self, branch: str, ref: str) -> dict[str, Any]:
        state = self._load()
        repository = self._repo(state)
        if branch in repository["branches"]:
            raise GitLabError(f"分支已存在：{branch}", status=409, payload={})
        source = repository["branches"].get(ref)
        if source is None:
            raise GitLabError(f"来源分支不存在：{ref}", status=404, payload={})
        repository["branches"][branch] = copy.deepcopy(source)
        self._save(state)
        return {"name": branch, "ref": ref}

    def create_commit(self, branch: str, commit_message: str, actions: list[dict[str, str]]) -> dict[str, Any]:
        state = self._load()
        repository = self._repo(state)
        current = repository["branches"].get(branch)
        if current is None:
            raise GitLabError(f"分支不存在：{branch}", status=404, payload={})
        files = copy.deepcopy(current.get("files", {}))
        for action in actions:
            path = str(action.get("file_path") or "")
            if action.get("action") in {"create", "update"}:
                files[path] = str(action.get("content") or "")
            elif action.get("action") == "delete":
                files.pop(path, None)
            else:
                raise GitLabError(f"模拟提交动作不支持：{action.get('action')}", status=400, payload={})
        commit_id = f"{self.repo_id}-{branch.replace('/', '-')}-commit-{len(files)}-{len(state['pipelines'])}"
        repository["branches"][branch] = {
            "commit_id": commit_id,
            "parent_ids": [current["commit_id"]],
            "files": files,
        }
        self._save(state)
        return {"id": commit_id, "short_id": commit_id[:12], "message": commit_message}

    def create_tag(self, tag_name: str, ref: str, message: str = "") -> dict[str, Any]:
        state = self._load()
        repository = self._repo(state)
        if any(item["name"] == tag_name for item in repository["tags"]):
            raise GitLabError(f"Tag 已存在：{tag_name}", status=409, payload={})
        target = ref
        if ref in repository["branches"]:
            target = repository["branches"][ref]["commit_id"]
        if not target:
            raise GitLabError("Tag 目标不能为空", status=400, payload={})
        item = {"name": tag_name, "target": target, "message": message}
        repository["tags"].append(item)
        self._save(state)
        return {"name": tag_name, "target": target, "message": message}

    def create_pipeline(self, ref: str, variables: dict[str, str] | None = None) -> dict[str, Any]:
        state = self._load()
        repository = self._repo(state)
        is_tag = any(item["name"] == ref for item in repository["tags"])
        is_branch = ref in repository["branches"]
        if not is_tag and not is_branch:
            raise GitLabError(f"Tag 或分支不存在：{ref}", status=404, payload={})
        pipeline_id = int(state["next_pipeline_id"])
        state["next_pipeline_id"] = pipeline_id + 1
        pipeline = {
            "id": pipeline_id,
            "repo_id": self.repo_id,
            "ref": ref,
            "status": "success",
            "source": "api",
            "variables": dict(variables or {}),
            "web_url": f"https://simulated.gitlab/{self.project_path}/-/pipelines/{pipeline_id}",
        }
        if ref == "ci/feature-package" and (variables or {}).get("GITOPS_FEATURE_PACKAGE") == "1":
            context_b64 = str((variables or {}).get("GITOPS_FEATURE_CONTEXT_B64") or "")
            try:
                import base64

                context = json.loads(base64.urlsafe_b64decode(context_b64.encode("ascii")).decode("utf-8"))
                build_id = str(context["build_id"])
                category = str(context["cloud_category"])
                pipeline["feature_result"] = {
                    "status": "success",
                    "registry": {"project": context["registry"]["project"], "package_version": build_id},
                    "nextcloud": {"cloud_dir": f"{category}/{build_id}"},
                }
            except Exception as exc:
                pipeline.update({"status": "failed", "feature_result": {"status": "failed", "error": str(exc)}})
        state["pipelines"].append(pipeline)
        self._save(state)
        return pipeline

    def pipelines(self, ref: str = "", status: str = "", source: str = "") -> list[dict[str, Any]]:
        return [
            item
            for item in self._load()["pipelines"]
            if item["repo_id"] == self.repo_id
            and (not ref or item["ref"] == ref)
            and (not status or item["status"] == status)
            and (not source or item["source"] == source)
        ]

    def pipeline_jobs(self, pipeline_id: int | str) -> list[dict[str, Any]]:
        pipeline = next((item for item in self._load()["pipelines"] if str(item.get("id")) == str(pipeline_id)), None)
        if pipeline and pipeline.get("feature_result") is not None:
            return [{"id": f"{pipeline_id}-feature-publish", "name": "feature_publish_nextcloud", "status": pipeline["status"], "pipeline": {"id": int(pipeline_id)}}]
        return [{"id": f"{pipeline_id}-package", "name": "package", "status": "success", "pipeline": {"id": int(pipeline_id)}}]

    def job_artifact_file_text(self, job_id: int | str, artifact_path: str) -> str:
        if artifact_path != "feature-package-result.json":
            raise GitLabError(f"模拟 artifact 不存在：{artifact_path}", status=404, payload={})
        pipeline_id = str(job_id).split("-", 1)[0]
        pipeline = next((item for item in self._load()["pipelines"] if str(item.get("id")) == pipeline_id), None)
        if not pipeline or pipeline.get("feature_result") is None:
            raise GitLabError("模拟 Feature 构建结果不存在", status=404, payload={})
        return json.dumps(pipeline["feature_result"], ensure_ascii=False)

    def reset(self) -> None:
        state = self._load()
        baseline = state.get("baseline_repositories", {}).get(self.repo_id)
        if baseline is None:
            baseline = self._normalize_repository({})
            state.setdefault("baseline_repositories", {})[self.repo_id] = copy.deepcopy(baseline)
        state["repositories"][self.repo_id] = copy.deepcopy(baseline)
        state["pipelines"] = [item for item in state["pipelines"] if item.get("repo_id") != self.repo_id]
        state["next_pipeline_id"] = 1
        self._save(state)

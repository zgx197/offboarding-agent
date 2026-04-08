#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Iterable
from xml.etree import ElementTree


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = WORKSPACE_ROOT / "templates"
RUNS_DIR = WORKSPACE_ROOT / "runs"

IGNORED_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".next",
    ".turbo",
    ".venv",
    "Library",
    "Logs",
    "Obj",
    "PackagesCache",
    "Temp",
    "UserSettings",
    "venv",
    "env",
    "runs",
}

TEXT_EXTENSIONS = {
    ".cs",
    ".csproj",
    ".config",
    ".dockerignore",
    ".env",
    ".gradle",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".kts",
    ".md",
    ".properties",
    ".ps1",
    ".py",
    ".rb",
    ".scala",
    ".sh",
    ".sql",
    ".sln",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

DOC_FILENAMES = {
    "readme",
    "readme.md",
    "readme.txt",
    "architecture.md",
    "design.md",
    "handover.md",
}

ENTRY_FILENAMES = {
    "main",
    "index",
    "program.cs",
    "startup.cs",
    "application",
}

CONFIG_FILENAMES = {
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
}

ENV_PREFIXES = ("appsettings", ".env")
SCRIPT_EXTENSIONS = {".ps1", ".sh"}
CONFIG_EXTENSIONS = {".asmdef", ".csproj", ".json", ".props", ".sln", ".targets", ".xml", ".yaml", ".yml"}
SOURCE_EXTENSIONS = {
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".py",
    ".rb",
    ".rs",
    ".scala",
    ".ts",
    ".tsx",
}

MAX_TEXT_FILE_SIZE = 256 * 1024
MAX_EVIDENCE_SNIPPET_CHARS = 1200
MAX_EVIDENCE_PER_TYPE = 20
MAX_RELATED_REFERENCE_FILES = 12
MAX_CODE_EVIDENCE_ITEMS = 28
MAX_CODE_EVIDENCE_PER_FILE = 2
MAX_CODE_EVIDENCE_PER_CATEGORY = 8

PRIMARY_CODE_PATH_PATTERNS = [
    "/editor/homemap/windows/homemapentrywindow.cs",
    "/editor/windows/entrywindow.cs",
    "/editor/scenemodel/scenesessionrequest.cs",
    "/editor/sceneprofiles/sceneprofile.cs",
    "/editor/sceneprofiles/homemapsceneprofile.cs",
    "/editor/session/editorsession.cs",
    "/editor/session/sceneoperationrouter.cs",
    "/editor/session/scenemodulesetregistry.cs",
    "/editor/session/scenemoduleset.cs",
    "/editor/export/exportcontext.cs",
    "/editor/export/exportpipeline.cs",
    "/editor/export/exportstepregistry.cs",
    "/editor/export/steps/exportsceneblueprintruntimedatastep.cs",
    "/editor/export/steps/exportunifiednavdescriptorstep.cs",
    "/editor/homemap/services/homemapruntimepackageexportservice.cs",
    "/runtime/framesync/data/sceneblueprintruntimedata.cs",
    "/runtime/homemap/data/homemapruntimepackagedata.cs",
    "/runtime/homemap/data/unifiednavexportdescriptordata.cs",
    "/editor/integration/stagedesignerintegration.cs",
    "/editor/bridge/mapeditorbridge.cs",
]


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    type: str
    title: str
    path: str
    summary: str
    snippet: str
    tags: list[str]
    sourceStage: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "path": self.path,
            "summary": self.summary,
            "snippet": self.snippet,
            "tags": self.tags,
            "sourceStage": self.sourceStage,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the MVP offboarding workflow without modifying the target repository."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Create a task, scan the target repository in read-only mode, and generate the first handover pack.",
    )
    run_parser.add_argument("--repo", required=True, help="Absolute or relative path to the target repository.")
    run_parser.add_argument(
        "--target",
        required=True,
        action="append",
        help="Target path inside the repository. Can be repeated.",
    )
    run_parser.add_argument("--owner", default="Unknown", help="The current owner or departing engineer.")
    run_parser.add_argument("--audience", default="接手开发", help="Primary audience of the handover material.")
    run_parser.add_argument("--reviewer", default="", help="Optional reviewer name.")
    run_parser.add_argument(
        "--task-id",
        help="Deprecated compatibility argument. Output directory is now always derived from the target project name.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "run":
        return run_workflow(args)
    raise ValueError(f"Unsupported command: {args.command}")


def run_workflow(args: argparse.Namespace) -> int:
    repo_path = Path(args.repo).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise SystemExit(f"Target repository does not exist: {repo_path}")

    target_paths = [resolve_target_path(repo_path, value) for value in args.target]
    task_id = build_task_id(target_paths)
    cleanup_legacy_run_dirs(task_id)
    run_dir = RUNS_DIR / task_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    task = {
        "taskId": task_id,
        "repoPath": str(repo_path),
        "targetPaths": [to_posix(target.relative_to(repo_path)) for target in target_paths],
        "owner": args.owner,
        "reviewer": args.reviewer,
        "audience": args.audience,
        "createdAt": created_at,
        "updatedAt": created_at,
        "status": "initialized",
        "workspaceOutputRoot": str(RUNS_DIR.resolve()),
        "writePolicy": "Never write into the target repository. Outputs are stored only under this workspace runs directory.",
    }
    write_json(run_dir / "task.json", task)

    evidence = collect_evidence(repo_path, target_paths)
    write_json(run_dir / "evidence.json", [item.as_dict() for item in evidence])

    understanding_dir = run_dir / "understanding"
    understanding_dir.mkdir(exist_ok=True)
    code_registry = build_code_evidence_registry(repo_path, target_paths, evidence, task)
    write_json(understanding_dir / "14-code-evidence-registry.json", code_registry)
    change_impact_matrix = render_change_impact_matrix(task, code_registry)
    (understanding_dir / "15-change-impact-matrix.md").write_text(change_impact_matrix, encoding="utf-8")

    questions_markdown = render_open_questions(task, evidence)
    (understanding_dir / "open-questions.md").write_text(questions_markdown, encoding="utf-8")

    scan_summary_markdown = render_scan_summary(task, evidence)
    (understanding_dir / "scan-summary.md").write_text(scan_summary_markdown, encoding="utf-8")

    handover_markdown = render_handover(task, evidence, understanding_dir)
    (run_dir / "handover.md").write_text(handover_markdown, encoding="utf-8")

    task["status"] = "completed"
    task["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_json(run_dir / "task.json", task)

    print(f"Run completed: {run_dir}")
    print("Generated files:")
    print(f"  - {run_dir / 'task.json'}")
    print(f"  - {run_dir / 'evidence.json'}")
    print(f"  - {run_dir / 'handover.md'}")
    print(f"  - {understanding_dir / '14-code-evidence-registry.json'}")
    print(f"  - {understanding_dir / '15-change-impact-matrix.md'}")
    print(f"  - {understanding_dir / 'scan-summary.md'}")
    print(f"  - {understanding_dir / 'open-questions.md'}")
    return 0


def resolve_target_path(repo_path: Path, raw_target: str) -> Path:
    raw_path = Path(raw_target)
    candidate = raw_path.resolve() if raw_path.is_absolute() else (repo_path / raw_path).resolve()

    if not candidate.exists():
        raise SystemExit(f"Target path does not exist: {candidate}")
    if not candidate.is_relative_to(repo_path):
        raise SystemExit(f"Target path must stay inside repo: {candidate}")
    return candidate


def build_task_id(target_paths: list[Path]) -> str:
    base_name = slugify(target_paths[0].name) or "task"
    return base_name


def cleanup_legacy_run_dirs(task_id: str) -> None:
    if not RUNS_DIR.exists():
        return

    runs_root = RUNS_DIR.resolve()
    aliases = {task_id, task_id.replace("-", "")}
    for candidate in RUNS_DIR.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name == task_id:
            continue
        if not any(
            candidate.name == alias or candidate.name.startswith(f"{alias}-")
            for alias in aliases
        ):
            continue

        resolved = candidate.resolve()
        if not resolved.is_relative_to(runs_root):
            raise SystemExit(f"Refusing to remove run directory outside runs root: {resolved}")
        shutil.rmtree(resolved)


def slugify(value: str) -> str:
    normalized = value.strip()
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", normalized)
    normalized = normalized.replace(" ", "-").lower()
    normalized = re.sub(r"[^a-z0-9._-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def collect_evidence(repo_path: Path, target_paths: list[Path]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    counter = 1

    for target_path in target_paths:
        evidence.append(
            make_evidence_item(
                counter,
                "directory_tree",
                f"目录结构概览: {to_posix(target_path.relative_to(repo_path))}",
                to_posix(target_path.relative_to(repo_path)),
                "目标范围的目录树摘要，可用于快速建立模块心智模型。",
                build_directory_tree(target_path, repo_path),
                ["structure", "target"],
            )
        )
        counter += 1

    candidate_files = sorted(
        scan_candidate_files(repo_path, target_paths),
        key=lambda path: (
            candidate_file_priority_score(repo_path, target_paths, path),
            to_posix(path.relative_to(repo_path)).lower(),
        ),
    )
    related_reference_files = sorted(
        find_related_reference_files(repo_path, target_paths),
        key=lambda path: (
            candidate_file_priority_score(repo_path, target_paths, path),
            to_posix(path.relative_to(repo_path)).lower(),
        ),
    )
    primary_candidate_files: list[Path] = []
    for pattern in PRIMARY_CODE_PATH_PATTERNS:
        for file_path in candidate_files:
            if pattern in to_posix(file_path.relative_to(repo_path)).lower():
                primary_candidate_files.append(file_path)
                break

    ordered_candidate_files: list[Path] = []
    seen_candidate_paths: set[Path] = set()
    for file_path in primary_candidate_files + candidate_files:
        if file_path in seen_candidate_paths:
            continue
        seen_candidate_paths.add(file_path)
        ordered_candidate_files.append(file_path)

    typed_counts: Counter[str] = Counter()

    for file_path in ordered_candidate_files + related_reference_files:
        evidence_type = classify_file(repo_path, target_paths, file_path)
        if not evidence_type:
            continue
        if typed_counts[evidence_type] >= MAX_EVIDENCE_PER_TYPE:
            continue

        relative_path = to_posix(file_path.relative_to(repo_path))
        snippet = read_snippet(file_path)
        title = build_title(evidence_type, relative_path)
        summary = build_summary(evidence_type, relative_path)
        tags = build_tags(evidence_type, file_path)
        evidence.append(
            make_evidence_item(counter, evidence_type, title, relative_path, summary, snippet, tags)
        )
        counter += 1
        typed_counts[evidence_type] += 1

    return evidence


def candidate_file_priority_score(repo_path: Path, target_paths: list[Path], file_path: Path) -> int:
    evidence_type = classify_file(repo_path, target_paths, file_path)
    if not evidence_type:
        return 1000

    relative_path = to_posix(file_path.relative_to(repo_path)).lower()
    file_name = file_path.name.lower()
    score = 100

    top_signal_names = (
        "scenesessionrequest",
        "sceneprofile",
        "homemapsceneprofile",
        "levelsceneprofile",
        "hubsceneprofile",
        "battletestsceneprofile",
        "editorsession",
        "sceneoperationrouter",
        "scenemodulesetregistry",
        "scenemoduleset",
        "exportcontext",
        "exportpipeline",
        "exportstepregistry",
        "exportsceneblueprintruntimedatastep",
        "exportunifiednavdescriptorstep",
        "homemapruntimepackageexportservice",
        "sceneblueprintruntimedata",
        "homemapruntimepackagedata",
        "unifiednavexportdescriptordata",
    )
    medium_signal_names = (
        "request",
        "profile",
        "session",
        "router",
        "moduleset",
        "runtimepackage",
        "runtimedata",
        "descriptor",
        "pipeline",
        "context",
    )
    low_signal_names = (
        "datamanager",
        "debugwindow",
        "visualizer",
        "actions",
        "events",
        "gui",
        "preview",
        "test",
    )

    if evidence_type == "doc":
        if "离职交接" in file_path.name:
            score -= 90
        if "架构设计" in file_path.name:
            score -= 80
        if relative_path.endswith("design.md"):
            score -= 60
        if "readme" in file_name:
            score -= 40
        if "/specs/" in relative_path:
            score -= 25
        if "proposal" in file_name or "requirements" in file_name:
            score -= 10
        if "tasks" in file_name or "verification" in file_name or "性能" in file_path.name:
            score += 20

    if evidence_type in {"source", "entrypoint", "config", "script"}:
        for name in top_signal_names:
            if name in relative_path:
                score -= 70
        for name in medium_signal_names:
            if name in relative_path:
                score -= 25
        for name in low_signal_names:
            if name in relative_path:
                score += 25

    if evidence_type == "config":
        if file_name.endswith(".asmdef"):
            score -= 25
        if file_name.endswith(".csproj") or file_name.endswith(".sln"):
            score += 20

    if evidence_type == "entrypoint":
        if "entrywindow" in relative_path:
            score -= 40
        if "settingswindow" in relative_path or "prefabimportwindow" in relative_path:
            score += 10

    if evidence_type == "related_reference":
        if "dungeonarchitect" in relative_path or "mapeditor" in relative_path:
            score -= 20

    return score


def scan_candidate_files(repo_path: Path, target_paths: list[Path]) -> Iterable[Path]:
    seen: set[Path] = set()

    repo_level_candidates = [repo_path / "README.md", repo_path / "README", repo_path / "docs"]
    for candidate in repo_level_candidates:
        if candidate.is_file():
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
        elif candidate.is_dir():
            for child in walk_files(candidate):
                if child not in seen:
                    seen.add(child)
                    yield child

    for target_path in target_paths:
        for child in walk_files(target_path):
            if child not in seen:
                seen.add(child)
                yield child

    for child in iter_repo_root_files(repo_path):
        if child not in seen and is_repo_level_relevant(child, repo_path):
            seen.add(child)
            yield child


def iter_repo_root_files(repo_path: Path) -> Iterable[Path]:
    for child in sorted(repo_path.iterdir(), key=lambda item: item.name.lower()):
        if child.name in IGNORED_DIR_NAMES:
            continue
        if child.is_file():
            yield child


def walk_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in IGNORED_DIR_NAMES]
        current_path = Path(current_root)
        for file_name in sorted(file_names):
            yield current_path / file_name


def is_repo_level_relevant(file_path: Path, repo_path: Path) -> bool:
    if not file_path.is_file():
        return False
    if any(part in IGNORED_DIR_NAMES for part in file_path.relative_to(repo_path).parts[:-1]):
        return False

    file_name = file_path.name.lower()
    suffix = file_path.suffix.lower()

    if file_name in CONFIG_FILENAMES:
        return True
    if suffix == ".sln":
        return True
    if file_name.startswith(ENV_PREFIXES):
        return True
    return False


def classify_file(repo_path: Path, target_paths: list[Path], file_path: Path) -> str | None:
    relative = to_posix(file_path.relative_to(repo_path))
    file_name = file_path.name.lower()
    suffix = file_path.suffix.lower()
    inside_target = any(file_path.is_relative_to(target) for target in target_paths)

    if inside_target and (file_name in DOC_FILENAMES or suffix == ".md"):
        return "doc"
    if file_name in CONFIG_FILENAMES or suffix in CONFIG_EXTENSIONS or file_name.startswith(ENV_PREFIXES):
        return "config"
    if not inside_target and is_related_reference_file(repo_path, target_paths, file_path):
        return "related_reference"
    if inside_target and looks_like_entrypoint(file_path):
        return "entrypoint"
    if inside_target and (suffix in SCRIPT_EXTENSIONS or "scripts" in {part.lower() for part in file_path.parts}):
        return "script"
    if inside_target and suffix in SOURCE_EXTENSIONS:
        return "source"
    return None


def looks_like_entrypoint(file_path: Path) -> bool:
    file_name = file_path.name.lower()
    suffix = file_path.suffix.lower()
    stem = file_path.stem.lower()
    noisy_partial_suffixes = (
        ".actions",
        ".events",
        ".gui",
        ".compatibility",
        ".governance",
        ".legacyshell",
        ".workbenchnarrative",
        ".linkedsource",
        ".logs",
        ".templatebatch",
        ".terrainexport",
        ".validation",
        ".advancedworkbench",
        ".preview",
    )

    if file_name in {"program.cs", "startup.cs"}:
        return True
    if suffix == ".unity" and stem in {"main", "entry"}:
        return True
    if suffix in {".cs", ".js", ".ts", ".tsx", ".jsx", ".py"} and stem in {"main", "index", "application", "entry", "bootstrap"}:
        return True
    if suffix == ".cs" and any(token in stem for token in noisy_partial_suffixes):
        return False
    if suffix == ".cs" and (stem.endswith("entrywindow") or stem == "entrywindow"):
        return True
    if suffix == ".cs" and ("window" in stem or "bootstrap" in stem or stem.startswith("entry")):
        return True
    return False


def read_snippet(file_path: Path) -> str:
    if file_path.stat().st_size > MAX_TEXT_FILE_SIZE:
        return "[skipped: file is too large for inline snippet]"

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                return "[skipped: binary or unsupported text encoding]"

    lines = []
    for line in content.splitlines():
        stripped = line.rstrip()
        if stripped:
            lines.append(stripped)
        if len("\n".join(lines)) >= MAX_EVIDENCE_SNIPPET_CHARS:
            break

    snippet = "\n".join(lines).strip()
    return snippet[:MAX_EVIDENCE_SNIPPET_CHARS] if snippet else "[empty text file]"


def build_directory_tree(target_path: Path, repo_path: Path, max_depth: int = 3, max_entries: int = 80) -> str:
    root_label = to_posix(target_path.relative_to(repo_path))
    lines = [root_label]
    entries_written = 0

    def walk(node: Path, prefix: str, depth: int) -> None:
        nonlocal entries_written
        if depth > max_depth or entries_written >= max_entries:
            return

        children = []
        for child in sorted(node.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if child.name in IGNORED_DIR_NAMES:
                continue
            children.append(child)

        for index, child in enumerate(children):
            if entries_written >= max_entries:
                return
            connector = "└── " if index == len(children) - 1 else "├── "
            lines.append(f"{prefix}{connector}{child.name}")
            entries_written += 1
            if child.is_dir():
                extension = "    " if index == len(children) - 1 else "│   "
                walk(child, prefix + extension, depth + 1)

    if target_path.is_dir():
        walk(target_path, "", 1)
    return "\n".join(lines)


def build_title(evidence_type: str, relative_path: str) -> str:
    type_titles = {
        "doc": "文档证据",
        "config": "配置证据",
        "entrypoint": "入口证据",
        "related_reference": "外部引用证据",
        "script": "脚本证据",
        "source": "源码证据",
    }
    return f"{type_titles.get(evidence_type, '证据')}: {relative_path}"


def build_summary(evidence_type: str, relative_path: str) -> str:
    summaries = {
        "doc": "现有文档，可用于理解系统目标、边界、操作说明或历史设计。",
        "config": "配置文件，可能包含依赖、环境变量、构建或部署约束。",
        "entrypoint": "疑似入口文件，适合优先阅读以建立系统启动与装配方式的认知。",
        "related_reference": "目标模块在仓库其他位置的引用，适合用来识别上下游依赖和耦合点。",
        "script": "脚本文件，可能承载构建、部署、运维或修复动作。",
        "source": "目标范围内的源码样本，可用于理解模块职责和实现风格。",
    }
    return f"{summaries.get(evidence_type, '可追溯事实证据')} 路径: {relative_path}"


def build_tags(evidence_type: str, file_path: Path) -> list[str]:
    tags = [evidence_type]
    suffix = file_path.suffix.lower()
    if suffix:
        tags.append(suffix.lstrip("."))
    if "test" in file_path.name.lower():
        tags.append("test")
    return tags


def make_evidence_item(
    counter: int,
    evidence_type: str,
    title: str,
    path: str,
    summary: str,
    snippet: str,
    tags: list[str],
) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev-{counter:03d}",
        type=evidence_type,
        title=title,
        path=path,
        summary=summary,
        snippet=snippet,
        tags=tags,
        sourceStage="scan",
    )


def render_handover(
    task: dict[str, object],
    evidence: list[EvidenceItem],
    understanding_dir: Path,
) -> str:
    template = load_template("handover.md.tpl")

    code_registry = load_code_evidence_registry(understanding_dir)
    change_rows = load_change_impact_rows(understanding_dir)

    docs = sort_docs_for_handover(filter_evidence(evidence, "doc"))
    configs = sort_configs_for_handover(filter_evidence(evidence, "config"), task)
    related_refs = filter_evidence(evidence, "related_reference")

    module_name = infer_module_name(task)
    goal_section = render_goal_section(task)
    main_chain_section = render_main_chain_section(code_registry)
    summary_section = render_summary_section(code_registry, change_rows)
    overview_section = render_overview_section(task, docs, code_registry)
    session_chain_section = render_session_chain_section(code_registry)
    runtime_chain_section = render_runtime_chain_section(code_registry)
    boundary_section = render_boundary_section(configs, related_refs, code_registry)
    verification_section = render_verification_section(change_rows, code_registry)
    open_questions_section = render_open_questions_section(task, evidence)
    reading_order_section = render_reading_order_section(docs, code_registry, related_refs)
    reference_material_section = render_reference_material_section(docs, code_registry, related_refs)

    return template.substitute(
        module_name=module_name,
        goal_section=goal_section,
        main_chain_section=main_chain_section,
        summary_section=summary_section,
        overview_section=overview_section,
        session_chain_section=session_chain_section,
        runtime_chain_section=runtime_chain_section,
        boundary_section=boundary_section,
        verification_section=verification_section,
        open_questions_section=open_questions_section,
        reading_order_section=reading_order_section,
        reference_material_section=reference_material_section,
    )


def load_code_evidence_registry(understanding_dir: Path) -> dict[str, object]:
    path = understanding_dir / "14-code-evidence-registry.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def load_change_impact_rows(understanding_dir: Path) -> list[dict[str, object]]:
    path = understanding_dir / "15-change-impact-matrix.md"
    if not path.exists():
        return []
    return parse_change_impact_rows(path.read_text(encoding="utf-8"))


def parse_change_impact_rows(markdown: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    seen_header = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        if "变更面" in line and "典型编辑位置" in line:
            seen_header = True
            continue
        if not seen_header or line.startswith("| ---"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 9:
            continue

        rows.append(
            {
                "change_surface": cells[0],
                "locations": re.findall(r"`([^`]+)`", cells[1]),
                "direct_layer": cells[2],
                "downstream": cells[3],
                "contracts": cells[4],
                "regressions": cells[5],
                "risk": cells[6],
                "code_ids": re.findall(r"`([^`]+)`", cells[7]),
                "evidence_ids": re.findall(r"`([^`]+)`", cells[8]),
            }
        )
    return rows


def infer_module_name(task: dict[str, object]) -> str:
    target_paths = task.get("targetPaths", [])
    if isinstance(target_paths, list) and target_paths:
        return Path(str(target_paths[0])).name or "模块"
    return "模块"


def format_backticked_paths(paths: list[str], limit: int = 3, separator: str = "、") -> str:
    values = [f"`{path}`" for path in dedupe_keep_order(paths)[:limit] if path]
    return separator.join(values)


def extract_paths_by_patterns(code_registry: dict[str, object], patterns: list[str], limit: int = 4) -> list[str]:
    return dedupe_keep_order(
        [str(item.get("path", "")) for item in find_code_items_by_path_patterns(code_registry, patterns, limit=limit) if item.get("path")]
    )[:limit]


def render_goal_section(task: dict[str, object]) -> str:
    target_paths = task.get("targetPaths", [])
    scope = "、".join(f"`{path}`" for path in target_paths) if isinstance(target_paths, list) else "`unknown`"
    lines = [
        f"这份文档用于离职交接，目标是把 {scope} 当前真实实现讲清楚。",
        "本文只讲当前真实实现，不讲理想形态，也不假设接手人已经了解这套系统的历史背景。",
        "接手同学读完后，应该能快速回答下面几个问题：",
        "- StageDesigner 当前真实启动主链是什么。",
        "- Session / Router / ModuleSet 在这套系统里分别承担什么职责。",
        "- Export 是怎么把编辑态数据继续收敛到 Runtime 契约的。",
        "- HomeMap 在统一宿主里处于什么位置，而不是被误解成完全独立系统。",
        "- 后续如果继续改，应该先看哪些文件，哪些地方最容易引发连锁回归。",
    ]
    return "\n".join(lines)


def render_main_chain_section(code_registry: dict[str, object]) -> str:
    entry_names = "HomeMapEntryWindow / EntryWindow" if extract_paths_by_patterns(code_registry, ["homemapentrywindow", "/editor/windows/entrywindow.cs"], limit=2) else "入口窗口"
    request_names = "SceneSessionRequest"
    profile_names = "SceneProfile / HomeMapSceneProfile"
    session_names = "EditorSession.StartSession(...)"
    router_names = "SceneOperationRouter"
    module_names = "SceneModuleSetRegistry / SceneModuleSet"
    export_names = "ExportContext / ExportPipeline / ExportStepRegistry"
    export_steps = "ExportSceneBlueprintRuntimeDataStep / ExportUnifiedNavDescriptorStep / HomeMapRuntimePackageExportService"
    runtime_names = "SceneBlueprintRuntimeData / HomeMapRuntimePackageData / UnifiedNavExportDescriptorData"
    return "\n".join(
        [
            "入口与启动",
            f"  {entry_names}",
            f"  -> {request_names}",
            f"  -> {profile_names}",
            f"  -> {session_names}",
            "",
            "会话与能力装配",
            f"  -> {router_names}",
            f"  -> {module_names}",
            "",
            "导出到运行时",
            f"  -> {export_names}",
            f"  -> {export_steps}",
            f"  -> {runtime_names}",
        ]
    )


def render_summary_section(code_registry: dict[str, object], change_rows: list[dict[str, object]]) -> str:
    dangerous_surfaces = [row.get("change_surface", "") for row in change_rows[:3] if row.get("change_surface")]
    lines = [
        "- StageDesigner 当前真实启动主链已经统一到 `SceneSessionRequest + SceneProfile + EditorSession.StartSession(...)`，入口侧不应该再把 `level`、`homeMapId` 这类旧参数当成长期主语义。",
        "- `SceneOperationRouter` 是整个系统的关键裁决点，它不只影响显示名，还影响 capability 判断、稳定场景标识、会话分流与后续导出行为。",
        "- `SceneModuleSetRegistry / SceneModuleSet` 决定某类场景启动后到底装配哪些模块，是“新场景类型接入”和“能力收缩/扩展”的首要入口。",
        "- 导出主链的宿主不是某一个单独 step，而是 `ExportContext / ExportPipeline / ExportStepRegistry`；具体步骤只是挂在这套宿主之上的执行单元。",
        "- 运行时真正消费的落点是 `SceneBlueprintRuntimeData`、`HomeMapRuntimePackageData`、`UnifiedNavExportDescriptorData`，这三类数据契约比编辑器窗口本身更接近下游真实边界。",
        "- HomeMap 不是完全独立的一套编辑器宿主，而是在统一会话主链上，通过 `HomeMapSceneProfile` 与 `HomeMapRuntimePackageExportService` 扩展出自己的专属工作流。",
    ]
    if dangerous_surfaces:
        lines.append(f"- 当前最危险的几个改动面是：{'、'.join(dangerous_surfaces)}。")
    return "\n".join(lines)


def render_overview_section(task: dict[str, object], docs: list[EvidenceItem], code_registry: dict[str, object]) -> str:
    lines = [
        "从现有文档和代码可以把 StageDesigner 理解成一套“统一场景宿主 + 多场景类型接入 + 导出到运行时契约”的通用场景设计工具，而不只是一个单独窗口。",
        "它的核心思路不是按功能点零散生长，而是先把场景抽象成统一请求与 profile，再通过会话、模块装配、导出和运行时契约把整条链路串起来。",
        "",
        "当前目录里最值得先建立认知的是：",
        "- `Documentations~`：架构、演进和 HomeMap spec 文档，是理解背景和演进方向的第一入口。",
        "- `Editor/SceneModel` 与 `Editor/SceneProfiles`：定义 `SceneIdentity / SceneProfile / SceneSessionRequest` 这组统一模型。",
        "- `Editor/Session`：定义真实会话宿主、能力路由和模块装配，是主链中心。",
        "- `Editor/Export`：定义导出上下文、流水线、步骤注册和具体 step。",
        "- `Editor/HomeMap`：承载 HomeMap 的入口、恢复与 runtime package 导出。",
        "- `Runtime/FrameSync` 与 `Runtime/HomeMap`：承载运行时最终消费的数据契约。",
        "- `Editor/Integration` 与 `Editor/Bridge`：承载对外桥接和联调边界。",
    ]
    if docs:
        lines.append("")
        lines.append(f"背景文档建议先看 {format_backticked_paths([item.path for item in docs[:3]], limit=3)}。")
    session_paths = extract_paths_by_patterns(code_registry, ["scenesessionrequest", "sceneprofile", "editorsession", "sceneoperationrouter"], limit=4)
    if session_paths:
        lines.append(f"如果只打算先抓主链，不要先扫全目录，优先抓住 {format_backticked_paths(session_paths, limit=4)}。")
    return "\n".join(lines)


def render_session_chain_section(code_registry: dict[str, object]) -> str:
    entry_paths = extract_paths_by_patterns(code_registry, ["homemapentrywindow", "/editor/windows/entrywindow.cs"], limit=2)
    model_paths = extract_paths_by_patterns(code_registry, ["scenesessionrequest", "/editor/sceneprofiles/sceneprofile.cs", "homemapsceneprofile"], limit=3)
    session_paths = extract_paths_by_patterns(code_registry, ["editorsession", "sceneoperationrouter", "scenemodulesetregistry", "scenemoduleset"], limit=4)
    lines = [
        "StageDesigner 的统一会话主链可以按“入口构造请求 -> 会话宿主接管 -> 路由判定能力 -> 模块集合完成装配”来理解。",
        "",
        f"- 启动入口：{format_backticked_paths(entry_paths, limit=2)} 负责把用户选择和场景信息整理成统一请求，而不是直接把旧业务参数散到各处。",
        f"- 请求模型：{format_backticked_paths(model_paths, limit=3)} 定义了 `SceneIdentity / SceneProfile / SceneSessionRequest` 这组统一数据模型，后续链路都围绕它工作。",
        f"- 会话宿主：{format_backticked_paths(extract_paths_by_patterns(code_registry, ['editorsession'], limit=1), limit=1)} 中的 `StartSession(SceneSessionRequest request)` 是真实入口，负责参数校验、Redux 初始化、模块配置、事件注册与生命周期管理。",
        f"- 路由裁决：{format_backticked_paths(extract_paths_by_patterns(code_registry, ['sceneoperationrouter'], limit=1), limit=1)} 负责 `SupportsCapability`、`GetStableSceneId`、`GetSceneDisplayName` 这类关键判断，是接手时最不能低估的一层。",
        f"- 模块装配：{format_backticked_paths(extract_paths_by_patterns(code_registry, ['scenemodulesetregistry', 'scenemoduleset'], limit=2), limit=2)} 决定当前场景会真正装配哪些编辑模块，因此新场景类型接入首先要回到这里，而不是先改导出或 UI。",
        "",
        "这条链对接手人的意义是：以后看到任何新入口、新场景类型、新 capability，先问它有没有被正确包装成 `SceneSessionRequest`，有没有走进 `EditorSession.StartSession(...)`，有没有在 `SceneModuleSetRegistry` 里完成模块装配。",
    ]
    if session_paths:
        lines.append(f"当前主链最核心的一组实现文件是：{format_backticked_paths(session_paths, limit=4)}。")
    return "\n".join(lines)


def render_runtime_chain_section(code_registry: dict[str, object]) -> str:
    export_host_paths = extract_paths_by_patterns(code_registry, ["exportcontext", "exportpipeline", "exportstepregistry"], limit=3)
    export_step_paths = extract_paths_by_patterns(code_registry, ["exportsceneblueprintruntimedata", "exportunifiednavdescriptor", "runtimepackageexportservice"], limit=3)
    runtime_paths = extract_paths_by_patterns(
        code_registry,
        [
            "/runtime/framesync/data/sceneblueprintruntimedata.cs",
            "/runtime/homemap/data/homemapruntimepackagedata.cs",
            "/runtime/homemap/data/unifiednavexportdescriptordata.cs",
        ],
        limit=3,
    )
    lines = [
        "导出到运行时的这条链，不应该被理解成“点一下导出按钮，某个工具类写文件”。更准确的理解是：先建立导出上下文，再由流水线和步骤注册器安排具体步骤，最后把数据收敛成运行时消费的契约。",
        "",
        f"- 导出宿主：{format_backticked_paths(export_host_paths, limit=3)} 负责从当前会话提取 `SceneIdentity / SceneProfile / CurrentSceneRequest`，构建执行计划，并按顺序调度步骤。",
        f"- 关键步骤：{format_backticked_paths(export_step_paths, limit=3)} 负责把场景蓝图、统一导航描述和 HomeMap runtime package 等具体产物写出来。",
        f"- 运行时落点：{format_backticked_paths(runtime_paths, limit=3)} 才是下游真正消费的契约。接手时要把它们当成运行时边界，而不是普通 editor data class。",
        "",
        "这条链最容易被误改的地方，是只盯着某一个 `ExportStep` 排查问题，而没有回头确认 `ExportContext` 提供的数据、`ExportPipeline` 的执行计划，以及 `ExportStepRegistry` 的注册顺序是否已经变了。",
        "如果 HomeMap 导出出问题，也不要只看 `HomeMapRuntimePackageExportService`，还要一起确认统一导航描述、蓝图运行时数据和最终 runtime package 三类产物是否同时保持一致。",
    ]
    return "\n".join(lines)


def render_boundary_section(
    configs: list[EvidenceItem],
    related_refs: list[EvidenceItem],
    code_registry: dict[str, object],
) -> str:
    lines = [
        "- 新入口或新场景类型接入时，不要重新把 `int level`、`string homeMapId` 之类旧参数当成主语义，应该先包装成 `SceneSessionRequest`。",
        "- 新逻辑不要绕开 `EditorSession.StartSession(...)` 自己拼一套局部初始化；否则 Redux、模块初始化和事件注册很容易出现缺口。",
        "- 改 `SceneOperationRouter` 时，不只是 UI 名称会变，`capability` 判断、稳定场景标识、导出分流和恢复逻辑都可能一起被波及。",
        "- 改 `SceneModuleSetRegistry / SceneModuleSet` 时，要同时确认模块集合和初始化顺序，而不是只看某个模块类本身。",
        "- 改导出逻辑时，不要只盯单个 `ExportStep`，至少要连带检查 `ExportContext`、`ExportPipeline`、`ExportStepRegistry`。",
        "- 改 `SceneBlueprintRuntimeData`、`HomeMapRuntimePackageData`、`UnifiedNavExportDescriptorData` 时，要按运行时契约变更处理，而不是按普通编辑器模型处理。",
    ]
    if configs:
        lines.append(f"- 程序集和配置边界主要落在 {format_backticked_paths([item.path for item in configs], limit=3)}，这部分改动往往会引起编译边界或目录约束变化。")
    if related_refs:
        lines.append(f"- StageDesigner 与仓库内其他模块存在真实耦合，当前至少已经扫到 {format_backticked_paths([item.path for item in related_refs], limit=3)} 这类外部引用，联调边界不能省略。")
    bridge_paths = extract_paths_by_patterns(code_registry, ["stagedesignerintegration", "mapeditorbridge"], limit=2)
    if bridge_paths:
        lines.append(f"- 对外桥接主要集中在 {format_backticked_paths(bridge_paths, limit=2)}，这些位置的改动要明确谁是主导方、谁负责回归。")
    return "\n".join(lines)


def render_verification_section(change_rows: list[dict[str, object]], code_registry: dict[str, object]) -> str:
    lines = [
        "1. 入口相关改动后，`HomeMapEntryWindow` 与 `EntryWindow` 都要能正确构造 `SceneSessionRequest`，并且顺利进入 `EditorSession.StartSession(...)`。",
        "2. `SceneProfile / SceneOperationRouter / SceneModuleSetRegistry` 改动后，要确认场景显示名、稳定场景标识、capability 判断和模块装配结果都没有回归。",
        "3. 导出链改动后，要确认 `ExportContext -> ExportPipeline -> ExportStepRegistry` 这条宿主链仍然成立，不是只让单个 step 看起来能跑。",
        "4. `ExportSceneBlueprintRuntimeDataStep`、`ExportUnifiedNavDescriptorStep`、`HomeMapRuntimePackageExportService` 改动后，要一起检查产物路径、产物内容和步骤顺序。",
        "5. 运行时契约改动后，要确认 `SceneBlueprintRuntimeData`、`HomeMapRuntimePackageData`、`UnifiedNavExportDescriptorData` 的下游消费没有被破坏。",
        "6. `Integration / Bridge` 改动后，要补一轮与外部模块的联调验证，而不是只看 StageDesigner 自己目录内是否编译通过。",
    ]
    if change_rows:
        top_rows = "、".join(str(row.get("change_surface", "")) for row in change_rows[:3] if row.get("change_surface"))
        if top_rows:
            lines.append(f"7. 当前优先级最高的几类回归面是：{top_rows}。")
    return "\n".join(lines)


def render_reading_order_section(
    docs: list[EvidenceItem],
    code_registry: dict[str, object],
    related_refs: list[EvidenceItem],
) -> str:
    doc_paths = [item.path for item in docs[:3]]
    entry_paths = extract_paths_by_patterns(code_registry, ["homemapentrywindow", "/editor/windows/entrywindow.cs", "scenesessionrequest", "/editor/sceneprofiles/sceneprofile.cs", "homemapsceneprofile"], limit=5)
    session_paths = extract_paths_by_patterns(code_registry, ["editorsession", "sceneoperationrouter", "scenemodulesetregistry", "scenemoduleset"], limit=4)
    export_paths = extract_paths_by_patterns(code_registry, ["exportcontext", "exportpipeline", "exportstepregistry", "exportsceneblueprintruntimedata", "exportunifiednavdescriptor", "runtimepackageexportservice"], limit=6)
    runtime_paths = extract_paths_by_patterns(
        code_registry,
        [
            "/runtime/framesync/data/sceneblueprintruntimedata.cs",
            "/runtime/homemap/data/homemapruntimepackagedata.cs",
            "/runtime/homemap/data/unifiednavexportdescriptordata.cs",
            "stagedesignerintegration",
            "mapeditorbridge",
        ],
        limit=5,
    )

    lines = [
        f"1. 先看背景文档：{format_backticked_paths(doc_paths, limit=3)}。",
        f"2. 再看启动入口与请求模型：{format_backticked_paths(entry_paths, limit=5)}。",
        f"3. 再看会话宿主与能力装配：{format_backticked_paths(session_paths, limit=4)}。",
        f"4. 再看导出主链：{format_backticked_paths(export_paths, limit=6)}。",
        f"5. 最后看运行时契约与对外桥接：{format_backticked_paths(runtime_paths, limit=5)}。",
    ]
    if related_refs:
        lines.append(f"6. 如果后续需要联调，再回看外部引用：{format_backticked_paths([item.path for item in related_refs], limit=3)}。")
    return "\n".join(lines)


def render_reference_material_section(
    docs: list[EvidenceItem],
    code_registry: dict[str, object],
    related_refs: list[EvidenceItem],
) -> str:
    references = []
    for item in docs[:4]:
        references.append(f"- `{item.path}`")
    for path in extract_priority_code_paths(code_registry, limit=8):
        references.append(f"- `{path}`")
    for item in related_refs[:2]:
        references.append(f"- `{item.path}`")
    return "\n".join(dedupe_keep_order(references))


def render_open_questions(task: dict[str, object], evidence: list[EvidenceItem]) -> str:
    template = load_template("open-questions.md.tpl")
    questions = build_open_questions(task, evidence)
    questions_markdown = "\n".join(f"{index}. {question}" for index, question in enumerate(questions, start=1))
    return template.substitute(
        task_id=task["taskId"],
        audience=task["audience"],
        questions=questions_markdown,
    )


def render_scan_summary(task: dict[str, object], evidence: list[EvidenceItem]) -> str:
    template = load_template("scan-summary.md.tpl")
    type_counter = Counter(item.type for item in evidence)
    counts = "\n".join(f"- `{item_type}`: {count}" for item_type, count in sorted(type_counter.items()))
    highlighted = []
    for evidence_type in ("directory_tree", "doc", "related_reference", "entrypoint", "config", "script"):
        for item in filter_evidence(evidence, evidence_type)[:3]:
            highlighted.append(f"- `{item.id}` `{item.path}`: {item.summary}")
    return template.substitute(
        task_id=task["taskId"],
        repo_path=task["repoPath"],
        target_paths="\n".join(f"- `{path}`" for path in task["targetPaths"]),
        counts=counts,
        highlighted="\n".join(highlighted) if highlighted else "- 无高亮证据",
    )


def build_code_evidence_registry(
    repo_path: Path,
    target_paths: list[Path],
    evidence: list[EvidenceItem],
    task: dict[str, object],
) -> dict[str, object]:
    candidates: list[tuple[int, EvidenceItem, Path]] = []
    priority = {
        "entrypoint": 0,
        "config": 1,
        "source": 2,
        "script": 3,
        "related_reference": 4,
    }

    for item in evidence:
        if item.type not in priority:
            continue
        file_path = repo_path / item.path
        if should_skip_code_registry_file(file_path):
            continue
        if file_path.exists() and file_path.is_file():
            candidates.append((priority[item.type], item, file_path))

    all_anchors: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, int]] = set()

    for _, evidence_item, file_path in sorted(
        candidates,
        key=lambda part: (
            compute_code_candidate_score(part[1]) + part[0] * 15,
            part[1].path.lower(),
        ),
    ):
        for anchor in extract_code_anchors(repo_path, target_paths, file_path, evidence_item):
            key = (str(anchor["path"]), str(anchor["symbol"]), int(anchor["startLine"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_anchors.append(anchor)

    items: list[dict[str, object]] = []
    category_counts: Counter[str] = Counter()
    selected_keys: set[tuple[str, str, int]] = set()

    def try_append(anchor: dict[str, object]) -> bool:
        if is_noise_code_anchor(anchor):
            return False
        category = infer_change_surface_category(anchor)
        key = (str(anchor["path"]), str(anchor["symbol"]), int(anchor["startLine"]))
        if key in selected_keys:
            return False
        if category_counts[category] >= MAX_CODE_EVIDENCE_PER_CATEGORY:
            return False
        if len(items) >= MAX_CODE_EVIDENCE_ITEMS:
            return False
        selected_keys.add(key)
        category_counts[category] += 1
        items.append(anchor)
        return True

    for pattern in PRIMARY_CODE_PATH_PATTERNS:
        matched = [
            anchor
            for anchor in all_anchors
            if pattern in str(anchor.get("path", "")).lower()
        ]
        for anchor in sorted(
            matched,
            key=lambda item: (
                code_anchor_priority_score(item),
                str(item.get("path", "")).lower(),
                int(item.get("startLine", 1)),
            ),
        ):
            if try_append(anchor):
                break

    preferred_categories = ["entrypoint", "session", "home_map", "export", "sync", "runtime", "bridge"]
    for category in preferred_categories:
        for anchor in all_anchors:
            if infer_change_surface_category(anchor) == category and try_append(anchor):
                break

    for anchor in all_anchors:
        try_append(anchor)

    for index, anchor in enumerate(items, start=1):
        anchor["id"] = f"code-{index:03d}"

    return {
        "meta": {
            "taskId": task["taskId"],
            "module": format_module_label(task),
            "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "confidence": "medium",
        },
        "items": items,
    }


def should_skip_code_registry_file(file_path: Path) -> bool:
    lowered = file_path.name.lower()
    return lowered.endswith(".sln") or lowered.endswith(".csproj")


def is_noise_code_anchor(anchor: dict[str, object]) -> bool:
    path = str(anchor.get("path", "")).lower()
    symbol = str(anchor.get("symbol", ""))
    if path.endswith(".sln") or path.endswith(".csproj"):
        return True
    if symbol.startswith("GUID:"):
        return True
    return False


def code_anchor_priority_score(anchor: dict[str, object]) -> int:
    path = str(anchor.get("path", "")).lower()
    symbol = str(anchor.get("symbol", ""))
    symbol_lower = symbol.lower()
    kind = str(anchor.get("kind", ""))

    score = 100
    if kind == "type":
        score -= 50
    elif kind == "method":
        score -= 20
    elif kind == "config":
        score += 10

    preferred_methods = {
        "startsession": -100,
        "showswindow": -30,
        "showwindow": -30,
        "tryresolve": -70,
        "initialize": -60,
        "create": -40,
        "buildexecutionplan": -70,
        "executeall": -60,
        "execute": -40,
        "exportruntimepackage": -80,
        "validatebasic": -50,
        "normalizecontractfields": -45,
        "getscenedisplayname": -55,
        "getstablesceneid": -55,
        "supportscapability": -55,
        "getoutputpath": -35,
    }
    score += preferred_methods.get(symbol_lower, 0)

    if path.endswith("/editor/session/editorsession.cs") and symbol == "StartSession":
        score -= 120
    if path.endswith("/editor/session/sceneoperationrouter.cs") and symbol in {"SupportsCapability", "GetStableSceneId", "GetSceneDisplayName"}:
        score -= 100
    if path.endswith("/editor/session/scenemodulesetregistry.cs") and symbol == "TryResolve":
        score -= 90
    if path.endswith("/runtime/framesync/data/sceneblueprintruntimedata.cs") and kind == "type":
        score -= 80
    if path.endswith("/runtime/homemap/data/homemapruntimepackagedata.cs") and kind == "type":
        score -= 100
    if path.endswith("/runtime/homemap/data/unifiednavexportdescriptordata.cs") and kind == "type":
        score -= 100
    if path.endswith("/editor/export/exportpipeline.cs") and symbol in {"BuildExecutionPlan", "ExecuteAll"}:
        score -= 100
    if path.endswith("/editor/export/steps/exportsceneblueprintruntimedatastep.cs") and symbol == "Execute":
        score -= 90
    if path.endswith("/editor/homemap/services/homemapruntimepackageexportservice.cs") and symbol == "ExportRuntimePackage":
        score -= 100

    return score


def extract_code_anchors(
    repo_path: Path,
    target_paths: list[Path],
    file_path: Path,
    evidence_item: EvidenceItem,
) -> list[dict[str, object]]:
    text = read_text_content(file_path)
    if not text:
        return []

    relative_path = to_posix(file_path.relative_to(repo_path))
    inside_target = any(file_path.is_relative_to(target) for target in target_paths)
    category = categorize_code_path(relative_path, evidence_item.type)
    anchors: list[dict[str, object]] = []
    suffix = file_path.suffix.lower()

    if suffix in {".cs", ".java", ".js", ".jsx", ".ts", ".tsx", ".py"}:
        anchors.extend(extract_symbol_anchors(relative_path, text, evidence_item, inside_target, category))
    elif suffix in {".json", ".yaml", ".yml", ".xml", ".props", ".targets", ".asmdef", ".csproj", ".sln"}:
        anchors.extend(extract_config_anchors(relative_path, text, evidence_item, inside_target, category))
    else:
        anchors.extend(extract_file_level_anchor(relative_path, text, evidence_item, inside_target, category))

    return anchors[:MAX_CODE_EVIDENCE_PER_FILE]


def extract_symbol_anchors(
    relative_path: str,
    text: str,
    evidence_item: EvidenceItem,
    inside_target: bool,
    category: str,
) -> list[dict[str, object]]:
    anchors: list[dict[str, object]] = []
    class_pattern = re.compile(
        r"^\s*(?:public|internal|private|protected)?\s*(?:sealed\s+|static\s+|abstract\s+|partial\s+)*"
        r"(class|interface|struct|enum)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.MULTILINE,
    )
    method_pattern = re.compile(
        r"^\s*(?:public|internal|private|protected)\s+"
        r"(?:static\s+|virtual\s+|override\s+|abstract\s+|sealed\s+|async\s+|extern\s+|new\s+|partial\s+)*"
        r"[\w<>\[\],?.]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
        re.MULTILINE,
    )

    matches: list[tuple[int, str, str]] = []
    for match in class_pattern.finditer(text):
        name = match.group(2)
        if is_high_signal_symbol(name, relative_path, evidence_item.type):
            matches.append((match.start(), "type", name))

    for match in method_pattern.finditer(text):
        name = match.group(1)
        if is_high_signal_symbol(name, relative_path, evidence_item.type):
            matches.append((match.start(), "method", name))

    if not matches:
        return extract_file_level_anchor(relative_path, text, evidence_item, inside_target, category)

    for position, kind, name in sorted(
        matches,
        key=lambda part: (
            score_symbol_anchor(relative_path, part[1], part[2]),
            part[0],
        ),
    )[:MAX_CODE_EVIDENCE_PER_FILE]:
        line_no = compute_line_number(text, position)
        anchors.append(
            make_code_anchor(
                relative_path=relative_path,
                kind=kind,
                symbol=name,
                line_no=line_no,
                evidence_item=evidence_item,
                inside_target=inside_target,
                category=category,
            )
        )
    return anchors


def score_symbol_anchor(relative_path: str, kind: str, symbol_name: str) -> int:
    score = 100
    symbol_lower = symbol_name.lower()
    path = relative_path.lower()

    if kind == "type":
        score -= 50
    elif kind == "method":
        score -= 10

    preferred_methods = {
        "startsession": -100,
        "tryresolve": -75,
        "initialize": -60,
        "create": -40,
        "buildexecutionplan": -70,
        "executeall": -60,
        "execute": -40,
        "exportruntimepackage": -85,
        "supportscapability": -55,
        "getstablesceneid": -55,
        "getscenedisplayname": -55,
        "validatebasic": -50,
        "normalizecontractfields": -45,
        "getoutputpath": -35,
        "showwindow": -25,
    }
    score += preferred_methods.get(symbol_lower, 0)

    if path.endswith("/editor/session/editorsession.cs") and symbol_name == "StartSession":
        score -= 130
    if path.endswith("/editor/session/sceneoperationrouter.cs") and symbol_name in {"SupportsCapability", "GetStableSceneId", "GetSceneDisplayName"}:
        score -= 100
    if path.endswith("/editor/session/scenemodulesetregistry.cs") and symbol_name == "TryResolve":
        score -= 90
    if path.endswith("/runtime/framesync/data/sceneblueprintruntimedata.cs") and kind == "type":
        score -= 80
    if path.endswith("/runtime/homemap/data/homemapruntimepackagedata.cs") and kind == "type":
        score -= 100
    if path.endswith("/runtime/homemap/data/unifiednavexportdescriptordata.cs") and kind == "type":
        score -= 100
    if path.endswith("/editor/export/exportpipeline.cs") and symbol_name in {"BuildExecutionPlan", "ExecuteAll"}:
        score -= 100
    if path.endswith("/editor/export/steps/exportsceneblueprintruntimedatastep.cs") and symbol_name == "Execute":
        score -= 100
    if path.endswith("/editor/homemap/services/homemapruntimepackageexportservice.cs") and symbol_name == "ExportRuntimePackage":
        score -= 100

    return score


def extract_config_anchors(
    relative_path: str,
    text: str,
    evidence_item: EvidenceItem,
    inside_target: bool,
    category: str,
) -> list[dict[str, object]]:
    patterns = [
        re.compile(r'"name"\s*:\s*"([^"]+)"'),
        re.compile(r'"references"\s*:\s*\[\s*"([^"]+)"'),
        re.compile(r'"rootNamespace"\s*:\s*"([^"]+)"'),
        re.compile(r"Project\(.*?\)\s*=\s*\"([^\"]+)\""),
        re.compile(r"<PackageReference[^>]+Include=\"([^\"]+)\"", re.IGNORECASE),
    ]
    anchors: list[dict[str, object]] = []
    seen_labels: set[str] = set()

    for pattern in patterns:
        for match in pattern.finditer(text):
            label = match.group(1)
            if not label or label.lower() in {"true", "false"} or label in seen_labels:
                continue
            if label.startswith("GUID:"):
                continue
            seen_labels.add(label)
            line_no = compute_line_number(text, match.start())
            anchors.append(
                make_code_anchor(
                    relative_path=relative_path,
                    kind="config",
                    symbol=label,
                    line_no=line_no,
                    evidence_item=evidence_item,
                    inside_target=inside_target,
                    category=category,
                )
            )
            if len(anchors) >= MAX_CODE_EVIDENCE_PER_FILE:
                return anchors

    return anchors or extract_file_level_anchor(relative_path, text, evidence_item, inside_target, category)


def extract_file_level_anchor(
    relative_path: str,
    text: str,
    evidence_item: EvidenceItem,
    inside_target: bool,
    category: str,
) -> list[dict[str, object]]:
    line_no = first_non_empty_line_number(text)
    return [
        make_code_anchor(
            relative_path=relative_path,
            kind="file",
            symbol=Path(relative_path).name,
            line_no=line_no,
            evidence_item=evidence_item,
            inside_target=inside_target,
            category=category,
        )
    ]


def make_code_anchor(
    relative_path: str,
    kind: str,
    symbol: str,
    line_no: int,
    evidence_item: EvidenceItem,
    inside_target: bool,
    category: str,
) -> dict[str, object]:
    metadata = build_anchor_metadata(relative_path, evidence_item, category)
    return {
        "scope": "in_target" if inside_target else "outside_target_consumer",
        "kind": kind,
        "path": relative_path,
        "symbol": symbol,
        "startLine": line_no,
        "endLine": line_no,
        "role": metadata["role"],
        "supports": metadata["supports"],
        "impacts": metadata["impacts"],
        "linkedEvidence": [evidence_item.id],
        "consumers": metadata["consumers"],
        "confidence": metadata["confidence"],
        "notes": metadata["notes"],
    }


def build_anchor_metadata(relative_path: str, evidence_item: EvidenceItem, category: str) -> dict[str, object]:
    configs: dict[str, dict[str, object]] = {
        "entrypoint": {
            "role": "用于定位入口启动链中的关键代码锚点。",
            "supports": ["入口层如何进入当前模块或工作流。"],
            "impacts": ["入口启动链", "参数传递", "场景或应用打开流程"],
            "consumers": ["entry_and_request"],
            "confidence": "medium",
            "notes": "适合和会话或配置层一起联读。",
        },
        "session": {
            "role": "用于定位会话、路由或状态分流的关键代码锚点。",
            "supports": ["会话模型、路由规则或状态派生方式。"],
            "impacts": ["会话模型", "路由规则", "状态派生"],
            "consumers": ["session_host"],
            "confidence": "medium",
            "notes": "改这一层通常会波及上下游多个链路。",
        },
        "home_map": {
            "role": "用于定位 HomeMap 专属接入、数据或导出行为的关键代码锚点。",
            "supports": ["HomeMap 的专属工作流、产物或接入方式。"],
            "impacts": ["HomeMap 工作流", "HomeMap 产物", "命名场景处理"],
            "consumers": ["home_map_stack"],
            "confidence": "medium",
            "notes": "HomeMap 既属于统一宿主，又保留专属实现。",
        },
        "export": {
            "role": "用于定位导出上下文、执行计划或导出步骤的关键代码锚点。",
            "supports": ["导出链如何收集上下文并执行。"],
            "impacts": ["导出上下文", "导出计划", "导出产物"],
            "consumers": ["export_sync"],
            "confidence": "medium",
            "notes": "导出问题通常要同时检查 context 和 pipeline。",
        },
        "sync": {
            "role": "用于定位同步上下文、同步目录或同步计划的关键代码锚点。",
            "supports": ["同步链如何选择源目录和目标目录。"],
            "impacts": ["同步上下文", "外部目录同步", "目标路径"],
            "consumers": ["export_sync"],
            "confidence": "medium",
            "notes": "同步问题往往和个人设置或外部目录有关。",
        },
        "runtime": {
            "role": "用于定位运行时契约或运行时消费边界的关键代码锚点。",
            "supports": ["运行时真正消费的目录、schema 或边界约束。"],
            "impacts": ["运行时契约", "对外同步", "下游消费"],
            "consumers": ["runtime_contracts"],
            "confidence": "medium",
            "notes": "适合和导出产物、外部消费者一起联读。",
        },
        "bridge": {
            "role": "用于定位跨模块或外部系统桥接的关键代码锚点。",
            "supports": ["模块的上下游耦合点和外部消费者。"],
            "impacts": ["外部桥接", "集成边界", "联调回归"],
            "consumers": ["integration_bridges"],
            "confidence": "medium",
            "notes": "桥接改动最容易引发隐性联动问题。",
        },
        "config": {
            "role": "用于定位配置、程序集或依赖声明中的关键代码锚点。",
            "supports": ["路径、依赖或程序集边界的正式声明。"],
            "impacts": ["配置边界", "程序集依赖", "构建或运行约束"],
            "consumers": ["config_naming"],
            "confidence": "medium",
            "notes": "配置改动往往不是本仓库单点问题。",
        },
    }
    resolved = configs.get(category, configs["config"]).copy()
    if evidence_item.type == "related_reference":
        resolved["consumers"] = ["integration_bridges"]
        resolved["notes"] = "该锚点位于 targetPaths 之外，用于补齐真实上下游影响面。"
    if "framesync" in relative_path.lower():
        resolved["consumers"] = ["runtime_contracts"]
        resolved["impacts"] = ["运行时契约", "外部同步", "编译边界"]
    return resolved


def render_change_impact_matrix(task: dict[str, object], code_registry: dict[str, object]) -> str:
    template = load_template("understanding/15-change-impact-matrix.md.tpl")
    rows = build_change_impact_rows(code_registry.get("items", []))
    matrix_rows = "\n".join(
        "| {change_surface} | `{locations}` | {direct_layer} | {downstream} | {contracts} | {regressions} | {risk} | {code_refs} | {evidence_refs} |".format(
            change_surface=row["change_surface"],
            locations="` `".join(row["locations"]),
            direct_layer=row["direct_layer"],
            downstream=row["downstream"],
            contracts=row["contracts"],
            regressions=row["regressions"],
            risk=row["risk"],
            code_refs=" ".join(f"`{code_id}`" for code_id in row["code_ids"]),
            evidence_refs=" ".join(f"`{ev_id}`" for ev_id in row["evidence_ids"]) or "`ev-unknown`",
        )
        for row in rows
    ) or "| 未识别到稳定改动面 | `N/A` | config | 需人工补充 | 需人工补充 | 需人工补充 | medium | `code-unknown` | `ev-unknown` |"

    high_risk_rows = [row for row in rows if row["risk"] == "high"][:3]
    high_risk_sections = "\n\n".join(render_high_risk_section(row) for row in high_risk_rows)
    top_risk_items = "\n".join(
        f"{index}. {row['change_surface']}" for index, row in enumerate(high_risk_rows[:3], start=1)
    ) or "1. 需要人工补充\n2. 需要人工补充\n3. 需要人工补充"

    return template.substitute(
        task_id=task["taskId"],
        module_path=format_module_label(task),
        updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        matrix_rows=matrix_rows,
        high_risk_sections=high_risk_sections,
        top_risk_items=top_risk_items,
    )


def build_change_impact_rows(code_items: list[dict[str, object]]) -> list[dict[str, object]]:
    definitions = [
        (
            "entrypoint",
            "修改入口与请求构造",
            "host/config",
            "启动参数、入口行为和上游输入会变化",
            "`SceneSessionRequest`、入口参数、打开或创建策略",
            "启动主流程、参数显示、入口可达性",
            "high",
        ),
        (
            "session",
            "修改会话、路由或状态派生",
            "host/subsystem",
            "会话分流、显示名、稳定标识和 capability 判断会变化",
            "会话模型、路由规则、状态派生结果",
            "启动、显示名、关键分支、相关导出或同步链",
            "high",
        ),
        (
            "home_map",
            "修改 HomeMap 专属工作流或命名产物",
            "subsystem/runtime/config",
            "HomeMap 启动、恢复、导出和运行时消费会变化",
            "HomeMap 命名约定、RuntimePackage、蓝图或导航产物",
            "HomeMap 启动、保存、导出、下游消费",
            "high",
        ),
        (
            "export",
            "修改导出上下文或导出计划",
            "subsystem/config",
            "导出目录、步骤依赖和产物写出结果会变化",
            "`ExportContext`、导出步骤、导出目录",
            "一键导出、单步导出、产物目录检查",
            "high",
        ),
        (
            "sync",
            "修改同步上下文或同步计划",
            "subsystem/config/manual_process",
            "源目录、目标目录和外部同步结果会变化",
            "`SyncContext`、同步目录、同步计划",
            "同步预览、目标目录、关键外部工程验证",
            "high",
        ),
        (
            "runtime",
            "修改运行时契约或运行时消费边界",
            "runtime/contract",
            "运行时 schema、外部同步或下游加载方式会变化",
            "运行时 schema、asmdef、运行时目录",
            "运行时加载、外部同步、编译边界检查",
            "high",
        ),
        (
            "bridge",
            "修改外部桥接与相关引用",
            "bridge/subsystem",
            "上下游模块联调点和外部消费者会变化",
            "桥接接口、外部路径、集成上下文",
            "桥接打开流程、上下游联调、路径校验",
            "medium",
        ),
        (
            "config",
            "修改配置、程序集或依赖声明",
            "config",
            "依赖、路径和构建约束会变化",
            "程序集依赖、配置键、路径约束",
            "配置读取、编译、运行环境检查",
            "medium",
        ),
    ]

    rows: list[dict[str, object]] = []
    for category, label, direct_layer, downstream, contracts, regressions, risk in definitions:
        matched = [item for item in code_items if infer_change_surface_category(item) == category]
        if not matched:
            continue

        locations = dedupe_keep_order([str(item["path"]) for item in matched])[:3]
        code_ids = [str(item["id"]) for item in matched[:6]]
        evidence_ids = sorted(
            {
                linked_id
                for item in matched
                for linked_id in item.get("linkedEvidence", [])
            }
        )[:6]

        rows.append(
            {
                "change_surface": label,
                "locations": locations,
                "direct_layer": direct_layer,
                "downstream": downstream,
                "contracts": contracts,
                "regressions": regressions,
                "risk": risk,
                "code_ids": code_ids,
                "evidence_ids": evidence_ids,
            }
        )
    return rows


def render_high_risk_section(row: dict[str, object]) -> str:
    locations = "、".join(f"`{path}`" for path in row["locations"])
    code_refs = " ".join(f"`{code_id}`" for code_id in row["code_ids"])
    evidence_refs = " ".join(f"`{ev_id}`" for ev_id in row["evidence_ids"])
    return "\n".join(
        [
            f"### 变更面：{row['change_surface']}",
            "",
            f"- 为什么有人会改这里：这一层直接承接 {row['contracts']}，是常见的统一、修复或扩展入口。",
            f"- 典型编辑位置：{locations}",
            f"- 直接影响层：{row['direct_layer']}",
            "",
            "#### 立即影响",
            "",
            f"- {row['downstream']}",
            "",
            "#### 连锁影响",
            "",
            f"- `{row['contracts']}` 相关链路会一起受影响。",
            f"- `{row['regressions']}` 对应的回归面需要一起检查。",
            "",
            "#### 最容易漏掉的回归项",
            "",
            f"- {row['regressions']}",
            "",
            "#### 推荐验证顺序",
            "",
            "1. 先验证直接编辑位置对应的主流程",
            "2. 再检查相关契约、目录或产物是否变化",
            "3. 最后补一轮下游消费者或外部联调验证",
            "",
            "#### 代码证据",
            "",
            f"- {code_refs}",
            "",
            "#### 主要证据",
            "",
            f"- {evidence_refs}",
        ]
    )


def infer_change_surface_category(code_item: dict[str, object]) -> str:
    path = str(code_item.get("path", "")).lower()
    if "/editor/windows/" in path or "/editor/homemap/windows/" in path:
        return "entrypoint"
    if (
        "/runtime/" in path
        or "homemapruntimepackagedata" in path
        or "unifiednavexportdescriptordata" in path
        or "sceneblueprintruntimedata" in path
    ):
        return "runtime"
    if (
        "/editor/session/" in path
        or "/editor/scenemodel/" in path
        or "/editor/sceneprofiles/" in path
    ):
        return "session"
    if "/editor/homemap/" in path or "/runtime/homemap/" in path:
        return "home_map"
    if (
        "/editor/export/" in path
        or "runtimepackageexportservice" in path
        or "exportsceneblueprintruntimedatastep" in path
        or "exportunifiednavdescriptorstep" in path
    ):
        return "export"
    if "/editor/sync/" in path:
        return "sync"
    if "bridge" in path or "integration" in path or code_item.get("scope") == "outside_target_consumer":
        return "bridge"
    return "config"


def compute_code_candidate_score(evidence_item: EvidenceItem) -> int:
    path = evidence_item.path.lower()
    high_signal_names = {
        "scenesessionrequest",
        "sceneprofile",
        "entrywindow",
        "editorsession",
        "sceneoperationrouter",
        "sceneassetpathrouter",
        "sceneartifactsemanticnaming",
        "scenemoduleset",
        "scenemodulesetregistry",
        "homemapsceneprofile",
        "homemapmodulesetregistration",
        "homemapmodule",
        "homemapruntimepackageexportservice",
        "homemapruntimepackagedata",
        "exportcontext",
        "synccontext",
        "exportpipeline",
        "exportstepregistry",
        "exportsceneblueprintruntimedatastep",
        "exportunifiednavdescriptorstep",
        "syncpipeline",
        "stagedesignersettings",
        "unifiednavexportdescriptordata",
        "sceneblueprintruntimedata",
        "runtimepackageexportservice",
        "framesync",
        "bootstrap",
        "bridge",
    }
    medium_signal_names = {
        "profile",
        "session",
        "router",
        "pipeline",
        "context",
        "module",
        "entry",
    }
    low_signal_names = {
        "datamanager",
        "debugwindow",
        "gui",
        "events",
        "actions",
    }

    score = 100
    for name in high_signal_names:
        if name in path:
            score -= 50
    for name in medium_signal_names:
        if name in path:
            score -= 20
    for name in low_signal_names:
        if name in path:
            score += 20
    if evidence_item.type == "related_reference":
        score += 10
    return score


def extract_priority_code_paths(code_registry: dict[str, object], limit: int = 3) -> list[str]:
    if not isinstance(code_registry, dict):
        return []
    items = code_registry.get("items", [])
    if not isinstance(items, list):
        return []
    ordered_items = sorted(
        items,
        key=lambda item: (
            min(
                (index for index, pattern in enumerate(PRIMARY_CODE_PATH_PATTERNS) if pattern in str(item.get("path", "")).lower()),
                default=len(PRIMARY_CODE_PATH_PATTERNS),
            ),
            str(item.get("path", "")).lower(),
            int(item.get("startLine", 1)),
        ),
    )
    paths = [str(item.get("path", "")) for item in ordered_items if item.get("path")]
    return dedupe_keep_order(paths)[:limit]


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def read_text_content(file_path: Path) -> str:
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                return file_path.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                return ""


def is_high_signal_symbol(symbol_name: str, relative_path: str, evidence_type: str) -> bool:
    if evidence_type == "entrypoint":
        return True
    high_signal_prefixes = (
        "Start",
        "Build",
        "Create",
        "Execute",
        "Load",
        "Save",
        "Open",
        "Register",
        "Validate",
        "TryGet",
        "Get",
        "Handle",
        "Stop",
        "Restore",
        "Mark",
    )
    high_signal_keywords = (
        "Session",
        "Request",
        "Identity",
        "Profile",
        "Module",
        "Registry",
        "Pipeline",
        "Context",
        "Router",
        "Window",
        "Bridge",
        "Service",
        "Package",
        "Descriptor",
        "Bootstrap",
    )
    return symbol_name.startswith(high_signal_prefixes) or any(keyword in symbol_name for keyword in high_signal_keywords) or any(
        keyword.lower() in relative_path.lower()
        for keyword in ("session", "request", "pipeline", "router", "profile", "module", "bridge", "runtime", "descriptor")
    )


def categorize_code_path(relative_path: str, evidence_type: str) -> str:
    lowered = relative_path.lower()
    if evidence_type == "entrypoint" or "/editor/windows/" in lowered:
        return "entrypoint"
    if (
        "/runtime/" in lowered
        or "stagedesignersettings.asset" in lowered
        or "homemapruntimepackagedata" in lowered
        or "unifiednavexportdescriptordata" in lowered
        or "sceneblueprintruntimedata" in lowered
    ):
        return "runtime"
    if (
        "/editor/session/" in lowered
        or "/editor/mode/" in lowered
        or "/editor/scenemodel/" in lowered
        or "/editor/sceneprofiles/" in lowered
    ):
        return "session"
    if "/editor/homemap/" in lowered or "/runtime/homemap/" in lowered:
        return "home_map"
    if (
        "/editor/export/" in lowered
        or "/editor/pipeline/" in lowered
        or "runtimepackageexportservice" in lowered
        or "exportsceneblueprintruntimedatastep" in lowered
        or "exportunifiednavdescriptorstep" in lowered
    ):
        return "export"
    if "/editor/sync/" in lowered or "syncconfig" in lowered:
        return "sync"
    if "bridge" in lowered or "integration" in lowered or evidence_type == "related_reference":
        return "bridge"
    return "config"


def compute_line_number(text: str, position: int) -> int:
    return text.count("\n", 0, position) + 1


def first_non_empty_line_number(text: str) -> int:
    for index, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            return index
    return 1


def format_module_label(task: dict[str, object]) -> str:
    target_paths = task.get("targetPaths", [])
    if isinstance(target_paths, list) and target_paths:
        return ", ".join(str(path) for path in target_paths)
    return "unknown-module"


def sort_docs_for_handover(docs: list[EvidenceItem]) -> list[EvidenceItem]:
    def score(item: EvidenceItem) -> tuple[int, str]:
        path = item.path.lower()
        value = 100
        if "离职交接" in item.path or "handover" in path:
            value -= 70
        if "架构设计" in item.path or "architecture" in path:
            value -= 60
        if path.endswith("design.md"):
            value -= 40
        if "readme" in path:
            value -= 35
        if "/specs/" in path:
            value -= 25
        if "演进方案" in item.path or "proposal" in path or "requirements" in path:
            value -= 15
        if "问题记录" in item.path or "verification" in path or "tasks.md" in path:
            value += 10
        if "性能" in item.path:
            value += 20
        return (value, path)

    return sorted(docs, key=score)


def sort_configs_for_handover(configs: list[EvidenceItem], task: dict[str, object]) -> list[EvidenceItem]:
    target_paths = [str(path).lower() for path in task.get("targetPaths", []) if isinstance(path, str)]

    def score(item: EvidenceItem) -> tuple[int, str]:
        path = item.path.lower()
        value = 100
        if any(path.startswith(prefix.lower()) for prefix in target_paths):
            value -= 50
        if path.endswith(".asmdef"):
            value -= 35
        if "stagedesigner" in path:
            value -= 25
        if path.endswith(".sln"):
            value += 20
        if path.endswith(".csproj"):
            value += 30
        return (value, path)

    return sorted(configs, key=score)


def get_code_items(code_registry: dict[str, object]) -> list[dict[str, object]]:
    if not isinstance(code_registry, dict):
        return []
    items = code_registry.get("items", [])
    return items if isinstance(items, list) else []


def get_code_items_by_category(code_registry: dict[str, object], category: str, limit: int = 4) -> list[dict[str, object]]:
    matched = [item for item in get_code_items(code_registry) if infer_change_surface_category(item) == category]
    return matched[:limit]


def find_code_items_by_path_patterns(
    code_registry: dict[str, object],
    patterns: list[str],
    limit: int = 3,
) -> list[dict[str, object]]:
    matched: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, int]] = set()
    lowered_patterns = [pattern.lower() for pattern in patterns]

    for item in get_code_items(code_registry):
        path = str(item.get("path", "")).lower()
        if not any(pattern in path for pattern in lowered_patterns):
            continue
        key = (str(item.get("path", "")), str(item.get("symbol", "")), int(item.get("startLine", 1)))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        matched.append(item)
        if len(matched) >= limit:
            break

    return matched


def render_code_anchor_refs(items: list[dict[str, object]], limit: int = 3) -> str:
    refs = []
    for item in items[:limit]:
        refs.append(f"`{item.get('id', 'code-unknown')}` `{item.get('path', 'unknown')}:{item.get('startLine', 1)}`")
    return " / ".join(refs)


def render_responsibility_section(docs: list[EvidenceItem], directory_trees: list[EvidenceItem]) -> str:
    lines = [
        "当前版本优先依据已有文档与目录结构推断模块职责，建议后续由离职人补充业务目标、上下游关系和非代码约束。",
    ]
    if docs:
        lines.append("优先参考以下文档证据：")
        lines.extend(f"- `{item.path}`" for item in docs[:5])
    else:
        lines.append("当前范围内未发现足够的模块文档，职责描述需要更多人工补充。")

    if directory_trees:
        lines.append("")
        lines.append("目录结构概览：")
        for item in directory_trees:
            lines.append("```text")
            lines.append(item.snippet)
            lines.append("```")
    return "\n".join(lines)


def render_scan_overview_section(task: dict[str, object], evidence: list[EvidenceItem]) -> str:
    type_counter = Counter(item.type for item in evidence)
    target_paths = task.get("targetPaths", [])
    lines = [
        f"本次整合文档面向 `{', '.join(str(path) for path in target_paths)}`，以下是内部扫描层提取到的核心事实概况。",
        f"- 目录结构证据：{type_counter.get('directory_tree', 0)} 条",
        f"- 文档证据：{type_counter.get('doc', 0)} 条",
        f"- 配置证据：{type_counter.get('config', 0)} 条",
        f"- 入口证据：{type_counter.get('entrypoint', 0)} 条",
        f"- 外部引用证据：{type_counter.get('related_reference', 0)} 条",
        f"- 源码样本：{type_counter.get('source', 0)} 条",
    ]
    if type_counter.get("related_reference", 0):
        lines.append("- 扫描结果表明该模块与仓库其他模块存在真实耦合，交接时不能只看目标目录内部代码。")
    if not type_counter.get("doc", 0):
        lines.append("- 当前范围几乎没有现成文档，自动 handover 的可信度会显著依赖代码锚点质量。")
    return "\n".join(lines)


def render_document_index_section(
    docs: list[EvidenceItem],
    code_registry: dict[str, object],
    change_rows: list[dict[str, object]],
) -> str:
    if not docs and not get_code_items(code_registry):
        return "当前未形成稳定的文档主索引，建议先人工补齐高层设计文档与关键入口文件。"

    lines = [
        "这部分用于把“应该先看什么”整合成一条连续阅读主线，避免接手人来回切十几份文档。",
    ]
    if docs:
        lines.append("建议优先阅读以下已有文档：")
        for item in docs[:5]:
            lines.append(f"- `{item.path}`")
    priority_code_paths = extract_priority_code_paths(code_registry, limit=18)
    if priority_code_paths:
        lines.append("")
        lines.append("需要与文档并行联读的核心代码路径：")
        for path in priority_code_paths:
            lines.append(f"- `{path}`")
    if change_rows:
        lines.append("")
        lines.append("阅读时优先关注以下高风险改动面：")
        for row in sorted(change_rows, key=lambda item: 0 if item.get("risk") == "high" else 1)[:3]:
            lines.append(f"- {row.get('change_surface', '未知变更面')}")
    return "\n".join(lines)


def render_evidence_bullets(items: list[EvidenceItem], empty_message: str) -> str:
    if not items:
        return empty_message
    lines = []
    for item in items:
        lines.append(f"- `{item.path}`")
        lines.append(f"  摘要：{item.summary}")
    return "\n".join(lines)


def render_architecture_section(code_registry: dict[str, object], docs: list[EvidenceItem]) -> str:
    definitions = [
        (
            "入口受理层",
            "入口窗口负责收集用户输入，并把 level / hub / homeMap 等启动意图统一包装成会话请求。",
            ["homemapentrywindow", "/editor/windows/entrywindow.cs"],
            3,
        ),
        (
            "统一请求建模层",
            "SceneIdentity / SceneProfile / SceneSessionRequest 共同定义场景身份、能力和启动语义，是整个主链的上游数据模型。",
            ["scenesessionrequest", "/editor/sceneprofiles/sceneprofile.cs", "homemapsceneprofile"],
            4,
        ),
        (
            "会话宿主与能力路由层",
            "EditorSession 持有真实生命周期，SceneOperationRouter 负责 capability、稳定场景标识与展示名判断。",
            ["editorsession", "sceneoperationrouter"],
            4,
        ),
        (
            "模块装配层",
            "SceneModuleSetRegistry / SceneModuleSet 根据 SceneProfile 装配模块集合，决定会话启动后真正会初始化哪些能力。",
            ["scenemodulesetregistry", "scenemoduleset"],
            4,
        ),
        (
            "导出执行层",
            "ExportContext / ExportPipeline / ExportStepRegistry 负责把当前会话转换成导出计划，再驱动具体步骤产生产物。",
            ["exportcontext", "exportpipeline", "exportstepregistry", "exportsceneblueprintruntimedatastep", "exportunifiednavdescriptorstep"],
            5,
        ),
        (
            "运行时契约层",
            "导出结果最终收敛为运行时资产或 JSON 契约，例如 SceneBlueprintRuntimeData、HomeMapRuntimePackageData、UnifiedNavExportDescriptorData。",
            ["sceneblueprintruntimedata", "homemapruntimepackagedata", "unifiednavexportdescriptordata", "runtimepackageexportservice"],
            5,
        ),
        (
            "外部桥接层",
            "Integration / Bridge 目录中的桥接代码负责把统一会话语义扩散到其他模块或外部编辑工作流。",
            ["stagedesignerintegration", "mapeditorbridge"],
            3,
        ),
    ]

    lines = [
        "这一节优先依据 `understanding/14-code-evidence-registry.json` 中的代码锚点归纳框架，而不是平铺目录清单。",
    ]
    if docs:
        lines.append(f"建议先用 `{docs[0].path}` 建立概念，再对照下面的代码层次落到实现。")
        lines.append("")

    written = 0
    for label, summary, patterns, limit in definitions:
        items = find_code_items_by_path_patterns(code_registry, patterns, limit=limit)
        if not items:
            continue
        written += 1
        lines.append(f"- {label}")
        lines.append(f"  职责：{summary}")
        lines.append(f"  锚点：{render_code_anchor_refs(items, limit=limit)}")

    if not written:
        return "当前还没有足够稳定的代码锚点来归纳框架层次，建议先人工确认入口、会话、导出和运行时这四层。"
    return "\n".join(lines)


def render_dependency_section(
    configs: list[EvidenceItem],
    related_refs: list[EvidenceItem],
    code_registry: dict[str, object],
) -> str:
    bridge_items = get_code_items_by_category(code_registry, "bridge", limit=3)
    runtime_items = get_code_items_by_category(code_registry, "runtime", limit=2)

    if not configs and not related_refs and not bridge_items and not runtime_items:
        return "当前未发现典型配置文件或外部引用线索，建议确认依赖声明、环境配置和跨模块关系是否散落在其他目录或平台配置中。"

    lines = [
        "这一节只保留对接手最重要的依赖与边界：程序集/配置声明、运行时消费边界，以及仓库内的外部耦合点。",
    ]
    for item in configs[:6]:
        dependency_hint = extract_dependency_hint(item)
        lines.append(f"- `{item.path}`")
        lines.append(f"  线索：{dependency_hint}")
    if runtime_items:
        lines.append("")
        lines.append("运行时或对外消费边界：")
        for item in runtime_items:
            lines.append(f"- `{item.get('path', 'unknown')}`")
            lines.append(f"  代码锚点：`{item.get('id', 'code-unknown')}` `{item.get('symbol', 'unknown')}`")
    if related_refs or bridge_items:
        lines.append("")
        lines.append("仓库内明确可见的外部耦合点：")
        for item in bridge_items:
            lines.append(f"- `{item.get('path', 'unknown')}`")
            lines.append(f"  代码锚点：`{item.get('id', 'code-unknown')}` `{item.get('symbol', 'unknown')}`")
        for item in related_refs[:5]:
            lines.append(f"- `{item.path}`")
            lines.append(f"  线索：{item.summary}")
    return "\n".join(lines)


def render_flow_section(
    entrypoints: list[EvidenceItem],
    scripts: list[EvidenceItem],
    docs: list[EvidenceItem],
    related_refs: list[EvidenceItem],
    code_registry: dict[str, object],
    change_rows: list[dict[str, object]],
) -> str:
    lines = [
        "这一节优先根据 `14` 的代码锚点和 `15` 的变更面，归纳接手人最该先理解的主干链路。",
        "建议先把主链记成：`EntryWindow / HomeMapEntryWindow -> SceneSessionRequest + SceneProfile -> EditorSession.StartSession(...) -> SceneOperationRouter -> SceneModuleSetRegistry / SceneModuleSet -> ExportContext / ExportPipeline / ExportStepRegistry -> ExportStep / HomeMapRuntimePackageExportService -> SceneBlueprintRuntimeData / HomeMapRuntimePackageData / UnifiedNavExportDescriptorData`。",
    ]

    unified_chain = [
        (
            "请求建模",
            "用统一的场景请求对象表达启动意图，而不是散落的 level/homeMapId 参数。",
            find_code_items_by_path_patterns(code_registry, ["scenesessionrequest", "/editor/sceneprofiles/sceneprofile.cs", "homemapsceneprofile"], limit=4),
        ),
        (
            "会话宿主",
            "由 EditorSession 承接 Start/Stop 生命周期、Redux 初始化、事件注册和模块编排。",
            find_code_items_by_path_patterns(code_registry, ["editorsession"], limit=3),
        ),
        (
            "能力路由",
            "通过 SceneOperationRouter 根据 SceneProfile / Capability / Identity 决定可用能力与显示名、稳定场景标识。",
            find_code_items_by_path_patterns(code_registry, ["sceneoperationrouter"], limit=3),
        ),
        (
            "模块装配",
            "通过 SceneModuleSet / SceneModuleSetRegistry 把 SceneProfile 映射成模块集合，决定会话启动时实际装配哪些能力。",
            find_code_items_by_path_patterns(code_registry, ["scenemodulesetregistry", "scenemoduleset"], limit=3),
        ),
        (
            "导出主链",
            "导出上下文与步骤注册器把当前场景转成可执行的导出计划，并开始向运行时产物收敛。",
            find_code_items_by_path_patterns(
                code_registry,
                ["exportcontext", "exportpipeline", "exportstepregistry", "exportsceneblueprintruntimedata", "exportunifiednavdescriptor", "runtimepackageexportservice"],
                limit=6,
            ),
        ),
        (
            "运行时落地",
            "最终产物会沉淀为运行时可消费的数据资产或 JSON 契约，例如 SceneBlueprintRuntimeData / RuntimePackage / UnifiedNavDescriptor。",
            find_code_items_by_path_patterns(
                code_registry,
                ["sceneblueprintruntimedata", "homemapruntimepackagedata", "unifiednavexportdescriptordata"],
                limit=5,
            ),
        ),
    ]

    rendered_chain = 0
    lines.append("当前自动归纳出的统一主链如下：")
    for title, summary, matched in unified_chain:
        if not matched:
            continue
        rendered_chain += 1
        lines.append(f"- {title}")
        lines.append(f"  含义：{summary}")
        lines.append(f"  关键锚点：{render_code_anchor_refs(matched, limit=4)}")
    if rendered_chain:
        lines.append("")

    flow_definitions = [
        ("入口受理", ["entrypoint"], "窗口或入口命令接收用户动作，并决定进入哪条编辑工作流。"),
        ("会话与能力装配", ["session", "home_map"], "会话、路由和模块装配决定当前场景能做什么、加载哪些专属能力。"),
        ("导出与同步执行", ["export", "sync"], "编辑态数据会进入导出/同步上下文，生成步骤计划、目录和产物。"),
        ("运行时消费与外部联动", ["runtime", "bridge"], "运行时数据、桥接接口和外部模块消费这些结果并形成联调边界。"),
    ]

    written = 0
    for title, categories, summary in flow_definitions:
        matched: list[dict[str, object]] = []
        for category in categories:
            matched.extend(get_code_items_by_category(code_registry, category, limit=2))
        matched = matched[:3]
        if not matched:
            continue
        written += 1
        lines.append(f"- {title}")
        lines.append(f"  流程含义：{summary}")
        lines.append(f"  关键锚点：{render_code_anchor_refs(matched, limit=3)}")

    if change_rows:
        lines.append("")
        lines.append("当前自动识别到的高风险流转节点：")
        for row in sorted(change_rows, key=lambda item: 0 if item.get("risk") == "high" else 1)[:3]:
            locations = " / ".join(f"`{path}`" for path in row.get("locations", [])[:2])
            lines.append(f"- {row.get('change_surface', '未知变更面')}")
            lines.append(f"  影响：{row.get('downstream', '需人工补充')}")
            if locations:
                lines.append(f"  位置：{locations}")

    if scripts:
        lines.append("")
        lines.append("补充线索：发现可能参与运维或批处理的脚本/流程文件。")
        lines.extend(f"- `{item.path}`" for item in scripts[:3])
    if not written and not change_rows:
        lines.append("尚未形成稳定的数据流主线，建议先从入口窗口、会话宿主、导出上下文这三类文件反向梳理。")
    return "\n".join(lines)


def render_risk_section(task: dict[str, object], evidence: list[EvidenceItem]) -> str:
    risks: list[str] = []
    docs = filter_evidence(evidence, "doc")
    configs = filter_evidence(evidence, "config")
    related_refs = filter_evidence(evidence, "related_reference")
    scripts = filter_evidence(evidence, "script")
    entrypoints = filter_evidence(evidence, "entrypoint")
    source_files = filter_evidence(evidence, "source")

    if not docs:
        risks.append("缺少现成文档，接手人需要更多时间通过代码建立模块心智模型。")
    if not entrypoints:
        risks.append("未识别出明显入口文件，启动链路和核心流程可能需要额外人工梳理。")
    if configs and not docs:
        risks.append("存在配置线索但缺少对应说明文档，环境差异与默认值可能成为隐性风险。")
    if related_refs:
        risks.append("目标模块与仓库其他模块存在显式引用，交接时需要补清楚调用方向、责任边界和联调方式。")
    if scripts:
        risks.append("发现脚本型资产，需确认其中是否包含手工运维、修复或发布动作。")
    if source_files and not any("test" in item.path.lower() for item in source_files):
        risks.append("当前扫描结果中未明显看到测试文件，回归与接手验证成本可能偏高。")

    if not risks:
        risks.append("当前证据未暴露明显结构性风险，但仍建议作者补充生产约束、监控和常见故障处理方式。")

    return "\n".join(f"- {risk}" for risk in risks)


def render_open_questions_section(task: dict[str, object], evidence: list[EvidenceItem]) -> str:
    questions = build_open_questions(task, evidence)
    if not questions:
        return "当前没有自动归纳出待确认问题，建议离职人手工补充业务背景、环境差异和常见故障处理方式。"

    lines = [
        "下面这些问题建议在正式交接前由模块作者补齐。单文档 handover 会直接展示它们，不再要求接手人单独打开另一份问题列表。",
    ]
    for question in questions[:8]:
        lines.append(f"- {question}")
    return "\n".join(lines)


def render_handover_advice(
    entrypoints: list[EvidenceItem],
    configs: list[EvidenceItem],
    docs: list[EvidenceItem],
    scripts: list[EvidenceItem],
    related_refs: list[EvidenceItem],
    code_registry: dict[str, object],
    change_rows: list[dict[str, object]],
) -> str:
    reading_order = []
    reading_order.extend(item.path for item in docs[:2])
    reading_order.extend(extract_priority_code_paths(code_registry, limit=18))
    for row in change_rows[:3]:
        reading_order.extend(row.get("locations", [])[:2])
    reading_order.extend(item.path for item in entrypoints[:2])
    reading_order.extend(item.path for item in configs[:2])
    reading_order.extend(item.path for item in scripts[:2])
    reading_order.extend(item.path for item in related_refs[:2])
    reading_order = dedupe_keep_order(reading_order)

    if not reading_order:
        return "建议先确认模块负责人和运行环境，再人工补齐最小阅读路径。"

    lines = [
        "建议接手人按以下顺序建立认知：",
    ]
    for index, path in enumerate(reading_order, start=1):
        lines.append(f"{index}. `{path}`")
    lines.append("")
    if change_rows:
        lines.append("优先把上面路径和 `understanding/15-change-impact-matrix.md` 的前三个高风险改动面一起联读。")
        lines.append("")
    lines.append("完成首轮阅读后，优先回到本交接文档中的“待确认问题”一节逐项补全人工信息。")
    return "\n".join(lines)


def render_code_evidence_section(code_registry: dict[str, object]) -> str:
    items = get_code_items(code_registry)
    if not items:
        return "当前未生成稳定的代码级 evidence，建议优先人工补齐统一入口、会话宿主和导出/同步上下文这三类关键锚点。"

    lines = [
        "这一节直接摘取 `understanding/14-code-evidence-registry.json` 里的高价值锚点，方便把框架和风险落到具体代码。",
    ]
    definitions = [
        ("entrypoint", "入口锚点"),
        ("session", "会话锚点"),
        ("home_map", "HomeMap 锚点"),
        ("export", "导出锚点"),
        ("runtime", "运行时锚点"),
        ("bridge", "桥接锚点"),
    ]
    written = 0
    for category, label in definitions:
        matched = get_code_items_by_category(code_registry, category, limit=2)
        if not matched:
            continue
        written += 1
        lines.append(f"- {label}")
        for item in matched:
            path = item.get("path", "unknown")
            symbol = item.get("symbol", "unknown")
            line_no = item.get("startLine", 1)
            role = item.get("role", "关键代码锚点")
            code_id = item.get("id", "code-unknown")
            impacts = ", ".join(item.get("impacts", [])[:3]) if isinstance(item.get("impacts"), list) else ""
            lines.append(f"  `{code_id}` `{path}:{line_no}` `{symbol}`")
            lines.append(f"  作用：{role}")
            if impacts:
                lines.append(f"  影响面：{impacts}")
    if not written:
        return "当前未生成稳定的关键代码锚点，建议优先人工确认入口窗口、会话宿主和导出上下文。"
    lines.append("")
    lines.append("完整代码锚点列表见 `understanding/14-code-evidence-registry.json`。")
    return "\n".join(lines)


def render_change_impact_section(change_rows: list[dict[str, object]]) -> str:
    if not change_rows:
        return "当前未自动识别出稳定的变更影响矩阵，建议至少先人工补齐“会话模型、路径命名、导出/同步上下文”三类高风险改动面。"

    lines = [
        "这一节直接从 `understanding/15-change-impact-matrix.md` 摘取最值得接手人先记住的改动风险。",
    ]
    for row in sorted(change_rows, key=lambda item: 0 if item.get("risk") == "high" else 1)[:4]:
        locations = " / ".join(f"`{path}`" for path in row.get("locations", [])[:2])
        code_refs = " ".join(f"`{code_id}`" for code_id in row.get("code_ids", [])[:4])
        lines.append(f"- {row['change_surface']}")
        lines.append(f"  典型位置：{locations}")
        lines.append(f"  影响：{row['downstream']}")
        lines.append(f"  必查：{row['contracts']}")
        lines.append(f"  回归：{row['regressions']}")
        lines.append(f"  风险等级：{row['risk']}")
        lines.append(f"  代码锚点：{code_refs}")
    lines.append("")
    lines.append("完整变更矩阵见 `understanding/15-change-impact-matrix.md`。")
    return "\n".join(lines)


def render_reference_section(
    evidence: list[EvidenceItem],
    code_registry: dict[str, object],
    change_rows: list[dict[str, object]],
) -> str:
    evidence_map = {item.id: item for item in evidence}
    priority_ids: list[str] = []
    for item in get_code_items(code_registry):
        priority_ids.extend(str(linked) for linked in item.get("linkedEvidence", []))
    for row in change_rows:
        priority_ids.extend(str(ev_id) for ev_id in row.get("evidence_ids", []))

    prioritized: list[EvidenceItem] = []
    seen_ids: set[str] = set()
    for evidence_id in priority_ids:
        item = evidence_map.get(evidence_id)
        if item and item.id not in seen_ids:
            prioritized.append(item)
            seen_ids.add(item.id)
    for item in sort_docs_for_handover(filter_evidence(evidence, "doc"))[:4]:
        if item.id not in seen_ids:
            prioritized.append(item)
            seen_ids.add(item.id)
    for item in evidence:
        if len(prioritized) >= 16:
            break
        if item.id in seen_ids:
            continue
        prioritized.append(item)
        seen_ids.add(item.id)

    lines = []
    for item in prioritized[:16]:
        lines.append(f"- `{item.id}` `{item.path}`: {item.summary}")
    return "\n".join(lines)


def render_internal_layer_section(understanding_dir: Path) -> str:
    descriptions = {
        "14-code-evidence-registry.json": "关键代码锚点注册表，给 Agent 和生成链定位“哪些代码不能跳过”。",
        "15-change-impact-matrix.md": "改动面 -> 影响 -> 回归矩阵，给生成链提炼高风险改动建议。",
        "scan-summary.md": "内部扫描摘要，记录本次证据采样覆盖情况。",
        "open-questions.md": "内部待确认问题列表，供 handover 汇总和离职人补充。",
    }

    files = sorted(
        [path for path in understanding_dir.iterdir() if path.is_file()],
        key=lambda path: path.name.lower(),
    )
    if not files:
        return "当前没有内部生成层文件。"

    lines = [
        "`understanding/` 已明确定位为内部生成层，不是交接人默认阅读入口。本 handover 已经把其中适合人读的内容整合进正文。",
        "当前内部层文件包括：",
    ]
    for path in files:
        description = descriptions.get(path.name, "内部生成材料，可用于追溯 handover 中的结构化结论。")
        lines.append(f"- `understanding/{path.name}`")
        lines.append(f"  作用：{description}")
    return "\n".join(lines)


def build_open_questions(task: dict[str, object], evidence: list[EvidenceItem]) -> list[str]:
    docs = filter_evidence(evidence, "doc")
    configs = filter_evidence(evidence, "config")
    related_refs = filter_evidence(evidence, "related_reference")
    scripts = filter_evidence(evidence, "script")
    entrypoints = filter_evidence(evidence, "entrypoint")

    questions = [
        f"当前范围 `{', '.join(task['targetPaths'])}` 的业务目标、核心指标和上下游依赖分别是什么？",
        "生产环境与测试环境有哪些必须手工维护的差异配置？这些差异目前记录在哪里？",
        "接手人第一次值班或发版前，必须掌握的排障动作和常见故障模式有哪些？",
    ]

    if not docs:
        questions.append("为什么该模块缺少直接可用的说明文档？是否存在未纳入仓库的 Wiki、页面或口头约定？")
    if configs:
        questions.append(f"以下配置文件 `{configs[0].path}` 中哪些配置项最敏感，变更前需要找谁确认？")
    if scripts:
        questions.append(f"脚本 `{scripts[0].path}` 是否参与部署、数据修复或手工运维？执行前置条件是什么？")
    if entrypoints:
        questions.append(f"入口文件 `{entrypoints[0].path}` 对应的主流程中，最容易踩坑的分支或隐性约束是什么？")
    if related_refs:
        questions.append(f"外部引用 `{related_refs[0].path}` 与当前模块之间谁是主导方？改动时联调和回归边界怎么划分？")

    questions.append("如果离职后无人可问，接手人应该优先联系哪些角色或外部系统负责人？")
    return questions[:8]


def filter_evidence(evidence: list[EvidenceItem], evidence_type: str) -> list[EvidenceItem]:
    return [item for item in evidence if item.type == evidence_type]


def extract_dependency_hint(item: EvidenceItem) -> str:
    path = item.path.lower()
    snippet = item.snippet

    if path.endswith("package.json"):
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError:
            return "检测到 package.json，但片段不足以稳定解析依赖，建议打开文件查看。"
        dependencies = sorted((data.get("dependencies") or {}).keys())
        dev_dependencies = sorted((data.get("devDependencies") or {}).keys())
        merged = dependencies[:5] + dev_dependencies[:3]
        if merged:
            return f"前几个依赖: {', '.join(merged)}"
        return "未在片段中识别到依赖字段，建议查看完整文件。"

    if path.endswith(".csproj"):
        try:
            root = ElementTree.fromstring(snippet)
        except ElementTree.ParseError:
            return "检测到 .csproj，但片段不足以稳定解析包引用，建议打开文件查看。"
        package_refs = []
        for element in root.iter():
            if element.tag.endswith("PackageReference") and "Include" in element.attrib:
                package_refs.append(element.attrib["Include"])
        if package_refs:
            return f"前几个包引用: {', '.join(package_refs[:5])}"
        return "未在片段中看到 PackageReference，可能依赖定义在其他片段。"

    if path.endswith("pom.xml"):
        package_names = re.findall(r"<artifactId>([^<]+)</artifactId>", snippet)
        if package_names:
            return f"前几个 Maven 依赖线索: {', '.join(package_names[:5])}"
        return "检测到 pom.xml，但片段中未提取到明显 artifactId。"

    if path.endswith("dockerfile"):
        first_line = snippet.splitlines()[0] if snippet.splitlines() else "无内容"
        return f"Docker 构建线索: {first_line}"

    if path.endswith(".asmdef"):
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError:
            return "检测到 .asmdef，但片段不足以稳定解析程序集依赖，建议打开文件查看。"
        references = data.get("references") or []
        if references:
            return f"程序集依赖: {', '.join(references[:6])}"
        return "未在片段中识别到 references，可能是独立程序集或片段不足。"

    if path.endswith(".sln"):
        projects = re.findall(r'Project\(.*?\) = "([^"]+)"', snippet)
        if projects:
            return f"解决方案中的项目线索: {', '.join(projects[:6])}"
        return "检测到解决方案文件，但片段中未提取到项目名。"

    return "建议结合文件片段确认依赖、环境变量与启动参数。"


def find_related_reference_files(repo_path: Path, target_paths: list[Path]) -> Iterable[Path]:
    keywords = derive_related_keywords(target_paths)
    if not keywords:
        return []

    matched: list[Path] = []
    for search_root_name in ("Assets", "Packages"):
        search_root = repo_path / search_root_name
        if not search_root.exists():
            continue
        for file_path in walk_files(search_root):
            if len(matched) >= MAX_RELATED_REFERENCE_FILES:
                return matched
            if any(file_path.is_relative_to(target) for target in target_paths):
                continue
            if not should_scan_for_related_reference(file_path):
                continue
            if file_contains_keywords(file_path, keywords):
                matched.append(file_path)
    return matched


def derive_related_keywords(target_paths: list[Path]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()

    for target_path in target_paths:
        base_name = target_path.name.strip()
        if base_name and base_name not in seen:
            keywords.append(base_name)
            seen.add(base_name)

        for asmdef_file in target_path.rglob("*.asmdef"):
            if asmdef_file.is_dir():
                continue
            keyword = asmdef_file.stem.strip()
            if keyword and keyword not in seen:
                keywords.append(keyword)
                seen.add(keyword)
            try:
                data = json.loads(asmdef_file.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            root_namespace = str(data.get("rootNamespace") or "").strip()
            if root_namespace and root_namespace not in seen:
                keywords.append(root_namespace)
                seen.add(root_namespace)

    return keywords[:10]


def should_scan_for_related_reference(file_path: Path) -> bool:
    suffix = file_path.suffix.lower()
    if suffix not in TEXT_EXTENSIONS:
        return False
    if suffix == ".meta":
        return False
    if file_path.stat().st_size > MAX_TEXT_FILE_SIZE:
        return False
    return True


def file_contains_keywords(file_path: Path, keywords: list[str]) -> bool:
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = file_path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                content = file_path.read_text(encoding="gb18030")
            except UnicodeDecodeError:
                return False

    lowered = content.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def is_related_reference_file(repo_path: Path, target_paths: list[Path], file_path: Path) -> bool:
    return (
        not any(file_path.is_relative_to(target) for target in target_paths)
        and not any(part in IGNORED_DIR_NAMES for part in file_path.relative_to(repo_path).parts[:-1])
        and should_scan_for_related_reference(file_path)
    )


def load_template(template_name: str) -> Template:
    template_path = TEMPLATES_DIR / template_name
    content = template_path.read_text(encoding="utf-8")
    return Template(content)


def write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def to_posix(path: Path) -> str:
    return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())

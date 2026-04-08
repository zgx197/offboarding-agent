#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
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
MAX_CODE_EVIDENCE_ITEMS = 18
MAX_CODE_EVIDENCE_PER_FILE = 2


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
    run_parser.add_argument("--task-id", help="Optional custom task id.")
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
    task_id = args.task_id or build_task_id(target_paths)
    run_dir = RUNS_DIR / task_id
    run_dir.mkdir(parents=True, exist_ok=False)

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

    handover_markdown = render_handover(task, evidence, code_registry)
    (run_dir / "handover.md").write_text(handover_markdown, encoding="utf-8")

    questions_markdown = render_open_questions(task, evidence)
    (run_dir / "open-questions.md").write_text(questions_markdown, encoding="utf-8")

    scan_summary_markdown = render_scan_summary(task, evidence)
    (run_dir / "scan-summary.md").write_text(scan_summary_markdown, encoding="utf-8")

    task["status"] = "completed"
    task["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_json(run_dir / "task.json", task)

    print(f"Run completed: {run_dir}")
    print("Generated files:")
    print(f"  - {run_dir / 'task.json'}")
    print(f"  - {run_dir / 'evidence.json'}")
    print(f"  - {run_dir / 'scan-summary.md'}")
    print(f"  - {run_dir / 'handover.md'}")
    print(f"  - {run_dir / 'open-questions.md'}")
    print(f"  - {understanding_dir / '14-code-evidence-registry.json'}")
    print(f"  - {understanding_dir / '15-change-impact-matrix.md'}")
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
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base_name = slugify(target_paths[0].name) or "task"
    return f"{timestamp}-{base_name}"


def slugify(value: str) -> str:
    normalized = value.strip().lower().replace(" ", "-")
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

    candidate_files = list(scan_candidate_files(repo_path, target_paths))
    related_reference_files = list(find_related_reference_files(repo_path, target_paths))
    typed_counts: Counter[str] = Counter()

    for file_path in candidate_files + related_reference_files:
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

    if file_name in {"program.cs", "startup.cs"}:
        return True
    if suffix == ".unity" and stem in {"main", "entry"}:
        return True
    if suffix in {".cs", ".js", ".ts", ".tsx", ".jsx", ".py"} and stem in {"main", "index", "application", "entry", "bootstrap"}:
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
    code_registry: dict[str, object] | None = None,
) -> str:
    template = load_template("handover.md.tpl")
    target_paths = "\n".join(f"- `{path}`" for path in task["targetPaths"])
    scope_notes = [
        "当前版本只读扫描目标目录，不会在目标仓库内生成任何文件。",
        f"本次输出目录固定在工具仓库的 `runs/{task['taskId']}`。",
        "以下结论为基于现有代码与文档的初版整理，仍需结合作者经验补充。",
    ]

    docs = filter_evidence(evidence, "doc")
    entrypoints = filter_evidence(evidence, "entrypoint")
    configs = filter_evidence(evidence, "config")
    related_refs = filter_evidence(evidence, "related_reference")
    scripts = filter_evidence(evidence, "script")
    directory_trees = filter_evidence(evidence, "directory_tree")

    responsibility_section = render_responsibility_section(docs, directory_trees)
    entry_section = render_evidence_bullets(entrypoints, empty_message="未发现明显入口文件，建议优先从目录树与配置文件入手排查。")
    dependency_section = render_dependency_section(configs, related_refs)
    flow_section = render_flow_section(entrypoints, scripts, docs, related_refs)
    code_evidence_section = render_code_evidence_section(code_registry or {})
    change_impact_section = render_change_impact_section(code_registry or {})
    risk_section = render_risk_section(task, evidence)
    handover_section = render_handover_advice(entrypoints, configs, docs, scripts, related_refs, code_registry or {})
    evidence_section = render_reference_section(evidence)

    return template.substitute(
        task_id=task["taskId"],
        repo_path=task["repoPath"],
        owner=task["owner"],
        reviewer=task["reviewer"] or "未指定",
        audience=task["audience"],
        created_at=task["createdAt"],
        target_paths=target_paths,
        scope_notes="\n".join(f"- {note}" for note in scope_notes),
        responsibility_section=responsibility_section,
        entry_section=entry_section,
        dependency_section=dependency_section,
        flow_section=flow_section,
        code_evidence_section=code_evidence_section,
        change_impact_section=change_impact_section,
        risk_section=risk_section,
        handover_section=handover_section,
        evidence_section=evidence_section,
    )


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
        if file_path.exists() and file_path.is_file():
            candidates.append((priority[item.type], item, file_path))

    items: list[dict[str, object]] = []
    seen_keys: set[tuple[str, str, int]] = set()

    for _, evidence_item, file_path in sorted(
        candidates,
        key=lambda part: (
            compute_code_candidate_score(part[1]) + part[0] * 15,
            part[1].path.lower(),
        ),
    ):
        if len(items) >= MAX_CODE_EVIDENCE_ITEMS:
            break

        for anchor in extract_code_anchors(repo_path, target_paths, file_path, evidence_item):
            key = (str(anchor["path"]), str(anchor["symbol"]), int(anchor["startLine"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            anchor["id"] = f"code-{len(items) + 1:03d}"
            items.append(anchor)
            if len(items) >= MAX_CODE_EVIDENCE_ITEMS:
                break

    return {
        "meta": {
            "taskId": task["taskId"],
            "module": format_module_label(task),
            "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "confidence": "medium",
        },
        "items": items,
    }


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

    for position, kind, name in sorted(matches, key=lambda part: part[0])[:MAX_CODE_EVIDENCE_PER_FILE]:
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


def extract_config_anchors(
    relative_path: str,
    text: str,
    evidence_item: EvidenceItem,
    inside_target: bool,
    category: str,
) -> list[dict[str, object]]:
    patterns = [
        re.compile(r'"name"\s*:\s*"([^"]+)"'),
        re.compile(r'"noEngineReferences"\s*:\s*(true|false)', re.IGNORECASE),
        re.compile(r"Project\(.*?\)\s*=\s*\"([^\"]+)\""),
        re.compile(r"<PackageReference[^>]+Include=\"([^\"]+)\"", re.IGNORECASE),
    ]
    anchors: list[dict[str, object]] = []

    for pattern in patterns:
        for match in pattern.finditer(text):
            label = match.group(1)
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

        locations = sorted({str(item["path"]) for item in matched})[:3]
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
    if "/editor/session/" in path:
        return "session"
    if "/editor/homemap/" in path or "/runtime/homemap/" in path:
        return "home_map"
    if "/editor/export/" in path:
        return "export"
    if "/editor/sync/" in path:
        return "sync"
    if "/runtime/" in path:
        return "runtime"
    if "bridge" in path or "integration" in path or code_item.get("scope") == "outside_target_consumer":
        return "bridge"
    return "config"


def compute_code_candidate_score(evidence_item: EvidenceItem) -> int:
    path = evidence_item.path.lower()
    high_signal_names = {
        "entrywindow",
        "editorsession",
        "sceneoperationrouter",
        "homemapsceneprofile",
        "homemapmodulesetregistration",
        "homemapmodule",
        "exportcontext",
        "synccontext",
        "exportpipeline",
        "syncpipeline",
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
    paths = [str(item.get("path", "")) for item in items if item.get("path")]
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
        "Profile",
        "Module",
        "Pipeline",
        "Context",
        "Router",
        "Window",
        "Bridge",
        "Service",
        "Bootstrap",
    )
    return symbol_name.startswith(high_signal_prefixes) or any(keyword in symbol_name for keyword in high_signal_keywords) or any(
        keyword.lower() in relative_path.lower() for keyword in ("session", "pipeline", "router", "profile", "module", "bridge")
    )


def categorize_code_path(relative_path: str, evidence_type: str) -> str:
    lowered = relative_path.lower()
    if evidence_type == "entrypoint" or "/editor/windows/" in lowered:
        return "entrypoint"
    if "/editor/session/" in lowered:
        return "session"
    if "/editor/homemap/" in lowered or "/runtime/homemap/" in lowered:
        return "home_map"
    if "/editor/export/" in lowered:
        return "export"
    if "/editor/sync/" in lowered:
        return "sync"
    if "/runtime/" in lowered:
        return "runtime"
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


def render_evidence_bullets(items: list[EvidenceItem], empty_message: str) -> str:
    if not items:
        return empty_message
    lines = []
    for item in items:
        lines.append(f"- `{item.path}`")
        lines.append(f"  摘要：{item.summary}")
    return "\n".join(lines)


def render_dependency_section(configs: list[EvidenceItem], related_refs: list[EvidenceItem]) -> str:
    if not configs and not related_refs:
        return "当前未发现典型配置文件或外部引用线索，建议确认依赖声明、环境配置和跨模块关系是否散落在其他目录或平台配置中。"

    lines = []
    for item in configs[:10]:
        dependency_hint = extract_dependency_hint(item)
        lines.append(f"- `{item.path}`")
        lines.append(f"  线索：{dependency_hint}")
    if related_refs:
        if lines:
            lines.append("")
        lines.append("目标模块在仓库其他位置的引用：")
        for item in related_refs[:8]:
            lines.append(f"- `{item.path}`")
            lines.append(f"  线索：{item.summary}")
    return "\n".join(lines)


def render_flow_section(
    entrypoints: list[EvidenceItem],
    scripts: list[EvidenceItem],
    docs: list[EvidenceItem],
    related_refs: list[EvidenceItem],
) -> str:
    lines = [
        "第一版工作流不会自动还原完整调用图，下面整理的是适合人工继续追踪的阅读顺序。",
    ]
    if entrypoints:
        lines.append("优先从以下入口文件入手：")
        lines.extend(f"- `{item.path}`" for item in entrypoints[:5])
    if scripts:
        lines.append("与运行或运维相关的脚本：")
        lines.extend(f"- `{item.path}`" for item in scripts[:5])
    if docs:
        lines.append("可交叉验证的文档：")
        lines.extend(f"- `{item.path}`" for item in docs[:5])
    if related_refs:
        lines.append("可继续追踪的外部耦合点：")
        lines.extend(f"- `{item.path}`" for item in related_refs[:5])
    if len(lines) == 1:
        lines.append("尚未发现明显入口或说明文档，建议从目标目录中命名最明确的源码文件开始逆向整理。")
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
    if configs and not any(item.path.lower().startswith("docs/") for item in docs):
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


def render_handover_advice(
    entrypoints: list[EvidenceItem],
    configs: list[EvidenceItem],
    docs: list[EvidenceItem],
    scripts: list[EvidenceItem],
    related_refs: list[EvidenceItem],
    code_registry: dict[str, object],
) -> str:
    reading_order = []
    reading_order.extend(item.path for item in docs[:2])
    reading_order.extend(extract_priority_code_paths(code_registry, limit=3))
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
    lines.append("完成首轮阅读后，优先补齐 `open-questions.md` 中的人工信息。")
    return "\n".join(lines)


def render_code_evidence_section(code_registry: dict[str, object]) -> str:
    items = code_registry.get("items", []) if isinstance(code_registry, dict) else []
    if not items:
        return "当前未生成稳定的代码级 evidence，建议优先人工补齐统一入口、会话宿主和导出/同步上下文这三类关键锚点。"

    lines = [
        "这一节列出自动提取出的关键代码锚点，用来把高层结论落到具体文件和符号上。",
    ]
    for item in items[:8]:
        path = item.get("path", "unknown")
        symbol = item.get("symbol", "unknown")
        line_no = item.get("startLine", 1)
        role = item.get("role", "关键代码锚点")
        code_id = item.get("id", "code-unknown")
        impacts = ", ".join(item.get("impacts", [])[:3]) if isinstance(item.get("impacts"), list) else ""
        lines.append(f"- `{code_id}` `{path}:{line_no}` `{symbol}`")
        lines.append(f"  作用：{role}")
        if impacts:
            lines.append(f"  影响面：{impacts}")
    lines.append("")
    lines.append("完整代码锚点列表见 `understanding/14-code-evidence-registry.json`。")
    return "\n".join(lines)


def render_change_impact_section(code_registry: dict[str, object]) -> str:
    rows = build_change_impact_rows(code_registry.get("items", []) if isinstance(code_registry, dict) else [])
    if not rows:
        return "当前未自动识别出稳定的变更影响矩阵，建议至少先人工补齐“会话模型、路径命名、导出/同步上下文”三类高风险改动面。"

    lines = [
        "下面整理的是更贴近维护动作的“改动面 -> 影响 -> 回归”提示。",
    ]
    for row in rows[:4]:
        locations = " / ".join(f"`{path}`" for path in row["locations"][:2])
        code_refs = " ".join(f"`{code_id}`" for code_id in row["code_ids"][:4])
        lines.append(f"- {row['change_surface']}")
        lines.append(f"  典型位置：{locations}")
        lines.append(f"  影响：{row['downstream']}")
        lines.append(f"  必查：{row['contracts']}")
        lines.append(f"  回归：{row['regressions']}")
        lines.append(f"  代码锚点：{code_refs}")
    lines.append("")
    lines.append("完整变更矩阵见 `understanding/15-change-impact-matrix.md`。")
    return "\n".join(lines)


def render_reference_section(evidence: list[EvidenceItem]) -> str:
    lines = []
    for item in evidence[:25]:
        lines.append(f"- `{item.id}` `{item.path}`: {item.summary}")
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

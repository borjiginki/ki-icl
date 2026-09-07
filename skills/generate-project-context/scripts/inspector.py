"""Deterministic, read-only inspection of software repositories for this skill.

Target files are parsed as untrusted data. No target code is imported or executed.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

MAX_FILES_CONSIDERED = 20_000
MAX_FILES_INSPECTED = 5_000
MAX_FILE_BYTES = 1_048_576
MAX_TOTAL_BYTES = 33_554_432

SECRET_PATTERNS = (
    ".env", ".env.*", ".npmrc", ".pypirc", ".netrc", ".git-credentials",
    "credentials*", "secrets*", "id_rsa*", "id_ed25519*", "*.key", "*.pem",
    "*.p12", "*.pfx", "kubeconfig",
)
_SECRET_RE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|credential)\s*[:=]\s*([^\s,;]+)")
_BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".woff", ".woff2", ".so", ".dylib", ".exe", ".dll", ".pyc"}
_EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "node_modules", "vendor", "dist", "build", "coverage", ".tox", ".mypy_cache", ".pytest_cache", "__pycache__", ".next", ".cache", ".terraform"}


@dataclass(frozen=True)
class Evidence:
    id: str
    category: str
    subject: str
    claim: str
    confidence: str
    evidence_kind: str
    source: str
    line_start: int | None = None
    line_end: int | None = None
    rationale: str | None = None


@dataclass
class EntryPoint:
    name: str
    kind: str
    command: str | None
    evidence_ids: list[str]


@dataclass
class Deployable:
    name: str
    kind: str
    evidence_ids: list[str]


@dataclass
class Technology:
    name: str
    role: str
    version_constraint: str | None
    confidence: str
    evidence_ids: list[str]


@dataclass
class Integration:
    name: str
    kind: str
    role: str
    confidence: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class Conflict:
    code: str
    summary: str
    evidence_ids: list[str]
    question: str | None


@dataclass(frozen=True)
class Warning:
    code: str
    message: str
    source: str | None


@dataclass(frozen=True)
class Unknown:
    code: str
    question: str
    reason: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class Repository:
    name: str
    root: str
    revision: str | None
    dirty: bool
    dirty_scope: str


@dataclass(frozen=True)
class Inspection:
    truncated: bool
    files_considered: int
    files_inspected: int
    bytes_read: int
    limits: dict[str, int]


@dataclass
class ProjectEvidence:
    schema_version: int
    repository: Repository
    inspection: Inspection
    evidence: list[Evidence] = field(default_factory=list)
    entry_points: list[EntryPoint] = field(default_factory=list)
    deployables: list[Deployable] = field(default_factory=list)
    technologies: list[Technology] = field(default_factory=list)
    integrations: list[Integration] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    warnings: list[Warning] = field(default_factory=list)
    unknowns: list[Unknown] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Collector:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.warnings: list[Warning] = []

    def add(self, category: str, subject: str, claim: str, kind: str, source: str,
            line: int | None = None, confidence: str = "certain", rationale: str | None = None) -> str:
        self.records.append({"category": category, "subject": subject, "claim": claim,
                             "confidence": confidence, "evidence_kind": kind, "source": source,
                             "line_start": line, "line_end": line, "rationale": rationale})
        return f"pending-{len(self.records)}"

    def warning(self, code: str, message: str, source: str | None = None) -> None:
        self.warnings.append(Warning(code, _redact(message), source))

    def finalize(self) -> tuple[list[Evidence], dict[str, str]]:
        original = {id(record): f"pending-{i}" for i, record in enumerate(self.records, 1)}
        self.records.sort(key=lambda x: (x["category"], x["subject"], x["source"], x["line_start"] or 0, x["line_end"] or 0, x["claim"]))
        result = []
        remap = {}
        for i, record in enumerate(self.records, 1):
            evidence_id = f"e-{i:04d}"
            result.append(Evidence(id=evidence_id, **record))
            remap[original[id(record)]] = evidence_id
        return result, remap


def _redact(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)


def _is_secret(path: Path) -> bool:
    import fnmatch
    return any(fnmatch.fnmatchcase(part.lower(), pattern.lower()) for part in path.parts for pattern in SECRET_PATTERNS)


def _safe_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(d for d in dirs if d not in _EXCLUDED_DIRS and not (current_path / d).is_symlink())
        for name in sorted(names):
            path = current_path / name
            try:
                if path.is_symlink() or _is_secret(path) or path.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                path.resolve().relative_to(root)
                files.append(path)
            except (OSError, ValueError):
                continue
    return files


def _git(root: Path, args: list[str]) -> str:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return ""


def _metadata(root: Path) -> tuple[str | None, bool]:
    revision = _git(root, ["rev-parse", "HEAD"]) or None
    status = _git(root, ["status", "--porcelain", "--untracked-files=all", "--", "."])
    return revision, bool(status)


def _read(path: Path, root: Path, stats: dict[str, int], collector: _Collector) -> str | None:
    try:
        path.resolve().relative_to(root)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES or stats["bytes"] + size > MAX_TOTAL_BYTES:
            collector.warning("inspection.limit", f"Skipped file because inspection byte limit was reached", path.relative_to(root).as_posix())
            return None
        data = path.read_bytes()
        stats["bytes"] += len(data)
        stats["inspected"] += 1
        return _redact(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        collector.warning("inspection.read_error", f"Could not safely read file: {exc}", path.relative_to(root).as_posix())
        return None


def inspect_project(root: Path) -> ProjectEvidence:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")
    collector, stats = _Collector(), {"bytes": 0, "inspected": 0}
    all_files = _safe_files(root)
    priority = ("README", "pyproject", "requirements", "package.json", "Dockerfile", "compose", ".github", "azure", ".gitlab", "Jenkinsfile", "terraform", ".tf")
    files = sorted(all_files, key=lambda p: (0 if any(x.lower() in p.name.lower() or x in p.parts for x in priority) else 1, p.relative_to(root).as_posix()))
    truncated = len(files) > MAX_FILES_CONSIDERED
    files = files[:MAX_FILES_CONSIDERED]
    contents: dict[str, str] = {}
    for path in files:
        if stats["inspected"] >= MAX_FILES_INSPECTED or stats["bytes"] >= MAX_TOTAL_BYTES:
            truncated = True
            break
        text = _read(path, root, stats, collector)
        if text is not None:
            contents[path.relative_to(root).as_posix()] = text
    if truncated:
        collector.warning("inspection.truncated", "Inspection limits prevented considering every candidate file")

    ids: dict[tuple[str, str, str], str] = {}
    def add(*args: Any, **kwargs: Any) -> str:
        pending = collector.add(*args, **kwargs)
        return pending

    technologies: list[Technology] = []
    integrations: list[Integration] = []
    entries: list[EntryPoint] = []
    deployables: list[Deployable] = []
    for source, text in contents.items():
        suffix = Path(source).suffix.lower()
        try:
            if Path(source).name == "pyproject.toml":
                data = tomllib.loads(text)
                project = data.get("project", {})
                if project.get("requires-python"):
                    pending = add("technology", "Python", "The project requires Python " + str(project["requires-python"]), "dependency_manifest", source, confidence="certain")
                    technologies.append(Technology("Python", "language/runtime", project["requires-python"], "certain", [pending]))
                for dep in project.get("dependencies", []):
                    match = re.match(r"([A-Za-z0-9_.-]+)\s*([<>=!~].*)?", dep)
                    if match:
                        name, constraint = match.groups()
                        line_no = next((n for n, line in enumerate(text.splitlines(), 1) if name.lower() in line.lower()), None)
                        pending = add("technology", name, f"The project declares the Python dependency {name}", "dependency_manifest", source, line=line_no, confidence="certain")
                        technologies.append(Technology(name, "Python dependency", constraint, "certain", [pending]))
                scripts = data.get("project", {}).get("scripts", {})
                for name, command in scripts.items():
                    pending = add("entry_point", name, f"The project exposes the command {name}", "runtime_configuration", source, confidence="certain")
                    entries.append(EntryPoint(name, "script", str(command), [pending]))
            elif Path(source).name == "package.json":
                data = json.loads(text)
                if data.get("engines", {}).get("node"):
                    pending = add("technology", "Node.js", "The project requires Node.js " + str(data["engines"]["node"]), "runtime_configuration", source, confidence="certain")
                    technologies.append(Technology("Node.js", "language/runtime", data["engines"]["node"], "certain", [pending]))
                for group in ("dependencies", "devDependencies", "peerDependencies"):
                    for name, constraint in data.get(group, {}).items():
                        pending = add("technology", name, f"The project declares the Node dependency {name}", "dependency_manifest", source, confidence="certain")
                        technologies.append(Technology(name, "Node dependency", constraint, "certain", [pending]))
                if data.get("scripts"):
                    for name, command in data["scripts"].items():
                        pending = add("entry_point", name, f"The project exposes the command {name}", "runtime_configuration", source, confidence="certain")
                        entries.append(EntryPoint(name, "script", command, [pending]))
            elif Path(source).name.startswith("requirements"):
                for line_no, line in enumerate(text.splitlines(), 1):
                    if not line.strip() or line.lstrip().startswith(("#", "-r ", "--requirement ", "-c ", "--constraint ")):
                        continue
                    match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*([<>=!~].*)?", line)
                    if match:
                        name, constraint = match.groups()
                        pending = add("technology", name, f"The project declares the Python dependency {name}", "dependency_manifest", source, line_no)
                        technologies.append(Technology(name, "Python dependency", constraint, "certain", [pending]))
            elif Path(source).name.startswith("Dockerfile"):
                pending = add("deployable", "container", "The project defines a container image", "deployment_configuration", source, 1)
                deployables.append(Deployable("container", "container", [pending]))
                for line_no, line in enumerate(text.splitlines(), 1):
                    if re.match(r"\s*(CMD|ENTRYPOINT)\b", line):
                        pending = add("entry_point", "container command", "The container declares a runtime command", "deployment_configuration", source, line_no)
                        entries.append(EntryPoint("container command", "container_command", line.split(None, 1)[1] if " " in line else None, [pending]))
            elif Path(source).name.startswith("tsconfig"):
                pending = add("technology", "TypeScript", "The repository contains TypeScript compiler configuration", "runtime_configuration", source, confidence="certain")
                technologies.append(Technology("TypeScript", "language", None, "certain", [pending]))
            elif Path(source).name in {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}:
                try:
                    import yaml
                    data = yaml.safe_load(text) or {}
                    for name in sorted((data.get("services") or {})):
                        pending = add("deployable", name, f"Compose declares the {name} service", "deployment_configuration", source, confidence="certain")
                        deployables.append(Deployable(name, "service", [pending]))
                except Exception as exc:
                    collector.warning("inspection.unsupported_format", f"Could not parse Compose configuration: {exc}", source)
            elif Path(source).suffix.lower() in {".tf"} or source.startswith(("k8s/", "kubernetes/", "manifests/", "deploy/")):
                pending = add("deployment", "infrastructure", "The repository declares infrastructure or orchestration configuration", "deployment_configuration", source, confidence="certain")
                technologies.append(Technology("Infrastructure configuration", "deployment", None, "certain", [pending]))
            elif source.startswith(".github/workflows/") or Path(source).name in {"azure-pipelines.yml", ".gitlab-ci.yml", "Jenkinsfile"}:
                pending = add("deployment", "CI/CD", "The repository declares continuous integration or deployment configuration", "deployment_configuration", source)
                technologies.append(Technology("CI/CD", "continuous integration/deployment", None, "certain", [pending]))
            elif Path(source).name in {"README.md", "README.rst"}:
                pending = add("purpose", "project", "The repository documents its purpose", "documentation", source)
            elif Path(source).name in {"pytest.ini", "tox.ini", "jest.config.js", "jest.config.ts", "vitest.config.ts", "playwright.config.ts"}:
                name = "pytest" if "pytest" in Path(source).name or Path(source).name == "tox.ini" else Path(source).stem.split(".")[0]
                pending = add("testing", name, f"The repository configures {name}", "runtime_configuration", source, confidence="certain")
                technologies.append(Technology(name, "testing", None, "certain", [pending]))
            elif source.startswith(("tests/", "test/", "__tests__/")) and Path(source).suffix in {".py", ".js", ".jsx", ".ts", ".tsx"}:
                pending = add("testing", "test suite", "The repository contains test source files", "source_code", source, confidence="likely", rationale="The path and file extension indicate test code")
                technologies.append(Technology("test suite", "testing", None, "likely", [pending]))
            elif suffix in {".py", ".js", ".ts", ".tsx"}:
                for name, kind, role in (("postgres", "database", "application persistence"), ("redis", "cache", "application caching"), ("kafka", "queue", "messaging"), ("sentry", "observability", "error reporting")):
                    if re.search(rf"\b{name}\b", text, re.IGNORECASE):
                        pending = add("integration", name, f"Source code references {name}", "source_code", source, confidence="likely", rationale="A source reference was found, but runtime configuration does not establish that it is active")
                        integrations.append(Integration(name, kind, role, "likely", [pending]))
        except (ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError, configparser.Error) as exc:
            collector.warning("inspection.unsupported_format", f"Could not parse supported file: {exc}", source)

    evidence, remap = collector.finalize()
    def mapped(items: list[Any]) -> list[Any]:
        for item in items:
            item.evidence_ids = sorted(remap.get(x, x) for x in item.evidence_ids)
        return items
    revision, dirty = _metadata(root)
    unknowns = [Unknown("project.purpose", "What is the project's intended outcome and primary users?", "The repository cannot establish business purpose or users", [])]
    return ProjectEvidence(1, Repository(root.name, str(root), revision, dirty, "inspected_root"), Inspection(truncated, len(files), stats["inspected"], stats["bytes"], {"max_files_considered": MAX_FILES_CONSIDERED, "max_files_inspected": MAX_FILES_INSPECTED, "max_file_bytes": MAX_FILE_BYTES, "max_total_bytes": MAX_TOTAL_BYTES}), evidence, mapped(entries), mapped(deployables), mapped(technologies), mapped(integrations), [], sorted(collector.warnings, key=lambda x: (x.code, x.source or "", x.message)), unknowns)

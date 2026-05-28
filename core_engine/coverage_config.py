"""
Coverage configuration helpers for the Python Auto Test Generator.

This module centralizes coverage-related behavior so CLI and GUI can build
consistent pytest-cov commands instead of duplicating coverage arguments in
multiple places.

Responsibilities:
- create / locate a project .coveragerc
- build pytest-cov command-line arguments
- clean coverage artifacts safely
- read a lightweight coverage summary from coverage.xml

The public API remains backward-compatible with the older GUI code:
    get_project_root(file_path=None)
    create_coveragerc(project_root=None)
    get_coveragerc_path(project_root=None)
    ensure_coverage_config(project_root=None)
    get_coverage_cli_args(target_module=..., include_branch=..., include_xml=..., project_root=...)
    cleanup_coverage_artifacts(project_root=None)
"""
from __future__ import annotations

import glob
import os
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    ".git",
    "requirements.txt",
)


# Cross-platform .coveragerc content. Keep this focused on excluding tests,
# generated output, virtual environments, caches, reports, and build artifacts.
# Do not omit core_engine here: sometimes the user wants to measure the tool
# itself, and sometimes they want to measure an external target.
DEFAULT_COVERAGERC_CONTENT = """[run]
# Measure branch coverage in addition to statement coverage.
branch = True

# Store paths relative to the project root when possible. This makes XML/HTML
# reports easier to compare across machines and CI environments.
relative_files = True

# Omit files that should not count as application/source coverage.
omit =
    # Test files and pytest helpers
    tests/*
    */tests/*
    test_*.py
    */test_*.py
    *_test.py
    */*_test.py
    conftest.py
    */conftest.py

    # Generated test output and reports
    tests_output/*
    */tests_output/*
    generated_tests/*
    */generated_tests/*
    htmlcov/*
    */htmlcov/*
    allure-results/*
    */allure-results/*

    # Virtual environments and installed packages
    .venv/*
    */.venv/*
    venv/*
    */venv/*
    env/*
    */env/*
    */site-packages/*

    # Build/package artifacts
    build/*
    */build/*
    dist/*
    */dist/*
    *.egg-info/*
    */*.egg-info/*

    # IDE/cache/system artifacts
    .idea/*
    */.idea/*
    .vscode/*
    */.vscode/*
    __pycache__/*
    */__pycache__/*
    .pytest_cache/*
    */.pytest_cache/*
    .mypy_cache/*
    */.mypy_cache/*
    .ruff_cache/*
    */.ruff_cache/*
    .git/*
    */.git/*
    *.pyc

[report]
ignore_errors = False
precision = 2
show_missing = True
skip_empty = True

[html]
directory = htmlcov

[xml]
output = coverage.xml

[json]
output = coverage.json
"""


# ============================================================================
# PATH HELPERS
# ============================================================================

def _as_path(path: str | os.PathLike[str] | Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_probably_file_path(path: Path) -> bool:
    """Return True for existing files or paths that look like Python files."""
    if path.exists():
        return path.is_file()
    return bool(path.suffix)


def _is_dangerous_cleanup_root(path: Path) -> bool:
    """Avoid cleaning coverage artifacts from root/home by accident."""
    resolved = path.resolve()
    home = Path.home().resolve()
    root = Path(resolved.anchor).resolve()
    return resolved in {home, root}


def get_project_root(file_path: Optional[str | os.PathLike[str] | Path] = None) -> Path:
    """
    Locate a project root from a file/folder path.

    The search walks upward until it finds a common Python project marker such
    as pyproject.toml, setup.py, setup.cfg, .git, or requirements.txt.

    If no marker is found:
    - for an explicit file path, return the file's parent folder
    - for an explicit directory path, return that directory
    - otherwise return the current working directory
    """
    if file_path is None:
        start = Path.cwd().resolve()
        fallback = start
    else:
        given = _as_path(file_path)
        start = given.parent if _is_probably_file_path(given) else given
        fallback = start

    current = start
    while True:
        for marker in PROJECT_ROOT_MARKERS:
            if (current / marker).exists():
                return current
        if current == current.parent:
            return fallback
        current = current.parent


# ============================================================================
# .coveragerc MANAGEMENT
# ============================================================================

def create_coveragerc(
    project_root: Optional[str | os.PathLike[str] | Path] = None,
    *,
    overwrite: bool = False,
    content: str = DEFAULT_COVERAGERC_CONTENT,
) -> Path:
    """
    Create .coveragerc in project_root if missing.

    Backward-compatible behavior: if .coveragerc already exists, return it
    unchanged unless overwrite=True is provided.
    """
    root = get_project_root(project_root) if project_root is not None else get_project_root()
    coveragerc_path = root / ".coveragerc"

    if coveragerc_path.exists() and not overwrite:
        return coveragerc_path

    try:
        root.mkdir(parents=True, exist_ok=True)
        coveragerc_path.write_text(content, encoding="utf-8")
        return coveragerc_path
    except (OSError, IOError) as exc:
        raise OSError(f"Cannot create .coveragerc at {coveragerc_path}: {exc}") from exc


def get_coveragerc_path(
    project_root: Optional[str | os.PathLike[str] | Path] = None,
) -> Optional[Path]:
    """Return the .coveragerc path if it exists, otherwise None."""
    root = get_project_root(project_root) if project_root is not None else get_project_root()
    coveragerc_path = root / ".coveragerc"
    return coveragerc_path if coveragerc_path.exists() else None


def ensure_coverage_config(
    project_root: Optional[str | os.PathLike[str] | Path] = None,
) -> Path:
    """Ensure .coveragerc exists and return its absolute path."""
    existing = get_coveragerc_path(project_root)
    if existing:
        return existing
    return create_coveragerc(project_root)


# ============================================================================
# PYTEST-COV ARGUMENT BUILDER
# ============================================================================

def _report_arg(report: str, destination: Optional[str] = None) -> str:
    if destination:
        return f"--cov-report={report}:{destination}"
    return f"--cov-report={report}"


def get_coverage_cli_args(
    target_module: Optional[str] = None,
    include_branch: bool = True,
    include_xml: bool = True,
    project_root: Optional[str | os.PathLike[str] | Path] = None,
    *,
    coverage_target: Optional[str] = None,
    include_term: bool = True,
    include_html: bool = True,
    include_json: bool = False,
    term_report: str = "term-missing",
    html_dir: str = "htmlcov",
    xml_path: str = "coverage.xml",
    json_path: str = "coverage.json",
    fail_under: Optional[float] = None,
    config_path: Optional[str | os.PathLike[str] | Path] = None,
    ensure_config: bool = False,
) -> list[str]:
    """
    Build pytest-cov arguments.

    Backward-compatible usage:
        get_coverage_cli_args(target_module="core_engine", include_branch=True, include_xml=True)

    New preferred usage:
        get_coverage_cli_args(coverage_target="src/package", include_html=True, include_xml=True)

    coverage_target/target_module can be either a module/package name or a path,
    because pytest-cov accepts both forms for --cov.
    """
    target = coverage_target or target_module
    if not target:
        raise ValueError("coverage_target or target_module is required")

    args: list[str] = [f"--cov={target}"]

    # This is safe even when .coveragerc also has branch=True. It makes the
    # command explicit, while the config remains the central default.
    if include_branch:
        args.append("--cov-branch")

    if include_term:
        args.append(_report_arg(term_report))
    if include_html:
        args.append(_report_arg("html", html_dir))
    if include_xml:
        args.append(_report_arg("xml", xml_path))
    if include_json:
        args.append(_report_arg("json", json_path))
    if fail_under is not None:
        args.append(f"--cov-fail-under={fail_under}")

    resolved_config: Optional[Path]
    if config_path is not None:
        resolved_config = _as_path(config_path)
    elif ensure_config:
        resolved_config = ensure_coverage_config(project_root)
    else:
        resolved_config = get_coveragerc_path(project_root)

    if resolved_config:
        args.append(f"--cov-config={resolved_config}")

    return args


# ============================================================================
# CLEANUP HELPERS
# ============================================================================

def _iter_artifact_paths(
    project_root: Path,
    *,
    include_data: bool = True,
    include_xml: bool = True,
    include_json: bool = True,
    include_html: bool = True,
    include_pytest_cache: bool = False,
    html_dir: str = "htmlcov",
    xml_path: str = "coverage.xml",
    json_path: str = "coverage.json",
) -> List[Path]:
    candidates: List[Path] = []

    if include_data:
        candidates.append(project_root / ".coverage")
        for path in glob.glob(str(project_root / ".coverage.*")):
            candidates.append(Path(path))
    if include_xml:
        candidates.append(project_root / xml_path)
    if include_json:
        candidates.append(project_root / json_path)
    if include_html:
        candidates.append(project_root / html_dir)
    if include_pytest_cache:
        candidates.append(project_root / ".pytest_cache")

    # Deduplicate while preserving order.
    out: List[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if not any(resolved == existing for existing in out):
            out.append(resolved)
    return out


def cleanup_coverage_artifacts(
    project_root: Optional[str | os.PathLike[str] | Path] = None,
    *,
    include_data: bool = True,
    include_xml: bool = True,
    include_json: bool = True,
    include_html: bool = True,
    include_pytest_cache: bool = False,
    html_dir: str = "htmlcov",
    xml_path: str = "coverage.xml",
    json_path: str = "coverage.json",
    dry_run: bool = False,
    raise_errors: bool = False,
    allow_dangerous_root: bool = False,
) -> List[Path]:
    """
    Remove old coverage artifacts and return the paths that were removed.

    Backward-compatible: callers may ignore the returned list.
    By default, this refuses to clean from filesystem root or the user's home
    directory to avoid accidental broad cleanup when project root inference is
    wrong.
    """
    root = get_project_root(project_root) if project_root is not None else get_project_root()

    if _is_dangerous_cleanup_root(root) and not allow_dangerous_root:
        raise ValueError(
            f"Refusing to clean coverage artifacts from dangerous root: {root}. "
            "Pass a specific project_root or allow_dangerous_root=True."
        )

    removed: List[Path] = []
    for path in _iter_artifact_paths(
        root,
        include_data=include_data,
        include_xml=include_xml,
        include_json=include_json,
        include_html=include_html,
        include_pytest_cache=include_pytest_cache,
        html_dir=html_dir,
        xml_path=xml_path,
        json_path=json_path,
    ):
        if not path.exists():
            continue
        if dry_run:
            removed.append(path)
            continue
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            removed.append(path)
        except (OSError, IOError) as exc:
            if raise_errors:
                raise OSError(f"Cannot remove coverage artifact {path}: {exc}") from exc
            # Keep old behavior: continue cleanup if one artifact cannot be removed.
            continue

    return removed


# ============================================================================
# COVERAGE REPORT READERS
# ============================================================================

def read_coverage_summary_from_xml(
    xml_path: str | os.PathLike[str] | Path = "coverage.xml",
) -> Dict[str, Any]:
    """
    Read a lightweight summary from coverage.py XML output.

    Returns an empty dict if the XML file does not exist or cannot be parsed.
    Typical returned fields:
        line_rate, branch_rate, lines_covered, lines_valid,
        branches_covered, branches_valid, line_percent, branch_percent
    """
    path = _as_path(xml_path)
    if not path.exists():
        return {}

    try:
        root = ET.parse(path).getroot()
    except Exception:
        return {}

    def _float_attr(name: str) -> Optional[float]:
        raw = root.attrib.get(name)
        if raw is None:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    def _int_attr(name: str) -> Optional[int]:
        raw = root.attrib.get(name)
        if raw is None:
            return None
        try:
            return int(float(raw))
        except ValueError:
            return None

    line_rate = _float_attr("line-rate")
    branch_rate = _float_attr("branch-rate")

    summary: Dict[str, Any] = {
        "xml_path": str(path),
        "line_rate": line_rate,
        "branch_rate": branch_rate,
        "lines_covered": _int_attr("lines-covered"),
        "lines_valid": _int_attr("lines-valid"),
        "branches_covered": _int_attr("branches-covered"),
        "branches_valid": _int_attr("branches-valid"),
    }
    if line_rate is not None:
        summary["line_percent"] = round(line_rate * 100, 2)
    if branch_rate is not None:
        summary["branch_percent"] = round(branch_rate * 100, 2)

    return summary

"""
conftest.py - optional pytest-html enrichment for Auto Test Generator.

"""
from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

try:
    from core_engine.coverage_config import read_coverage_summary_from_xml
except Exception:  # pragma: no cover - fallback for unusual import layouts
    read_coverage_summary_from_xml = None  # type: ignore[assignment]


_ATG_CONFIG: Optional[pytest.Config] = None
_ENV_COVERAGE_XML = "ATG_COVERAGE_XML"
_ENV_DISABLE_HTML_SUMMARY = "ATG_DISABLE_HTML_COVERAGE_SUMMARY"


# ==========================================================================
# PYTEST OPTIONS
# ==========================================================================

def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("auto-test-generator")
    group.addoption(
        "--atg-coverage-xml",
        action="store",
        default=None,
        help=(
            "Path to coverage.py XML output used to enrich pytest-html summary. "
            "Defaults to ATG_COVERAGE_XML or coverage.xml."
        ),
    )
    group.addoption(
        "--atg-no-html-coverage-summary",
        action="store_true",
        default=False,
        help="Disable Auto Test Generator coverage summary in pytest-html reports.",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Store config so optional pytest-html hooks with older 3-argument
    # signatures can still access options without relying on a session param.
    global _ATG_CONFIG
    _ATG_CONFIG = config


# ==========================================================================
# COVERAGE SUMMARY
# ==========================================================================

def _coverage_xml_path() -> Path:
    configured: Optional[str] = None
    if _ATG_CONFIG is not None:
        configured = _ATG_CONFIG.getoption("--atg-coverage-xml", default=None)

    raw = configured or os.getenv(_ENV_COVERAGE_XML) or "coverage.xml"
    return Path(raw).expanduser().resolve()


def _is_summary_disabled() -> bool:
    if os.getenv(_ENV_DISABLE_HTML_SUMMARY, "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if _ATG_CONFIG is None:
        return False
    return bool(_ATG_CONFIG.getoption("--atg-no-html-coverage-summary", default=False))


def _read_summary(xml_path: Path) -> Dict[str, Any]:
    if read_coverage_summary_from_xml is None:
        return {}
    try:
        return dict(read_coverage_summary_from_xml(xml_path))
    except Exception:
        return {}


def _format_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "N/A"


def _format_ratio(covered: Any, valid: Any) -> str:
    if covered is None or valid is None:
        return "N/A"
    return f"{covered}/{valid}"


def _coverage_summary_html(summary: Dict[str, Any]) -> str:
    line_percent = _format_percent(summary.get("line_percent"))
    branch_percent = _format_percent(summary.get("branch_percent"))
    lines = _format_ratio(summary.get("lines_covered"), summary.get("lines_valid"))
    branches = _format_ratio(summary.get("branches_covered"), summary.get("branches_valid"))
    xml_path = html.escape(str(summary.get("xml_path", "coverage.xml")))

    # Keep this self-contained and conservative. pytest-html accepts strings in
    # pytest_html_results_summary; no direct report.html mutation is needed.
    return f"""
    <section id="atg-coverage-summary" style="
        margin: 12px 0 18px 0;
        padding: 12px 14px;
        border-left: 4px solid #2e7d32;
        background: #f6f8fa;
        border-radius: 6px;
    ">
        <h3 style="margin: 0 0 8px 0; font-size: 16px;">Coverage Summary</h3>
        <table style="border-collapse: collapse; min-width: 360px;">
            <tbody>
                <tr>
                    <td style="padding: 4px 12px 4px 0; font-weight: 600;">Line coverage</td>
                    <td style="padding: 4px 0;">{line_percent}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 12px 4px 0; font-weight: 600;">Branch coverage</td>
                    <td style="padding: 4px 0;">{branch_percent}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 12px 4px 0; font-weight: 600;">Lines</td>
                    <td style="padding: 4px 0;">{html.escape(lines)}</td>
                </tr>
                <tr>
                    <td style="padding: 4px 12px 4px 0; font-weight: 600;">Branches</td>
                    <td style="padding: 4px 0;">{html.escape(branches)}</td>
                </tr>
            </tbody>
        </table>
        <p style="margin: 8px 0 0 0; color: #666; font-size: 12px;">
            Source: {xml_path}
        </p>
    </section>
    """


# ==========================================================================
# PYTEST-HTML HOOK
# ==========================================================================

@pytest.hookimpl(optionalhook=True)
def pytest_html_results_summary(prefix: list[Any], summary: list[Any], postfix: list[Any]) -> None:
    """
    Add coverage information to pytest-html's Summary section when available.

    This is an optional hook: when pytest-html is not installed/enabled, pytest
    must not treat this as an unknown hook error.
    """
    if _is_summary_disabled():
        return

    xml_path = _coverage_xml_path()
    coverage_summary = _read_summary(xml_path)
    if not coverage_summary:
        return

    prefix.append(_coverage_summary_html(coverage_summary))

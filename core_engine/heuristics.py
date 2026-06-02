"""
Static-analysis driven heuristics for automated test value generation.

This module converts parser metadata into deterministic input values for
pytest generation. It is intentionally conservative: it does not execute target
code and it keeps the legacy public API used by code_generator.py.

Pipeline:
    Type hints / AST constraints / semantic name hints
    -> inferred parameter type
    -> boundary values
    -> safe values / raise values / smoke value
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ============================================================================
# SEMANTIC CATEGORIES
# ============================================================================

class SemanticCategory(str, Enum):
    EMAIL = "email"
    AGE = "age"
    PASSWORD = "password"
    PHONE = "phone"
    URL = "url"
    LATITUDE = "latitude"
    LONGITUDE = "longitude"
    PERCENTAGE = "percentage"
    PRICE = "price"
    COUNT = "count"
    ID = "id"
    NAME = "name"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    USERNAME = "username"
    PATH = "path"
    COLLECTION = "collection"


_SEMANTIC_KEYWORDS: list[tuple[SemanticCategory, tuple[str, ...]]] = [
    (SemanticCategory.EMAIL, ("email", "e_mail", "mail")),
    (SemanticCategory.PASSWORD, ("password", "passwd", "pwd", "passphrase")),
    (SemanticCategory.PHONE, ("phone", "mobile", "tel", "telephone")),
    (SemanticCategory.URL, ("url", "uri", "link", "website", "endpoint")),
    (SemanticCategory.LATITUDE, ("latitude", "lat")),
    (SemanticCategory.LONGITUDE, ("longitude", "lng", "lon")),
    (SemanticCategory.PERCENTAGE, ("percentage", "percent", "ratio", "rate")),
    (SemanticCategory.PRICE, ("price", "amount", "total", "subtotal", "fee", "cost")),
    (SemanticCategory.AGE, ("age",)),
    (SemanticCategory.COUNT, ("count", "qty", "quantity", "size", "length", "limit", "offset", "page")),
    (SemanticCategory.ID, ("id", "uuid", "code", "status_code")),
    (SemanticCategory.BOOLEAN, ("is_", "has_", "can_", "should_", "active", "enabled", "flag", "valid", "visible")),
    (SemanticCategory.USERNAME, ("username", "user_name", "login")),
    (SemanticCategory.NAME, ("name", "title", "label")),
    (SemanticCategory.TEXT, ("text", "msg", "message", "description", "content", "comment")),
    (SemanticCategory.DATE, ("date", "time", "timestamp", "datetime")),
    (SemanticCategory.PATH, ("path", "file", "filename", "directory", "folder")),
    (SemanticCategory.COLLECTION, ("items", "list", "array", "collection", "records", "rows")),
]


# ============================================================================
# HELPERS
# ============================================================================

def _dedupe(values: List[Any]) -> List[Any]:
    """Remove duplicates while preserving order; supports unhashable values."""
    out: List[Any] = []
    for value in values:
        if not any(value == existing for existing in out):
            out.append(value)
    return out


def _sort_values(values: List[Any]) -> List[Any]:
    """Sort deterministically when values are comparable; otherwise keep order."""
    try:
        return sorted(values)
    except Exception:
        return values


def _normalize_annotation(annotation: str) -> str:
    """Normalize common Python/typing annotations to a small strategy type set."""
    text = (annotation or "Any").strip()
    if not text or text == "None":
        return "Any"

    text = text.replace("typing.", "")
    text = text.replace("builtins.", "")

    # Handle Optional[T] and Union[T, None].
    if text.startswith("Optional[") and text.endswith("]"):
        return _normalize_annotation(text[len("Optional["):-1])
    if text.startswith("Union[") and text.endswith("]"):
        parts = [p.strip() for p in text[len("Union["):-1].split(",")]
        non_none = [p for p in parts if p not in {"None", "NoneType"}]
        return _normalize_annotation(non_none[0]) if non_none else "Any"

    # Handle PEP 604 unions: str | None.
    if "|" in text:
        parts = [p.strip() for p in text.split("|")]
        non_none = [p for p in parts if p not in {"None", "NoneType"}]
        return _normalize_annotation(non_none[0]) if non_none else "Any"

    lowered = text.lower()
    if lowered in {"any", "object"}:
        return "Any"
    if lowered in {"int", "integer"}:
        return "int"
    if lowered in {"float", "double", "number"}:
        return "float"
    if lowered in {"str", "string"}:
        return "str"
    if lowered in {"bool", "boolean"}:
        return "bool"
    if lowered in {"list", "sequence", "iterable", "tuple", "set"}:
        return "list"
    if lowered in {"dict", "mapping"}:
        return "dict"

    generic_prefixes = {
        "list[": "list",
        "List[": "list",
        "Sequence[": "list",
        "Iterable[": "list",
        "tuple[": "list",
        "Tuple[": "list",
        "set[": "list",
        "Set[": "list",
        "dict[": "dict",
        "Dict[": "dict",
        "Mapping[": "dict",
    }
    for prefix, normalized in generic_prefixes.items():
        if text.startswith(prefix):
            return normalized

    return text


def _infer_semantic_category(param_name: str) -> Optional[SemanticCategory]:
    """Infer a lightweight semantic category from a parameter name."""
    raw = (param_name or "").strip()
    if not raw:
        return None

    name = raw.lower()
    normalized = name.replace("-", "_")

    for category, keywords in _SEMANTIC_KEYWORDS:
        for kw in keywords:
            if kw.endswith("_"):
                if normalized.startswith(kw):
                    return category
            elif kw == normalized or kw in normalized:
                return category

    return None


def _safe_string_variants(base: str = "test") -> List[str]:
    """Generate deterministic, safe string variants."""
    base = "test" if base is None else str(base)
    values = [
        "",
        base,
        base.strip(),
        base.upper(),
        base.lower(),
        f"{base}_alt",
        "a",
        "abc",
        "   ",
        "test value",
    ]
    return _dedupe(values)


def _domain_numeric_values(category: Optional[SemanticCategory]) -> List[Any]:
    """Return deterministic numeric values for known semantic domains."""
    if category == SemanticCategory.AGE:
        return [0, 1, 17, 18, 19, 65, 120]
    if category == SemanticCategory.PERCENTAGE:
        return [0, 1, 50, 99, 100]
    if category == SemanticCategory.PRICE:
        return [0, 1, 1000, 99900, 100000]
    if category == SemanticCategory.LATITUDE:
        return [-90.0, -1.0, 0.0, 1.0, 90.0]
    if category == SemanticCategory.LONGITUDE:
        return [-180.0, -1.0, 0.0, 1.0, 180.0]
    if category in {SemanticCategory.COUNT, SemanticCategory.ID}:
        return [0, 1, -1, 10, 100]
    return [0, 1, -1, 100]


def _collection_variants(elem_type: str = "mixed") -> List[Any]:
    """Generate deterministic list-like variants."""
    elem_type = _normalize_annotation(elem_type)
    if elem_type == "int":
        return [[], [0], [1], [1, 2, 3]]
    if elem_type == "str":
        return [[], [""], ["a"], ["a", "b"]]
    if elem_type == "dict":
        return [[], [{"k": 1}], [{"id": 1}, {"id": 2}]]
    return [[], [1], ["a"], [1, "a", True]]


def _semantic_values(category: Optional[SemanticCategory]) -> List[Any]:
    """Generate deterministic values for a semantic category."""
    if category is None:
        return []

    mapping: Dict[SemanticCategory, List[Any]] = {
        SemanticCategory.EMAIL: ["", "user@example.com", "admin@example.com", "invalid-email"],
        SemanticCategory.PASSWORD: ["", "ValidPassword123!", "password", "123456", "short"],
        SemanticCategory.PHONE: ["", "+84901234567", "0901234567", "invalid-phone"],
        SemanticCategory.URL: ["", "https://example.com", "http://localhost", "not-a-url"],
        SemanticCategory.USERNAME: ["", "admin", "test_user", "user123"],
        SemanticCategory.NAME: ["", "test", "admin", "Nguyen Van A"],
        SemanticCategory.TEXT: ["", "test", "a", "   ", "longer text value"],
        SemanticCategory.DATE: ["", "2026-01-01", "1970-01-01", "invalid-date"],
        SemanticCategory.PATH: ["", "file.txt", "/tmp/file.txt", "missing.file"],
        SemanticCategory.BOOLEAN: [True, False],
        SemanticCategory.COLLECTION: _collection_variants("mixed"),
    }
    if category in {
        SemanticCategory.AGE,
        SemanticCategory.COUNT,
        SemanticCategory.ID,
        SemanticCategory.LATITUDE,
        SemanticCategory.LONGITUDE,
        SemanticCategory.PERCENTAGE,
        SemanticCategory.PRICE,
    }:
        return _domain_numeric_values(category)

    return mapping.get(category, [])


def _base_values(annotation: str, param_name: str = "") -> List[Any]:
    """Base fallback values by type, enriched with semantic hints."""
    type_name = _normalize_annotation(annotation)
    category = _infer_semantic_category(param_name)
    values: List[Any] = []

    if category is not None:
        values.extend(_semantic_values(category))

    values.extend(type_based_values(type_name))
    return _dedupe(values)


def _matches(value: Any, op: str, pivot: Any) -> bool:
    """Check if value satisfies a comparison operator."""
    try:
        if op == "Eq":
            return value == pivot
        if op == "NotEq":
            return value != pivot
        if op == "Lt":
            return value < pivot
        if op == "LtE":
            return value <= pivot
        if op == "Gt":
            return value > pivot
        if op == "GtE":
            return value >= pivot
        if op == "In":
            return value in pivot
        if op == "NotIn":
            return value not in pivot
        if op == "Is":
            return value is pivot
        if op == "IsNot":
            return value is not pivot
        if op == "Truthy":
            return bool(value) is True
        if op == "Falsy":
            return bool(value) is False
    except Exception:
        return False
    return False


def _apply_transform(value: Any, transform: Optional[str]) -> Any:
    """Apply a parser transform to a candidate value for classification only."""
    try:
        if transform == "len":
            return len(value)
        if transform == "strip" and hasattr(value, "strip"):
            return value.strip()
        if transform == "lower" and hasattr(value, "lower"):
            return value.lower()
        if transform == "upper" and hasattr(value, "upper"):
            return value.upper()
        if transform == "subscript":
            return value[0]
    except Exception:
        return value
    return value


def _matches_constraint(value: Any, constraint: Dict[str, Any]) -> bool:
    compared = _apply_transform(value, constraint.get("transform"))
    return _matches(compared, constraint.get("op"), constraint.get("value"))


def _value_type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, tuple):
        return "list"
    if isinstance(value, set):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "Any"


# ============================================================================
# CONSTRAINT EXTRACTION
# ============================================================================

def _constraint_targets_arg(constraint: Dict[str, Any], arg_name: str) -> bool:
    """Return whether a normalized constraint applies to arg_name."""
    if not arg_name:
        return False
    candidates = {
        constraint.get("arg"),
        constraint.get("base_arg"),
        constraint.get("path"),
    }
    if arg_name in candidates:
        return True
    path = constraint.get("path")
    return isinstance(path, str) and path.startswith(f"{arg_name}.")


def _normalized_constraint_from_branch(branch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a legacy-compatible constraint from old branch fields."""
    arg = branch.get("arg")
    op = branch.get("op")
    if arg is None or op is None:
        return None
    return {
        "arg": arg,
        "base_arg": branch.get("base_arg") or arg,
        "path": branch.get("arg_path") or branch.get("path") or arg,
        "transform": branch.get("transform"),
        "expression_kind": branch.get("expression_kind"),
        "op": op,
        "value": branch.get("value"),
        "raise_when": branch.get("raise_when"),
        "exception_type": branch.get("exception_type"),
        "source": branch.get("source"),
    }


def extract_constraints(arg_name: str, branches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extract all constraints for a parameter.

    Supports both legacy parser branches (arg/op/value) and the newer
    ast_parser.py metadata (branch["constraints"] with transform/path/base_arg).
    """
    constraints: List[Dict[str, Any]] = []

    for branch in branches:
        nested = branch.get("constraints") or []
        if nested:
            for raw in nested:
                if not _constraint_targets_arg(raw, arg_name):
                    continue
                constraint = dict(raw)
                constraint.setdefault("raise_when", branch.get("raise_when"))
                constraint.setdefault("exception_type", branch.get("exception_type"))
                constraint.setdefault("branch_source", branch.get("source"))
                constraints.append(constraint)
            continue

        legacy = _normalized_constraint_from_branch(branch)
        if legacy and _constraint_targets_arg(legacy, arg_name):
            constraints.append(legacy)

    return _dedupe(constraints)


# ============================================================================
# TYPE INFERENCE
# ============================================================================

def _infer_type_from_semantics(param_name: str) -> str:
    category = _infer_semantic_category(param_name)
    if category in {
        SemanticCategory.EMAIL,
        SemanticCategory.PASSWORD,
        SemanticCategory.PHONE,
        SemanticCategory.URL,
        SemanticCategory.USERNAME,
        SemanticCategory.NAME,
        SemanticCategory.TEXT,
        SemanticCategory.DATE,
        SemanticCategory.PATH,
    }:
        return "str"
    if category in {
        SemanticCategory.AGE,
        SemanticCategory.COUNT,
        SemanticCategory.ID,
        SemanticCategory.PERCENTAGE,
        SemanticCategory.PRICE,
    }:
        return "int"
    if category in {SemanticCategory.LATITUDE, SemanticCategory.LONGITUDE}:
        return "float"
    if category == SemanticCategory.BOOLEAN:
        return "bool"
    if category == SemanticCategory.COLLECTION:
        return "list"
    return "Any"


def _infer_type_from_constraint(constraint: Dict[str, Any], param_name: str) -> str:
    transform = constraint.get("transform")
    value = constraint.get("value")
    op = constraint.get("op")

    if transform in {"strip", "lower", "upper", "startswith", "endswith", "isdigit", "isalpha"}:
        return "str"

    if transform == "len":
        semantic = _infer_type_from_semantics(param_name)
        if semantic in {"str", "list", "dict"}:
            return semantic
        name = (param_name or "").lower()
        if any(k in name for k in ["items", "list", "array", "rows", "records"]):
            return "list"
        return "str"

    if op in {"In", "NotIn"} and isinstance(value, (list, tuple, set)):
        values = list(value)
        if values:
            return _value_type_name(values[0])

    if op in {"Truthy", "Falsy"}:
        semantic = _infer_type_from_semantics(param_name)
        if semantic != "Any":
            return semantic
        return "bool"

    inferred = _value_type_name(value)
    return inferred


def infer_type(arg_spec: Dict[str, Any], branches: List[Dict[str, Any]]) -> str:
    """Infer type from annotation, AST constraints, and semantic fallback."""
    annotation = arg_spec.get("annotation", "Any")
    norm = _normalize_annotation(annotation)
    if norm != "Any":
        return norm

    param_name = arg_spec.get("name", "")
    for constraint in extract_constraints(param_name, branches):
        inferred = _infer_type_from_constraint(constraint, param_name)
        if inferred != "Any":
            return inferred

    return _infer_type_from_semantics(param_name)


# ============================================================================
# BOUNDARY STRATEGY
# ============================================================================

def _numeric_boundary(op: str, pivot: Any, inferred_type: str) -> List[Any]:
    if inferred_type == "int" and isinstance(pivot, int) and not isinstance(pivot, bool):
        if op in {"Eq", "Lt", "LtE", "Gt", "GtE"}:
            return [pivot - 1, pivot, pivot + 1]
        if op == "NotEq":
            return [pivot - 1, pivot + 1]
    if inferred_type == "float" and isinstance(pivot, (int, float)) and not isinstance(pivot, bool):
        eps = 0.1
        p = float(pivot)
        return [round(p - eps, 10), p, round(p + eps, 10)]
    return []


def _length_values(op: str, pivot: Any, target_type: str) -> List[Any]:
    """Generate strings/lists/dicts whose len(value) sits around pivot."""
    if not isinstance(pivot, int) or isinstance(pivot, bool):
        return []

    lengths = [max(0, pivot - 1), max(0, pivot), max(0, pivot + 1)]
    values: List[Any] = []

    def make(length: int) -> Any:
        if target_type == "list":
            return list(range(length))
        if target_type == "dict":
            return {f"k{i}": i for i in range(length)}
        return "a" * length

    for length in lengths:
        values.append(make(length))
    return _dedupe(values)


def _string_transform_values(transform: Optional[str], op: str, pivot: Any) -> List[Any]:
    if not isinstance(pivot, str):
        return []

    if transform == "strip":
        if pivot == "":
            return ["", "   ", " test ", "test"]
        return [pivot, f" {pivot} ", "", f"{pivot}_alt"]

    if transform == "lower":
        return [pivot, pivot.upper(), pivot.capitalize(), f"{pivot}_alt"]

    if transform == "upper":
        return [pivot, pivot.lower(), pivot.upper(), f"{pivot}_alt"]

    return []


def boundary_values(op: str, pivot: Any, inferred_type: str, transform: Optional[str] = None) -> List[Any]:
    """Generate operator-aware boundary values."""
    if transform == "len":
        return _length_values(op, pivot, inferred_type)

    transform_values = _string_transform_values(transform, op, pivot)
    if transform_values:
        return transform_values

    numeric = _numeric_boundary(op, pivot, inferred_type)
    if numeric:
        return numeric

    if inferred_type == "str" and isinstance(pivot, str):
        if op == "Eq":
            return ["", pivot, pivot + "_alt"]
        if op == "NotEq":
            return [pivot + "_alt", ""]
        if op in {"In", "NotIn"}:
            return [pivot, pivot + "_alt"]
        return ["", pivot]

    if inferred_type == "bool" and isinstance(pivot, bool):
        return [True, False]

    if op in {"In", "NotIn"} and isinstance(pivot, (list, tuple, set)):
        values = list(pivot)
        if values:
            return [values[0], values[-1], None]
        return [None]

    if op in {"Is", "IsNot"}:
        return [pivot, None] if pivot is not None else [None, 0, ""]

    if op == "Truthy":
        return [True, False] if inferred_type == "bool" else [1, 0]
    if op == "Falsy":
        return [0, 1]

    return [pivot] if pivot is not None else []


# ============================================================================
# FALLBACK TYPE VALUES
# ============================================================================

def type_based_values(type_name: str) -> List[Any]:
    """Fallback type-based test values when no constraints exist."""
    normalized = _normalize_annotation(type_name)
    strategies = {
        "int": [0, 1, -1, 100],
        "float": [0.0, 1.0, -1.0, 3.14],
        "str": ["", "test", "a"],
        "bool": [True, False],
        "list": [[], [1], [1, 2, 3]],
        "dict": [{}, {"k": 1}],
        "Any": [None, 0, "", True],
    }
    return list(strategies.get(normalized, [None]))


def _semantic_param_heuristic(param_name: str) -> List[Any]:
    """Semantic fallback for parameter names."""
    category = _infer_semantic_category(param_name)
    return _semantic_values(category)


# ============================================================================
# TRY-EXCEPT AWARE TEST GENERATION
# ============================================================================

def exception_trigger_values(
    exception_types: List[str],
    arg_name: str,
    inferred_type: str,
) -> List[Any]:
    """Generate values likely to trigger specific exception types."""
    trigger_values: List[Any] = []
    category = _infer_semantic_category(arg_name)

    for exc_type in exception_types:
        if exc_type == "ZeroDivisionError" and inferred_type in {"int", "float"}:
            trigger_values.append(0)
            if inferred_type == "float":
                trigger_values.append(0.0)

        elif exc_type == "IndexError" and inferred_type == "int":
            trigger_values.extend([-1, -100, 1000])

        elif exc_type == "ValueError":
            if inferred_type == "int":
                trigger_values.extend(["invalid", None])
            elif inferred_type == "float":
                trigger_values.extend(["not_a_number", None])
            elif inferred_type == "str":
                if category == SemanticCategory.EMAIL:
                    trigger_values.extend(["", "invalid-email"])
                elif category == SemanticCategory.URL:
                    trigger_values.extend(["", "not-a-url"])
                else:
                    trigger_values.extend(["", "\x00", "123abc"])
            else:
                trigger_values.extend([None, "invalid"])

        elif exc_type == "KeyError" and inferred_type == "str":
            trigger_values.extend(["", "nonexistent_key", "missing"])

        elif exc_type == "TypeError":
            trigger_values.append(None)
            if inferred_type in {"int", "float"}:
                trigger_values.append("string")
            elif inferred_type == "str":
                trigger_values.append(123)
            elif inferred_type == "list":
                trigger_values.append("not_a_list")
            elif inferred_type == "dict":
                trigger_values.append([])

        elif exc_type == "AttributeError":
            trigger_values.append(None)

        elif exc_type in {"Exception", "RuntimeError"}:
            trigger_values.extend([None, ""])

    return _dedupe(trigger_values)


# ============================================================================
# MAIN STRATEGY ENGINE
# ============================================================================

def _would_trigger_raise(value: Any, constraints: List[Dict[str, Any]]) -> bool:
    """Return True when a candidate value satisfies a known raise path."""
    for constraint in constraints:
        raise_when = constraint.get("raise_when")
        if raise_when not in {"truthy", "falsy"}:
            continue
        matches = _matches_constraint(value, constraint)
        if raise_when == "truthy" and matches:
            return True
        if raise_when == "falsy" and not matches:
            return True
    return False


def _classify_boundary_values(
    bounds: List[Any],
    constraint: Dict[str, Any],
    raise_when: Optional[str],
) -> tuple[List[Any], List[Any]]:
    safe_values: List[Any] = []
    raise_values: List[Any] = []

    for value in bounds:
        satisfies = _matches_constraint(value, constraint)
        if raise_when is None:
            safe_values.append(value)
        elif raise_when == "truthy":
            (raise_values if satisfies else safe_values).append(value)
        elif raise_when == "falsy":
            (raise_values if not satisfies else safe_values).append(value)
        else:
            safe_values.append(value)

    return safe_values, raise_values


def build_arg_strategy(
    arg_spec: Dict[str, Any],
    branches: List[Dict[str, Any]],
    try_except_blocks: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """
    Build test strategy for one parameter.

    Returns a legacy-compatible dict:
        {"safe": [...], "raise": [...], "smoke": value}
    """
    if try_except_blocks is None:
        try_except_blocks = []

    param_name = arg_spec.get("name", "")
    inferred_type = infer_type(arg_spec, branches)
    constraints = extract_constraints(param_name, branches)

    boundary_safe: List[Any] = []
    boundary_raise: List[Any] = []

    for constraint in constraints:
        op = constraint.get("op")
        value = constraint.get("value")
        transform = constraint.get("transform")
        raise_when = constraint.get("raise_when")
        if not op:
            continue

        bounds = boundary_values(op, value, inferred_type, transform=transform)
        safe_part, raise_part = _classify_boundary_values(bounds, constraint, raise_when)
        boundary_safe.extend(safe_part)
        boundary_raise.extend(raise_part)

    type_values = type_based_values(inferred_type)
    semantic_values = _semantic_param_heuristic(param_name)

    exception_trigger: List[Any] = []
    for block in try_except_blocks:
        except_types = block.get("except_types", [])
        if except_types:
            exception_trigger.extend(
                exception_trigger_values(except_types, param_name, inferred_type)
            )

    priority_boundary = _sort_values(_dedupe(boundary_safe))
    type_values = _sort_values(_dedupe(type_values))
    semantic_values = _sort_values(_dedupe(semantic_values))
    exception_trigger = _sort_values(_dedupe(exception_trigger))

    fallback_values: List[Any] = []
    for value in type_values + semantic_values:
        if value not in priority_boundary and value not in fallback_values:
            fallback_values.append(value)

    combined_safe = priority_boundary + fallback_values

    default = arg_spec.get("default")
    if default is not None and default not in combined_safe:
        combined_safe.insert(0, default)

    boundary_raise = _dedupe(boundary_raise)
    unsafe_fallback_values = [
        value for value in combined_safe
        if value not in boundary_raise and _would_trigger_raise(value, constraints)
    ]
    boundary_raise = _dedupe(boundary_raise + unsafe_fallback_values)
    combined_safe = [
        value for value in combined_safe
        if value not in boundary_raise and not _would_trigger_raise(value, constraints)
    ]
    if not combined_safe:
        combined_safe = type_values or semantic_values or [None]

    if len(priority_boundary) >= 2:
        smoke_value = priority_boundary[1]
    else:
        smoke_value = next(
            (
                value for value in combined_safe
                if value not in [None, "", [], {}]
            ),
            combined_safe[0] if combined_safe else None,
        )

    return {
        "safe": combined_safe,
        "raise": _dedupe(boundary_raise + exception_trigger),
        "smoke": smoke_value,
    }

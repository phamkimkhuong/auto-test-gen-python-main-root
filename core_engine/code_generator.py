"""
Module Code Generator - sinh mã nguồn kiểm thử Pytest tự động.

This module converts metadata from ast_parser.py and value strategies from
heuristics.py into deterministic pytest files. It keeps the legacy public API
used by cli.py/gui.py, while adding compatibility with the newer parser fields:
constructor metadata, method_kind, constraints, and return-expression metadata.

Design goals:
- Generate plain pytest by default so tests run without optional report plugins.
- Keep Allure integration optional via use_allure=True.
- Preserve deterministic, readable generated tests.
- Avoid executing target code during generation.
"""
from __future__ import annotations

import ast
import builtins
import itertools
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .ast_parser import parse_file
from .heuristics import build_arg_strategy

_BUILTIN_EXCEPTIONS = {
    name
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
}

_EMPTY_VALUES = [None, "", [], {}]


# ============================================================================
# SORTING & DETERMINISTIC ORDERING
# ============================================================================

def _dedupe(values: Iterable[Any]) -> List[Any]:
    """Remove duplicates while preserving order; supports unhashable values."""
    out: List[Any] = []
    for value in values:
        if not any(value == existing for existing in out):
            out.append(value)
    return out


def _sort_values(values: List[Any]) -> List[Any]:
    """Sort values deterministically, handling nested lists and mixed types."""
    if not values:
        return []

    has_none = any(value is None for value in values)
    non_none = [value for value in values if value is not None]
    if not non_none:
        return [None] if has_none else []

    deduped = _dedupe(non_none)

    try:
        if all(isinstance(v, bool) for v in deduped):
            sorted_vals = sorted(deduped)
        elif all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in deduped):
            sorted_vals = sorted(deduped)
        elif all(isinstance(v, str) for v in deduped):
            sorted_vals = sorted(deduped, key=lambda s: (len(s), s))
        elif all(isinstance(v, (list, tuple, set)) for v in deduped):
            sorted_vals = sorted(deduped, key=lambda x: (len(x), repr(x)))
        elif all(isinstance(v, dict) for v in deduped):
            sorted_vals = sorted(deduped, key=lambda x: (len(x), repr(x)))
        else:
            sorted_vals = sorted(deduped, key=lambda x: (type(x).__name__, repr(x)))
    except Exception:
        sorted_vals = deduped

    return ([None] if has_none else []) + sorted_vals


def _unique_tuples(tuples_: List[List[Any]]) -> List[List[Any]]:
    """Deduplicate list-based test tuples using repr as a stable key."""
    seen = set()
    out: List[List[Any]] = []
    for item in tuples_:
        key = repr(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _unique_names(names: Sequence[str]) -> List[str]:
    """Deduplicate import names while preserving order."""
    out: List[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


# ============================================================================
# SERIALIZATION HELPERS
# ============================================================================

def _serialize_value(value: Any) -> str:
    """Serialize a Python value to valid Python source code."""
    if isinstance(value, float):
        if math.isinf(value):
            return "float('inf')" if value > 0 else "float('-inf')"
        if math.isnan(value):
            return "float('nan')"
    return repr(value)


def _safe_identifier(text: str) -> str:
    """Return a stable identifier-safe fragment for generated test names."""
    out = []
    for ch in text:
        if ch.isalnum() or ch == "_":
            out.append(ch.lower())
        else:
            out.append("_")
    cleaned = "".join(out).strip("_")
    return cleaned or "target"


def _case_id(args: List[Dict[str, Any]], case: Any, max_len: int = 80) -> str:
    values = case if isinstance(case, (tuple, list)) else [case]
    raw = "-".join(
        f"{spec['name']}={repr(value)}"
        for spec, value in zip(args, values)
    )
    raw = raw.replace('"', "'")
    return raw[:max_len]


def _serialize_case(case: Any, is_single_param: bool) -> str:
    if isinstance(case, (tuple, list)):
        if is_single_param:
            return _serialize_value(case[0])
        elements = ", ".join(_serialize_value(value) for value in case)
        if len(case) == 1:
            elements += ","
        return f"({elements})"
    return _serialize_value(case)


def _serialize_param_names(param_names: Sequence[str]) -> str:
    if len(param_names) == 1:
        return repr(param_names[0])
    return "(" + ", ".join(repr(name) for name in param_names) + ")"


def _generate_parametrize_decorator(
    param_names: Union[str, Sequence[str]],
    cases: List[Any],
    ids: Optional[List[str]] = None,
) -> List[str]:
    """Generate a readable pytest.mark.parametrize decorator."""
    if isinstance(param_names, str):
        names = [part.strip() for part in param_names.split(",") if part.strip()]
    else:
        names = list(param_names)

    is_single_param = len(names) == 1
    lines = ["@pytest.mark.parametrize("]
    lines.append(f"    {_serialize_param_names(names)},")
    lines.append("    [")
    for case in cases:
        lines.append(f"        {_serialize_case(case, is_single_param)},")
    lines.append("    ],")
    if ids:
        lines.append("    ids=[")
        for id_str in ids:
            lines.append(f"        {id_str!r},")
        lines.append("    ],")
    lines.append(")")
    return lines


# ============================================================================
# ASSERTION INFERENCE
# ============================================================================

def _normalize_return_type(annotation: str) -> str:
    text = (annotation or "Any").replace("typing.", "").replace("builtins.", "").strip()
    return text or "Any"


def _literal_assertion_from_returns(func_data: Dict[str, Any], result_var: str = "result") -> Optional[str]:
    """
    Infer exact assertion only for very safe cases.

    We only assert exact values when the callable has a single literal return and
    no parsed branches. For multiple branch returns, exact expected output needs
    path-sensitive analysis and should not be guessed here.
    """
    returns = func_data.get("returns") or []
    if len(returns) != 1:
        return None
    if func_data.get("branches"):
        return None

    expr = returns[0].get("expression") or {}
    if expr.get("kind") != "literal":
        return None

    value = expr.get("literal")
    if value is None:
        return f"assert {result_var} is None"
    if value is True:
        return f"assert {result_var} is True"
    if value is False:
        return f"assert {result_var} is False"
    return f"assert {result_var} == {_serialize_value(value)}"


def _build_assertion(result_var: str, return_type: str) -> Optional[str]:
    """Build conservative assertion from return annotation."""
    rt = _normalize_return_type(return_type)

    simple_map = {
        "int": "int",
        "float": "float",
        "str": "str",
        "bool": "bool",
        "list": "list",
        "dict": "dict",
    }
    if rt in simple_map:
        return f"assert isinstance({result_var}, {simple_map[rt]})"

    if rt in {"None", "NoneType"}:
        return f"assert {result_var} is None"

    if rt.startswith("Optional[") and rt.endswith("]"):
        inner = rt[len("Optional["):-1].strip()
        inner_assert = _build_assertion(result_var, inner)
        if inner_assert and inner_assert.startswith(f"assert isinstance({result_var}, "):
            py_type = inner_assert.split(", ", 1)[1].rstrip(")")
            return f"assert {result_var} is None or isinstance({result_var}, {py_type})"

    if "|" in rt and "None" in rt:
        parts = [p.strip() for p in rt.split("|") if p.strip() not in {"None", "NoneType"}]
        if len(parts) == 1:
            return _build_assertion(result_var, f"Optional[{parts[0]}]")

    return None


def _infer_assertion_from_return(func_data: Dict[str, Any], result_var: str = "result") -> Optional[str]:
    """Infer a safe assertion from return metadata, then type annotation."""
    literal_assertion = _literal_assertion_from_returns(func_data, result_var)
    if literal_assertion:
        return literal_assertion

    returns = func_data.get("returns") or []
    if returns:
        expression_sources = " ".join((ret.get("source") or "") for ret in returns).lower()
        if "len(" in expression_sources:
            return f"assert isinstance({result_var}, int)"
        if any(token in expression_sources for token in [" is ", " is not ", "==", "!=", ">", "<"]):
            return f"assert isinstance({result_var}, bool)" if func_data.get("return_type") == "Any" else None

    return _build_assertion(result_var, func_data.get("return_type", "Any"))


# ============================================================================
# CALL TARGET MODEL
# ============================================================================

def _call_expression(func_name: str, args: List[Dict[str, Any]], value_names: List[str]) -> str:
    positional: List[str] = []
    keyword: List[str] = []
    for spec, name in zip(args, value_names):
        if spec.get("kind") == "keyword_only":
            keyword.append(f"{spec['name']}={name}")
        else:
            positional.append(name)
    joined = positional + keyword
    return f"{func_name}({', '.join(joined)})"


def _callable_has_unsupported_reason(func: Dict[str, Any]) -> bool:
    return bool(func.get("unsupported_reason")) or func.get("support_level") == "unsupported"


def _build_targets(functions: List[Dict[str, Any]], classes: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build generation targets with class context preserved."""
    supported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for func in functions:
        target = {"kind": "function", "func": func, "class": None}
        (skipped if _callable_has_unsupported_reason(func) else supported).append(target)

    for cls in classes:
        for method in cls.get("methods", []):
            target = {"kind": "method", "func": method, "class": cls}
            if _callable_has_unsupported_reason(method):
                skipped.append(target)
            else:
                supported.append(target)

    return supported, skipped


def _target_display_name(target: Dict[str, Any]) -> str:
    func = target["func"]
    cls = target.get("class")
    if cls:
        return f"{cls['class_name']}.{func['name']}"
    return func["name"]


def _target_test_name(target: Dict[str, Any], suffix: str) -> str:
    return f"test_{_safe_identifier(_target_display_name(target))}_{suffix}"


def _target_call_name(target: Dict[str, Any], arg_names: List[str], obj_var: Optional[str] = None) -> str:
    func = target["func"]
    cls = target.get("class")
    name = func["name"]
    method_kind = func.get("method_kind")

    if not cls:
        return _call_expression(name, func.get("args", []), arg_names)

    class_name = cls["class_name"]
    if method_kind in {"staticmethod", "classmethod"}:
        return f"{class_name}." + _call_expression(name, func.get("args", []), arg_names)
    if method_kind == "property":
        return f"{obj_var}.{name}" if obj_var else f"{class_name}().{name}"
    return f"{obj_var}." + _call_expression(name, func.get("args", []), arg_names)


def _needs_instance(target: Dict[str, Any]) -> bool:
    cls = target.get("class")
    if not cls:
        return False
    method_kind = target["func"].get("method_kind", "instance")
    return method_kind in {"instance", "property", None}


def _constructor_smoke_values(cls: Dict[str, Any]) -> List[Any]:
    constructor = cls.get("constructor") or {}
    args = constructor.get("args") or []
    if not args:
        return []
    bundle = _build_safe_cases(
        args,
        constructor.get("branches", []),
        constructor.get("try_except_blocks", []),
        limit=1,
    )
    return list(bundle.get("smoke", []))


def _constructor_expression(cls: Dict[str, Any]) -> str:
    class_name = cls["class_name"]
    constructor = cls.get("constructor") or {}
    args = constructor.get("args") or []
    values = _constructor_smoke_values(cls)
    if not args:
        return f"{class_name}()"

    positional: List[str] = []
    keyword: List[str] = []
    for spec, value in zip(args, values):
        rendered = _serialize_value(value)
        if spec.get("kind") == "keyword_only":
            keyword.append(f"{spec['name']}={rendered}")
        else:
            positional.append(rendered)
    joined = positional + keyword
    return f"{class_name}({', '.join(joined)})"


# ============================================================================
# STRATEGY BUILDERS
# ============================================================================

def _build_safe_cases(
    args: List[Dict[str, Any]],
    branches: List[Dict[str, Any]],
    try_except_blocks: List[Dict[str, Any]] | None = None,
    limit: int = 8,
) -> Dict[str, Any]:
    """Build smoke and boundary cases from heuristic strategies."""
    if try_except_blocks is None:
        try_except_blocks = []

    strategies = [build_arg_strategy(arg, branches, try_except_blocks) for arg in args]
    smoke = [strategy["smoke"] for strategy in strategies]

    # Scale max candidate slicing dynamically based on argument count to prevent explosion
    num_args = len(args)
    if num_args <= 1:
        max_per_arg = 100
    elif num_args == 2:
        max_per_arg = 25
    else:
        max_per_arg = 8

    safe_lists = [strategy["safe"][:max_per_arg] if strategy["safe"] else [None] for strategy in strategies]
    candidates = _unique_tuples([list(combo) for combo in itertools.product(*safe_lists)])
    if smoke not in candidates:
        candidates.insert(0, smoke)

    try:
        candidates = _sort_values(candidates)
    except Exception:
        pass

    # Find candidates that cover each branch
    branch_covering_cases: List[Any] = []
    from .assertion_inference import evaluate_condition
    for branch in branches:
        cond = branch.get("condition") or {}
        for combo in candidates:
            input_values = {}
            for arg_spec, val in zip(args, combo):
                input_values[arg_spec.get("name", "")] = val
            try:
                if evaluate_condition(cond, input_values):
                    if combo not in branch_covering_cases:
                        branch_covering_cases.append(combo)
                    break
            except Exception:
                pass

    # Find fallback/default candidates (that match none of the branches)
    fallback_cases: List[Any] = []
    for combo in candidates:
        matched_any = False
        for branch in branches:
            cond = branch.get("condition") or {}
            input_values = {}
            for arg_spec, val in zip(args, combo):
                input_values[arg_spec.get("name", "")] = val
            try:
                if evaluate_condition(cond, input_values):
                    matched_any = True
                    break
            except Exception:
                pass
        if not matched_any:
            if combo not in fallback_cases:
                fallback_cases.append(combo)
            if len(fallback_cases) >= 2:
                break

    # Prioritize candidates according to selection logic
    prioritized: List[Any] = []
    if smoke not in prioritized:
        prioritized.append(smoke)

    for case in branch_covering_cases:
        if case not in prioritized:
            prioritized.append(case)

    for case in fallback_cases:
        if case not in prioritized:
            prioritized.append(case)

    # Fill remaining spots from sorted candidates
    for combo in candidates:
        if combo not in prioritized:
            prioritized.append(combo)

    # Ensure limit is dynamic to accommodate all branch-covering plus fallback cases
    min_required = len(branch_covering_cases) + len(fallback_cases)
    if smoke not in branch_covering_cases and smoke not in fallback_cases:
        min_required += 1

    actual_limit = max(limit, min_required)

    return {
        "strategies": strategies,
        "smoke": smoke,
        "boundary": prioritized[:actual_limit],
    }


def _infer_exception_name(func: Dict[str, Any]) -> Optional[str]:
    """Infer a builtin exception type that generated tests can catch safely."""
    if func.get("unconditional_raise"):
        types_ = func.get("exception_types", [])
        if len(types_) == 1 and types_[0] in _BUILTIN_EXCEPTIONS:
            return types_[0]
        return None

    branch_types = [
        branch.get("exception_type")
        for branch in func.get("branches", [])
        if branch.get("raise_when") in {"truthy", "falsy"} and branch.get("exception_type")
    ]
    unique_branch_types = list(dict.fromkeys(branch_types))
    if len(unique_branch_types) == 1 and unique_branch_types[0] in _BUILTIN_EXCEPTIONS:
        return unique_branch_types[0]

    try_except_types: List[str] = []
    for block in func.get("try_except_blocks", []):
        try_except_types.extend(block.get("except_types", []))
    unique_try_types = [name for name in dict.fromkeys(try_except_types) if name in _BUILTIN_EXCEPTIONS]
    if len(unique_try_types) == 1:
        return unique_try_types[0]

    return None


def _branch_target_names(branch: Dict[str, Any]) -> List[str]:
    """Extract target parameter names from new constraints or legacy fields."""
    names: List[str] = []
    for constraint in branch.get("constraints", []) or []:
        for key in ("arg", "base_arg"):
            value = constraint.get(key)
            if isinstance(value, str) and value not in names:
                names.append(value)
    legacy = branch.get("arg")
    if isinstance(legacy, str) and legacy not in names:
        names.append(legacy)
    return names


def _build_raise_cases(
    args: List[Dict[str, Any]],
    func: Dict[str, Any],
    safe_smoke: List[Any],
    try_except_blocks: List[Dict[str, Any]] | None = None,
) -> List[List[Any]]:
    """Build cases that are expected to raise exceptions."""
    if try_except_blocks is None:
        try_except_blocks = []

    if func.get("unconditional_raise"):
        return [list(safe_smoke)]

    strategies = [build_arg_strategy(arg, func.get("branches", []), try_except_blocks) for arg in args]
    strategies_by_name = {spec["name"]: strategy for spec, strategy in zip(args, strategies)}
    index_by_name = {spec["name"]: index for index, spec in enumerate(args)}
    cases: List[List[Any]] = []

    # Conditional raise branches. New parser constraints are preferred; legacy
    # branch["arg"] remains supported.
    for branch in func.get("branches", []):
        if branch.get("raise_when") not in {"truthy", "falsy"}:
            continue
        for target in _branch_target_names(branch):
            strategy = strategies_by_name.get(target)
            index = index_by_name.get(target)
            if strategy is None or index is None:
                continue
            for raise_value in strategy.get("raise", []):
                values = list(safe_smoke)
                values[index] = raise_value
                cases.append(values)

    # Try/except trigger values gathered by heuristics.
    for target, strategy in strategies_by_name.items():
        index = index_by_name.get(target)
        if index is None:
            continue
        for raise_value in strategy.get("raise", []):
            values = list(safe_smoke)
            values[index] = raise_value
            cases.append(values)

    unique_cases = _unique_tuples(cases)
    return _sort_values(unique_cases) if len(unique_cases) > 1 else unique_cases


# ============================================================================
# LINE EMISSION HELPERS
# ============================================================================

def _extend_decorators(lines: List[str], *, is_async: bool, use_allure: bool, title: str) -> None:
    if is_async:
        lines.append("@pytest.mark.asyncio")
    if use_allure:
        lines.append(f"@allure.title({title!r})")


def _emit_input_data(lines: List[str], args: List[Dict[str, Any]]) -> None:
    lines.append("    input_data = {")
    for spec in args:
        lines.append(f"        {spec['name']!r}: {spec['name']},")
    lines.append("    }")


def _emit_allure_attach(lines: List[str], expression: str, name: str) -> None:
    lines.extend([
        "    allure.attach(",
        f"        repr({expression}),",
        f"        name={name!r},",
        "        attachment_type=allure.attachment_type.TEXT,",
        "    )",
    ])


def _emit_execute_call(
    lines: List[str],
    *,
    call_expr: str,
    is_async: bool,
    use_allure: bool,
    display_name: str,
    assign_result: bool = True,
    indent: str = "    ",
) -> None:
    prefix = "await " if is_async else ""
    assignment = "result = " if assign_result else ""
    statement = f"{assignment}{prefix}{call_expr}"
    if use_allure:
        lines.append(f"{indent}with allure.step('Execute {display_name}'):")
        lines.append(f"{indent}    {statement}")
    else:
        lines.append(f"{indent}{statement}")


def _emit_result_assertion(lines: List[str], assert_line: Optional[str], *, use_allure: bool) -> None:
    indent = "        " if use_allure else "    "
    if assert_line:
        lines.append(f"{indent}{assert_line}")
    else:
        lines.append(f"{indent}# Execution-only test: no reliable assertion could be inferred.")


def _emit_assertion_for_plan(
    lines: List[str],
    plan: Dict[str, Any],
    *,
    use_allure: bool,
    result_var: str = "result"
) -> None:
    indent = "        " if use_allure else "    "
    if plan["kind"] == "exact":
        lines.append(f"{indent}{plan['comment']}")
        lines.append(f"{indent}assert {result_var} == {plan['expected']}")
    elif plan["kind"] == "type":
        lines.append(f"{indent}{plan['comment']}")
        if "expected_type" in plan:
            lines.append(f"{indent}assert isinstance({result_var}, {plan['expected_type']})")
        else:
            lines.append(f"{indent}assert {result_var} is None")
    else:
        lines.append(f"{indent}# Assertion strength: 0 (Smoke test)")
        lines.append(f"{indent}# Execution-only test: no reliable assertion could be inferred.")



def _emit_instance_setup(lines: List[str], target: Dict[str, Any], obj_var: Optional[str]) -> None:
    if not _needs_instance(target) or not obj_var:
        return
    cls = target["class"]
    lines.append(f"    {obj_var} = {_constructor_expression(cls)}")


def _emit_value_assignments(lines: List[str], args: List[Dict[str, Any]], values: Sequence[Any]) -> None:
    for spec, value in zip(args, values):
        lines.append(f"    {spec['name']} = {_serialize_value(value)}")


# ============================================================================
# MAIN GENERATION
# ============================================================================

def generate_test_file(
    source_file: str,
    output_dir: str,
    module_path: Optional[str] = None,
    dry_run: bool = False,
    use_allure: bool = False,
) -> Dict[str, Any]:
    """Generate a pytest file for one Python source module."""
    try:
        parsed_data = parse_file(source_file)
        functions = parsed_data["functions"]
        classes = parsed_data["classes"]

        supported_targets, skipped_targets = _build_targets(functions, classes)
        skipped_callables = [target["func"] for target in skipped_targets]

        if dry_run:
            return {
                "status": "dry_run",
                "source_file": source_file,
                "functions": functions,
                "classes": classes,
                "generated_tests": 0,
                "output_file": None,
                "skipped_functions": skipped_callables,
                "exact_assertions": 0,
                "expression_assertions": 0,
                "type_assertions": 0,
                "smoke_assertions": 0,
            }

        if not supported_targets:
            return {
                "status": "no_supported_functions",
                "source_file": source_file,
                "functions": functions,
                "classes": classes,
                "generated_tests": 0,
                "output_file": None,
                "skipped_functions": skipped_callables,
                "exact_assertions": 0,
                "expression_assertions": 0,
                "type_assertions": 0,
                "smoke_assertions": 0,
            }

        module_name = os.path.basename(source_file).replace(".py", "")
        source_dir = os.path.dirname(os.path.abspath(source_file))

        function_imports = [target["func"]["name"] for target in supported_targets if target["kind"] == "function"]
        class_imports = [target["class"]["class_name"] for target in supported_targets if target.get("class")]
        all_imports = _unique_names(function_imports + class_imports)
        import_base = f"{module_path}.{module_name}" if module_path else module_name
        import_stmt = f"from {import_base} import {', '.join(all_imports)}"

        lines: List[str] = [
            "import sys",
            "import os",
            f"sys.path.insert(0, r'{source_dir}')",
            "",
            "import pytest",
        ]
        if use_allure:
            lines.append("import allure")
        lines.extend([import_stmt, ""])

        generated_tests = 0
        exact_assertions = 0
        expression_assertions = 0
        type_assertions = 0
        smoke_assertions = 0

        for target in supported_targets:
            func = target["func"]
            args = func.get("args", [])
            name = func["name"]
            display_name = _target_display_name(target)
            is_async = bool(func.get("is_async", False))
            def_prefix = "async def" if is_async else "def"
            assert_line = _infer_assertion_from_return(func)
            obj_var = f"obj_{_safe_identifier(target['class']['class_name'])}" if _needs_instance(target) else None

            # Property targets are read-only expressions and must not receive params.
            if func.get("method_kind") == "property" and args:
                lines.extend([
                    "@pytest.mark.skip(reason='property with parameters is not supported safely')",
                    f"def {_target_test_name(target, 'property_unsupported')}():",
                    "    pass",
                    "",
                ])
                generated_tests += 1
                continue

            if not args:
                call_expr = _target_call_name(target, [], obj_var)
                if func.get("unconditional_raise"):
                    exc_name = _infer_exception_name(func)
                    if exc_name:
                        _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Exception test for {display_name}")
                        lines.append(f"{def_prefix} {_target_test_name(target, 'raises')}():")
                        lines.append('    """Exception-path test generated from raise-condition analysis."""')
                        _emit_instance_setup(lines, target, obj_var)
                        if use_allure:
                            lines.append(f"    with allure.step('Expect exception from {display_name}'):")
                            lines.append(f"        with pytest.raises({exc_name}):")
                            lines.append(f"            {'await ' if is_async else ''}{call_expr}")
                        else:
                            lines.append(f"    with pytest.raises({exc_name}):")
                            lines.append(f"        {'await ' if is_async else ''}{call_expr}")
                        lines.append("")
                        generated_tests += 1
                        exact_assertions += 1
                    else:
                        lines.extend([
                            "@pytest.mark.skip(reason='unconditional raise detected but exact exception type unresolved')",
                            f"def {_target_test_name(target, 'raises_unresolved')}():",
                            "    pass",
                            "",
                        ])
                        generated_tests += 1
                    continue

                from .assertion_inference import infer_assertion_plan
                smoke_plan = infer_assertion_plan(func, [])

                _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Smoke test for {display_name}")
                lines.append(f"{def_prefix} {_target_test_name(target, 'smoke')}():")
                lines.append('    """Smoke test generated from static AST analysis."""')
                _emit_instance_setup(lines, target, obj_var)
                _emit_execute_call(
                    lines,
                    call_expr=call_expr,
                    is_async=is_async,
                    use_allure=use_allure,
                    display_name=display_name,
                )
                _emit_assertion_for_plan(lines, smoke_plan, use_allure=use_allure)
                if use_allure:
                    _emit_allure_attach(lines, "result", "Output")
                lines.append("")
                generated_tests += 1

                if smoke_plan["strength_level"] in {2, 4}:
                    exact_assertions += 1
                elif smoke_plan["strength_level"] == 3:
                    expression_assertions += 1
                elif smoke_plan["strength_level"] == 1:
                    type_assertions += 1
                else:
                    smoke_assertions += 1
                continue

            safe_bundle = _build_safe_cases(args, func.get("branches", []), func.get("try_except_blocks", []))
            smoke_values = safe_bundle["smoke"]
            boundary_cases = safe_bundle["boundary"]
            arg_names = [arg["name"] for arg in args]
            param_names = ", ".join(arg_names)
            call_expr = _target_call_name(target, arg_names, obj_var)

            # Smoke test.
            from .assertion_inference import infer_assertion_plan
            smoke_plan = infer_assertion_plan(func, smoke_values)

            _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Smoke test for {display_name}")
            lines.append(f"{def_prefix} {_target_test_name(target, 'smoke')}():")
            lines.append('    """Smoke test generated from static AST analysis."""')
            _emit_instance_setup(lines, target, obj_var)
            _emit_value_assignments(lines, args, smoke_values)
            _emit_input_data(lines, args)
            if use_allure:
                _emit_allure_attach(lines, "input_data", "Input")
            _emit_execute_call(
                lines,
                call_expr=call_expr,
                is_async=is_async,
                use_allure=use_allure,
                display_name=display_name,
            )
            _emit_assertion_for_plan(lines, smoke_plan, use_allure=use_allure)
            if use_allure:
                _emit_allure_attach(lines, "result", "Output")
            lines.append("")
            generated_tests += 1

            if smoke_plan["strength_level"] in {2, 4}:
                exact_assertions += 1
            elif smoke_plan["strength_level"] == 3:
                expression_assertions += 1
            elif smoke_plan["strength_level"] == 1:
                type_assertions += 1
            else:
                smoke_assertions += 1

            # Boundary tests.
            if len(boundary_cases) > 1:
                plans = [infer_assertion_plan(func, case) for case in boundary_cases]
                can_parametrize_expected = all(p["kind"] == "exact" and p["strength_level"] in {2, 4} for p in plans)

                if can_parametrize_expected:
                    extended_arg_names = arg_names + ["expected"]
                    extended_param_names = ", ".join(extended_arg_names)
                    extended_cases = []
                    for case, plan in zip(boundary_cases, plans):
                        try:
                            raw_expected = ast.literal_eval(plan["expected"])
                        except Exception:
                            raw_expected = plan["expected"]
                        extended_cases.append(case + [raw_expected])

                    case_ids = [_case_id(args, case) for case in boundary_cases]
                    lines.extend(_generate_parametrize_decorator(extended_arg_names, extended_cases, case_ids))
                    _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Boundary test for {display_name}")
                    lines.append(f"{def_prefix} {_target_test_name(target, 'boundary')}({extended_param_names}):")
                    lines.append('    """Boundary test generated from AST constraints."""')
                    _emit_instance_setup(lines, target, obj_var)
                    _emit_input_data(lines, args)
                    if use_allure:
                        _emit_allure_attach(lines, "input_data", "Input")
                    _emit_execute_call(
                        lines,
                        call_expr=call_expr,
                        is_async=is_async,
                        use_allure=use_allure,
                        display_name=display_name,
                    )
                    max_strength = max(p["strength_level"] for p in plans)
                    desc = "Branch/path assertion" if max_strength == 4 else "Literal assertion"
                    lines.append(f"    # Assertion strength: {max_strength} ({desc})")
                    lines.append("    assert result == expected")
                    if use_allure:
                        _emit_allure_attach(lines, "result", "Output")
                    lines.append("")
                    exact_assertions += len(boundary_cases)
                else:
                    case_ids = [_case_id(args, case) for case in boundary_cases]
                    lines.extend(_generate_parametrize_decorator(arg_names, boundary_cases, case_ids))
                    _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Boundary test for {display_name}")
                    lines.append(f"{def_prefix} {_target_test_name(target, 'boundary')}({param_names}):")
                    lines.append('    """Boundary test generated from AST constraints."""')
                    _emit_instance_setup(lines, target, obj_var)
                    _emit_input_data(lines, args)
                    if use_allure:
                        _emit_allure_attach(lines, "input_data", "Input")
                    _emit_execute_call(
                        lines,
                        call_expr=call_expr,
                        is_async=is_async,
                        use_allure=use_allure,
                        display_name=display_name,
                    )
                    # Check if all plans are a uniform Level 3 expression
                    level_3_plans = [p for p in plans if p["strength_level"] == 3]
                    if len(level_3_plans) == len(plans) and len({p["expected"] for p in plans}) == 1:
                        plan = plans[0]
                        lines.append(f"    {plan['comment']}")
                        lines.append(f"    assert result == {plan['expected']}")
                        expression_assertions += len(boundary_cases)
                    else:
                        _emit_result_assertion(lines, assert_line, use_allure=use_allure)
                        if not assert_line:
                            smoke_assertions += len(boundary_cases)
                        elif "isinstance(" in assert_line or "is None" in assert_line:
                            type_assertions += len(boundary_cases)
                        elif "==" in assert_line:
                            exact_assertions += len(boundary_cases)
                        else:
                            smoke_assertions += len(boundary_cases)
                    if use_allure:
                        _emit_allure_attach(lines, "result", "Output")
                    lines.append("")
                generated_tests += len(boundary_cases)

            # Exception tests.
            raise_cases = _build_raise_cases(args, func, smoke_values, func.get("try_except_blocks", []))
            exc_name = _infer_exception_name(func)
            if raise_cases and exc_name:
                raise_ids = [_case_id(args, case) for case in raise_cases]
                lines.extend(_generate_parametrize_decorator(arg_names, raise_cases, raise_ids))
                _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Exception test for {display_name}")
                lines.append(f"{def_prefix} {_target_test_name(target, 'raises')}({param_names}):")
                lines.append('    """Exception-path test generated from raise-condition analysis."""')
                _emit_instance_setup(lines, target, obj_var)
                _emit_input_data(lines, args)
                if use_allure:
                    _emit_allure_attach(lines, "input_data", "Input")
                    lines.append(f"    with allure.step('Expect exception from {display_name}'):")
                    lines.append(f"        with pytest.raises({exc_name}):")
                    lines.append(f"            {'await ' if is_async else ''}{call_expr}")
                else:
                    lines.append(f"    with pytest.raises({exc_name}):")
                    lines.append(f"        {'await ' if is_async else ''}{call_expr}")
                lines.append("")
                generated_tests += len(raise_cases)
                exact_assertions += len(raise_cases)
            elif func.get("raises"):
                lines.extend([
                    "@pytest.mark.skip(reason='raise path detected but trigger tuple or exact exception type could not be inferred safely')",
                    f"def {_target_test_name(target, 'raises_unresolved')}():",
                    "    pass",
                    "",
                ])
                generated_tests += 1

        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"test_{module_name}.py")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")

        return {
            "status": "generated",
            "source_file": source_file,
            "functions": functions,
            "classes": classes,
            "generated_tests": generated_tests,
            "output_file": output_file,
            "skipped_functions": skipped_callables,
            "exact_assertions": exact_assertions,
            "expression_assertions": expression_assertions,
            "type_assertions": type_assertions,
            "smoke_assertions": smoke_assertions,
        }
    except Exception as e:
        return {
            "status": "syntax_error",
            "source_file": source_file,
            "message": f"{type(e).__name__}: {str(e)}",
            "functions": [],
            "classes": [],
            "generated_tests": 0,
            "output_file": None,
            "skipped_functions": [],
            "exact_assertions": 0,
            "expression_assertions": 0,
            "type_assertions": 0,
            "smoke_assertions": 0,
        }

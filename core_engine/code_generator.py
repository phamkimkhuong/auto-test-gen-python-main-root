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
import math
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from .ast_parser import parse_file
from .whitebox_planner import plan_whitebox_cases

_BUILTIN_EXCEPTIONS = {
    name
    for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException)
}

_EMPTY_VALUES = [None, "", [], {}]


# ============================================================================
# SORTING & DETERMINISTIC ORDERING
# ============================================================================

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


def _constructor_statement_values(cls: Dict[str, Any]) -> List[Any]:
    """Return one safe constructor tuple for instance method setup."""
    constructor = cls.get("constructor") or {}
    args = constructor.get("args") or []
    if not args:
        return []

    plan = plan_whitebox_cases(
        constructor,
        args,
        constructor.get("try_except_blocks", []),
    )
    cases = (
        plan.get("statement_cases")
        or plan.get("branch_cases")
        or []
    )
    return list(cases[0]) if cases else []


def _constructor_expression(cls: Dict[str, Any]) -> str:
    class_name = cls["class_name"]
    constructor = cls.get("constructor") or {}
    args = constructor.get("args") or []
    values = _constructor_statement_values(cls)
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
# EXCEPTION INFERENCE
# ============================================================================

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
        lines.append(f"{indent}# Assertion strength: 0 (Execution-only fallback)")
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
# WHITE-BOX EMISSION HELPERS
# ============================================================================

def _record_assertion_stats(plan: Dict[str, Any], count: int = 1) -> Tuple[int, int, int, int]:
    """Return assertion counters for one inferred assertion plan."""
    level = int(plan.get("strength_level", 0) or 0)
    if level in {2, 4}:
        return count, 0, 0, 0
    if level == 3:
        return 0, count, 0, 0
    if level == 1:
        return 0, 0, count, 0
    return 0, 0, 0, count


def _cases_with_expected(
    func: Dict[str, Any],
    cases: List[List[Any]],
) -> Tuple[List[Dict[str, Any]], bool, List[List[Any]]]:
    """Build assertion plans and optional expected-value parameter cases."""
    from .assertion_inference import infer_assertion_plan

    plans = [infer_assertion_plan(func, case) for case in cases]
    can_parametrize_expected = bool(cases) and all(
        plan.get("kind") == "exact" and plan.get("strength_level") in {2, 4}
        for plan in plans
    )
    if not can_parametrize_expected:
        return plans, False, []

    extended_cases: List[List[Any]] = []
    for case, plan in zip(cases, plans):
        try:
            raw_expected = ast.literal_eval(plan["expected"])
        except Exception:
            raw_expected = plan["expected"]
        extended_cases.append(list(case) + [raw_expected])
    return plans, True, extended_cases


def _emit_normal_coverage_test(
    lines: List[str],
    *,
    target: Dict[str, Any],
    func: Dict[str, Any],
    args: List[Dict[str, Any]],
    cases: List[List[Any]],
    suffix: str,
    title: str,
    docstring: str,
    call_expr: str,
    obj_var: Optional[str],
    is_async: bool,
    def_prefix: str,
    use_allure: bool,
    fallback_assertion: Optional[str],
) -> Tuple[int, int, int, int, int]:
    """
    Emit a statement/branch coverage test and return counters:
    (generated_tests, exact_assertions, expression_assertions, type_assertions, weak_assertions)
    """
    if not cases:
        return 0, 0, 0, 0, 0

    arg_names = [arg["name"] for arg in args]
    display_name = _target_display_name(target)
    plans, can_parametrize_expected, expected_cases = _cases_with_expected(func, cases)

    if args and len(cases) > 1:
        case_ids = [_case_id(args, case) for case in cases]
        if can_parametrize_expected:
            extended_arg_names = arg_names + ["expected"]
            extended_param_names = ", ".join(extended_arg_names)
            lines.extend(_generate_parametrize_decorator(extended_arg_names, expected_cases, case_ids))
            _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=title)
            lines.append(f"{def_prefix} {_target_test_name(target, suffix)}({extended_param_names}):")
            lines.append(f'    """{docstring}"""')
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
            max_strength = max(int(plan.get("strength_level", 0) or 0) for plan in plans)
            desc = "Branch/path assertion" if max_strength == 4 else "Literal assertion"
            lines.append(f"    # Assertion strength: {max_strength} ({desc})")
            lines.append("    assert result == expected")
            if use_allure:
                _emit_allure_attach(lines, "result", "Output")
            lines.append("")
            return len(cases), len(cases), 0, 0, 0

        lines.extend(_generate_parametrize_decorator(arg_names, cases, case_ids))
        _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=title)
        lines.append(f"{def_prefix} {_target_test_name(target, suffix)}({', '.join(arg_names)}):")
        lines.append(f'    """{docstring}"""')
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

        level_3_plans = [plan for plan in plans if plan.get("strength_level") == 3]
        if len(level_3_plans) == len(plans) and len({plan.get("expected") for plan in plans}) == 1:
            plan = plans[0]
            lines.append(f"    {plan['comment']}")
            lines.append(f"    assert result == {plan['expected']}")
            if use_allure:
                _emit_allure_attach(lines, "result", "Output")
            lines.append("")
            return len(cases), 0, len(cases), 0, 0

        _emit_result_assertion(lines, fallback_assertion, use_allure=use_allure)
        if use_allure:
            _emit_allure_attach(lines, "result", "Output")
        lines.append("")

        if not fallback_assertion:
            return len(cases), 0, 0, 0, len(cases)
        if "isinstance(" in fallback_assertion or " is None" in fallback_assertion:
            return len(cases), 0, 0, len(cases), 0
        if "==" in fallback_assertion:
            return len(cases), len(cases), 0, 0, 0
        return len(cases), 0, 0, 0, len(cases)

    # Single case or no-argument case: emit a compact plain test function.
    case = cases[0]
    plan = plans[0] if plans else None
    _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=title)
    lines.append(f"{def_prefix} {_target_test_name(target, suffix)}():")
    lines.append(f'    """{docstring}"""')
    _emit_instance_setup(lines, target, obj_var)
    if args:
        _emit_value_assignments(lines, args, case)
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
    if plan:
        _emit_assertion_for_plan(lines, plan, use_allure=use_allure)
        ex, expr, typ, weak = _record_assertion_stats(plan, 1)
    else:
        _emit_result_assertion(lines, fallback_assertion, use_allure=use_allure)
        ex, expr, typ, weak = (0, 0, 0, 1)
    if use_allure:
        _emit_allure_attach(lines, "result", "Output")
    lines.append("")
    return 1, ex, expr, typ, weak


def _emit_exception_coverage_test(
    lines: List[str],
    *,
    target: Dict[str, Any],
    args: List[Dict[str, Any]],
    cases: List[List[Any]],
    exc_name: str,
    call_expr: str,
    obj_var: Optional[str],
    is_async: bool,
    def_prefix: str,
    use_allure: bool,
) -> int:
    """Emit pytest.raises test cases and return generated case count."""
    if not cases:
        return 0

    arg_names = [arg["name"] for arg in args]
    display_name = _target_display_name(target)
    if args:
        case_ids = [_case_id(args, case) for case in cases]
        lines.extend(_generate_parametrize_decorator(arg_names, cases, case_ids))
        signature = ", ".join(arg_names)
    else:
        signature = ""

    _extend_decorators(lines, is_async=is_async, use_allure=use_allure, title=f"Exception test for {display_name}")
    lines.append(f"{def_prefix} {_target_test_name(target, 'exception')}({signature}):")
    lines.append('    """Exception test generated from white-box exception-path objectives."""')
    _emit_instance_setup(lines, target, obj_var)
    if args:
        _emit_input_data(lines, args)
        if use_allure:
            _emit_allure_attach(lines, "input_data", "Input")
    if use_allure:
        lines.append(f"    with allure.step('Expect exception from {display_name}'):")
        lines.append(f"        with pytest.raises({exc_name}):")
        lines.append(f"            {'await ' if is_async else ''}{call_expr}")
    else:
        lines.append(f"    with pytest.raises({exc_name}):")
        lines.append(f"        {'await ' if is_async else ''}{call_expr}")
    lines.append("")
    return len(cases)

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
                "weak_assertions": 0,
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
                "weak_assertions": 0,
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
        weak_assertions = 0

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

            arg_names = [arg["name"] for arg in args]
            call_expr = _target_call_name(target, arg_names, obj_var)
            whitebox_plan = plan_whitebox_cases(
                func,
                args,
                func.get("try_except_blocks", []),
            )
            unresolved_objectives = whitebox_plan.get("unresolved_objectives", [])
            # Store unresolved objectives on the function metadata so the result
            # can report limits without generating unreliable placeholder tests.
            if unresolved_objectives:
                func["unresolved_objectives"] = unresolved_objectives

            # Unconditional raise without parameters is a special case. The
            # current planner focuses on branch/decision objectives and keeps
            # no-argument unconditional raises safe here in the generator.
            if not args and func.get("unconditional_raise"):
                exc_name = _infer_exception_name(func)
                if exc_name:
                    emitted = _emit_exception_coverage_test(
                        lines,
                        target=target,
                        args=args,
                        cases=[[]],
                        exc_name=exc_name,
                        call_expr=call_expr,
                        obj_var=obj_var,
                        is_async=is_async,
                        def_prefix=def_prefix,
                        use_allure=use_allure,
                    )
                    generated_tests += emitted
                    exact_assertions += emitted
                else:
                    func.setdefault("unresolved_objectives", []).append({
                        "kind": "exception",
                        "description": "Unconditional raise detected but exact exception type could not be inferred safely.",
                    })
                continue

            # Statement coverage: used for callables without branch objectives.
            statement_cases = whitebox_plan.get("statement_cases", []) or []
            if statement_cases:
                counts = _emit_normal_coverage_test(
                    lines,
                    target=target,
                    func=func,
                    args=args,
                    cases=statement_cases,
                    suffix="statement_coverage",
                    title=f"Statement coverage test for {display_name}",
                    docstring="Statement coverage test generated from white-box objectives.",
                    call_expr=call_expr,
                    obj_var=obj_var,
                    is_async=is_async,
                    def_prefix=def_prefix,
                    use_allure=use_allure,
                    fallback_assertion=assert_line,
                )
                gen, ex, expr, typ, weak = counts
                generated_tests += gen
                exact_assertions += ex
                expression_assertions += expr
                type_assertions += typ
                weak_assertions += weak

            # Branch coverage: primary white-box output for callables with
            # decision branches. The planner has already selected reduced cases;
            # do not append extra heuristic candidate cases here.
            branch_cases = whitebox_plan.get("branch_cases", []) or []
            if branch_cases:
                counts = _emit_normal_coverage_test(
                    lines,
                    target=target,
                    func=func,
                    args=args,
                    cases=branch_cases,
                    suffix="branch_coverage",
                    title=f"Branch coverage test for {display_name}",
                    docstring="Branch coverage test generated from white-box branch objectives.",
                    call_expr=call_expr,
                    obj_var=obj_var,
                    is_async=is_async,
                    def_prefix=def_prefix,
                    use_allure=use_allure,
                    fallback_assertion=assert_line,
                )
                gen, ex, expr, typ, weak = counts
                generated_tests += gen
                exact_assertions += ex
                expression_assertions += expr
                type_assertions += typ
                weak_assertions += weak

            # Exception coverage: exception objectives are emitted separately so
            # pytest.raises(...) stays explicit and readable.
            exception_cases = whitebox_plan.get("exception_cases", []) or []
            exc_name = _infer_exception_name(func)
            if exception_cases and exc_name:
                emitted = _emit_exception_coverage_test(
                    lines,
                    target=target,
                    args=args,
                    cases=exception_cases,
                    exc_name=exc_name,
                    call_expr=call_expr,
                    obj_var=obj_var,
                    is_async=is_async,
                    def_prefix=def_prefix,
                    use_allure=use_allure,
                )
                generated_tests += emitted
                exact_assertions += emitted
            elif (func.get("raises") or any(obj.get("kind") == "exception" for obj in unresolved_objectives)) and not exception_cases:
                func.setdefault("unresolved_objectives", []).append({
                    "kind": "exception",
                    "description": "Exception path detected but trigger tuple or exact exception type could not be inferred safely.",
                })

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
            "weak_assertions": weak_assertions,
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
            "weak_assertions": 0,
        }

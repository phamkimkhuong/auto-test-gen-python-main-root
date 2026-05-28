"""
White-box planning layer for automatic pytest generation.

This module converts parser metadata into a compact set of test cases driven by
white-box testing objectives. It does NOT generate pytest code. Its only job is:

    AST metadata
    -> statement / branch / exception objectives
    -> candidate input tuples
    -> selected test cases that cover the objectives

Design principles:
- Branch/Decision Coverage is the main white-box criterion.
- Statement coverage is used for callables without decision branches.
- Exception paths are handled as first-class objectives and should be emitted
  as pytest.raises(...) tests by the code generator.
- Boundary values and semantic heuristic values are candidate sources only;
  they are not emitted automatically unless they help cover an objective.
- The selector uses a greedy set-cover style algorithm. It is deterministic and
  practical, but should be described as "reduced" or "near-minimal", not as a
  mathematical proof of the absolute minimum for every possible Python program.
"""

from __future__ import annotations

import ast
import itertools
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .assertion_inference import evaluate_condition
from .heuristics import build_arg_strategy


Case = List[Any]
Objective = Dict[str, Any]
CoverageMap = Dict[str, Set[str]]


# ============================================================================
# SMALL DETERMINISTIC HELPERS
# ============================================================================


def _dedupe_values(values: Iterable[Any]) -> List[Any]:
    """Remove duplicates while preserving order; supports unhashable values."""
    out: List[Any] = []
    for value in values:
        if not any(value == existing for existing in out):
            out.append(value)
    return out


def _dedupe_cases(cases: Iterable[Sequence[Any]]) -> List[Case]:
    """Remove duplicate input tuples while preserving order."""
    out: List[Case] = []
    seen: Set[str] = set()
    for case in cases:
        normalized = list(case)
        key = repr(normalized)
        if key not in seen:
            seen.add(key)
            out.append(normalized)
    return out


def _case_key(case: Sequence[Any]) -> str:
    return repr(list(case))


def _case_complexity(case: Sequence[Any]) -> Tuple[int, str]:
    """
    Prefer simple, stable values when two candidates cover the same objectives.

    This is intentionally conservative. It makes output deterministic and avoids
    selecting complicated containers unless they are needed for a coverage goal.
    """
    score = 0
    for value in case:
        if value in (None, "", [], {}):
            score += 3
        elif isinstance(value, bool):
            score += 1
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            score += 1 + min(int(abs(value)) if isinstance(value, int) else 2, 10)
        elif isinstance(value, str):
            score += 1 + min(len(value), 10)
        elif isinstance(value, (list, tuple, set, dict)):
            score += 4 + min(len(value), 10)
        else:
            score += 8
    return score, repr(list(case))


def _input_values(args: Sequence[Dict[str, Any]], case: Sequence[Any]) -> Dict[str, Any]:
    return {
        spec.get("name", ""): value
        for spec, value in zip(args, case)
        if spec.get("name")
    }




def _safe_eval_condition_source(source: str, values: Dict[str, Any]) -> Optional[bool]:
    """Evaluate a small, side-effect-free condition expression if possible.

    This is used only for planner classification of candidate values. It accepts
    simple expressions such as `n % 2 == 0`, arithmetic comparisons, boolean
    operators, and names already present in `values`. Calls, attributes,
    comprehensions, and subscripts are rejected.
    """
    if not source:
        return None
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError:
        return None

    allowed_nodes = (
        ast.Expression, ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare,
        ast.Name, ast.Load, ast.Constant,
        ast.And, ast.Or, ast.Not,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Is, ast.IsNot,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.USub, ast.UAdd,
    )
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes):
            return None
        if isinstance(node, ast.Name) and node.id not in values:
            return None

    try:
        return bool(eval(compile(tree, "<whitebox_condition>", "eval"), {"__builtins__": {}}, dict(values)))
    except Exception:
        return None

def _condition_matches(branch: Dict[str, Any], values: Dict[str, Any]) -> bool:
    cond = branch.get("condition") or {}
    try:
        if bool(evaluate_condition(cond, values)):
            return True
    except Exception:
        pass

    # Fallback for simple expressions not normalized by ast_parser.py, for
    # example loop-local conditions such as `n % 2 == 0`.
    source = cond.get("source") or branch.get("source") or ""
    evaluated = _safe_eval_condition_source(source, values)
    if evaluated is not None:
        return evaluated

    return False


def _loop_for_branch(func: Dict[str, Any], branch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return a simple for-loop context when a branch uses the loop target."""
    source = branch.get("source") or ""
    for loop in func.get("loops") or []:
        if loop.get("type") not in {"for", "async_for"}:
            continue
        target = loop.get("target")
        iter_name = loop.get("iter")
        if not target or not iter_name:
            continue
        # Current parser stores simple names as text. Avoid guessing for complex
        # iter expressions such as obj.items() at this stage.
        if not isinstance(iter_name, str) or not iter_name.isidentifier():
            continue
        if isinstance(target, str) and target in source:
            return loop
    return None


def _branch_outcomes(func: Dict[str, Any], branch: Dict[str, Any], values: Dict[str, Any]) -> Tuple[bool, bool, bool]:
    """
    Return (can_true, can_false, executed) for a branch under one input case.

    For ordinary branches, there is one condition evaluation. For a simple
    branch inside `for item in items`, the same input case may cover both true
    and false edges if the collection contains values of both kinds.
    """
    loop = _loop_for_branch(func, branch)
    if not loop:
        matched = _condition_matches(branch, values)
        return matched, not matched, True

    iter_name = loop.get("iter")
    target = loop.get("target")
    iterable = values.get(iter_name)
    if not isinstance(iterable, (list, tuple, set)):
        return False, False, False

    outcomes: List[bool] = []
    for item in iterable:
        local_values = dict(values)
        local_values[str(target)] = item
        outcomes.append(_condition_matches(branch, local_values))

    if not outcomes:
        # Empty loop executes no inner if. It may still help statement coverage
        # for the function, but it does not cover either branch edge.
        return False, False, False

    return any(outcomes), any(not result for result in outcomes), True


# ============================================================================
# BRANCH GROUPING
# ============================================================================


def _branch_groups(branches: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    Group simple if/elif chains and guard-clause chains.

    ast_parser.py emits each `if` or `elif` as a branch item in source order.
    This helper models two common control-flow shapes without building a full
    CFG:

    1. if/elif chains: later conditions are reachable only when previous
       conditions are false. Parser metadata marks intermediate items with
       has_elif=True.
    2. guard clauses with early return/raise:

           if x == "":
               return "empty"
           if len(x) == 1:
               return "single"
           return "other"

       The second if is reachable only if the first guard is false. Therefore
       consecutive guard branches are grouped as one decision chain.

    This is still not a full Control Flow Graph. It is a practical and
    deterministic approximation for the metadata produced by the current parser.
    """
    groups: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []

    for branch in branches:
        current.append(branch)

        if branch.get("has_elif"):
            continue

        if branch.get("has_direct_else"):
            groups.append(current)
            current = []
            continue

        is_guard = bool(branch.get("body_returns") or branch.get("has_body_raise"))
        if is_guard:
            # Keep the group open so following guard clauses are treated as
            # reachable only after this condition is false.
            continue

        groups.append(current)
        current = []

    if current:
        groups.append(current)

    return groups


def _branch_reachable_truthy(
    func: Dict[str, Any],
    group: Sequence[Dict[str, Any]],
    branch_index: int,
    values: Dict[str, Any],
) -> bool:
    """Return whether branch_index in an if/elif group is the taken truthy arm."""
    for previous in group[:branch_index]:
        previous_true, _, _ = _branch_outcomes(func, previous, values)
        if previous_true:
            return False
    current_true, _, _ = _branch_outcomes(func, group[branch_index], values)
    return current_true


def _group_reaches_default(func: Dict[str, Any], group: Sequence[Dict[str, Any]], values: Dict[str, Any]) -> bool:
    """Return whether the default/falsy path of a branch group is covered."""
    if len(group) == 1 and _loop_for_branch(func, group[0]):
        # For an if inside a loop, one input collection may cover both edges:
        # e.g. [1, 2, 3] covers false for 1/3 and true for 2.
        _, can_false, executed = _branch_outcomes(func, group[0], values)
        return executed and can_false

    any_executed = False
    for branch in group:
        can_true, _, executed = _branch_outcomes(func, branch, values)
        any_executed = any_executed or executed
        if can_true:
            return False
    return any_executed


def _has_fallthrough_default(group: Sequence[Dict[str, Any]]) -> bool:
    """
    Decide whether a branch group has a fallthrough/default path worth testing.

    - if/else: the else path is represented by has_direct_else=True on the final
      branch and should be modeled as a falsy branch.
    - if/elif without explicit else: the path where all conditions are false is
      a fallthrough/default path and should be tested.
    """
    if not group:
        return False
    last = group[-1]
    return not bool(last.get("has_direct_else"))


# ============================================================================
# OBJECTIVE BUILDING
# ============================================================================


def _objective_id(parts: Sequence[Any]) -> str:
    return ":".join(str(part) for part in parts if part is not None and part != "")


def build_whitebox_objectives(func: Dict[str, Any]) -> List[Objective]:
    """
    Build white-box testing objectives for one parsed callable.

    The returned objectives are intentionally simple and generator-friendly:
    - `statement`: one representative execution for callables without branches.
    - `branch`: a normal non-raising branch arm to execute.
    - `default`: a fallthrough/default path where all conditions in an if/elif
      chain are false.
    - `exception`: a path expected to raise a specific exception type.
    """
    branches = list(func.get("branches") or [])
    objectives: List[Objective] = []

    if not branches:
        objectives.append({
            "id": _objective_id(["statement", func.get("name") or func.get("qualified_name") or "callable"]),
            "kind": "statement",
            "description": "Execute callable at least once for statement coverage.",
        })
        return objectives

    groups = _branch_groups(branches)
    for group_index, group in enumerate(groups):
        for branch_index, branch in enumerate(group):
            source = branch.get("source") or f"branch_{branch_index}"
            lineno = branch.get("lineno")
            raise_when = branch.get("raise_when")
            exc_name = branch.get("exception_type")

            if raise_when == "truthy":
                objectives.append({
                    "id": _objective_id(["exception", lineno, source, exc_name or "Exception"]),
                    "kind": "exception",
                    "group_index": group_index,
                    "branch_index": branch_index,
                    "side": "truthy",
                    "source": source,
                    "lineno": lineno,
                    "exception_type": exc_name,
                    "description": f"Execute exception path for condition `{source}`.",
                })
            else:
                objectives.append({
                    "id": _objective_id(["branch", lineno, source, "truthy"]),
                    "kind": "branch",
                    "group_index": group_index,
                    "branch_index": branch_index,
                    "side": "truthy",
                    "source": source,
                    "lineno": lineno,
                    "description": f"Execute truthy branch for condition `{source}`.",
                })

            # A direct else on a simple if is a concrete branch. For if/elif
            # chains without else, default/fallthrough is handled once per group.
            is_last = branch_index == len(group) - 1
            if is_last and branch.get("has_direct_else"):
                if raise_when == "falsy":
                    objectives.append({
                        "id": _objective_id(["exception", lineno, source, "falsy", exc_name or "Exception"]),
                        "kind": "exception",
                        "group_index": group_index,
                        "branch_index": branch_index,
                        "side": "falsy",
                        "source": source,
                        "lineno": lineno,
                        "exception_type": exc_name,
                        "description": f"Execute exception else-path for condition `{source}`.",
                    })
                else:
                    objectives.append({
                        "id": _objective_id(["branch", lineno, source, "falsy"]),
                        "kind": "branch",
                        "group_index": group_index,
                        "branch_index": branch_index,
                        "side": "falsy",
                        "source": source,
                        "lineno": lineno,
                        "description": f"Execute falsy/else branch for condition `{source}`.",
                    })

        if _has_fallthrough_default(group):
            last = group[-1]
            objectives.append({
                "id": _objective_id(["default", group_index, last.get("lineno")]),
                "kind": "default",
                "group_index": group_index,
                "source": "fallthrough/default",
                "lineno": last.get("lineno"),
                "description": "Execute fallthrough/default path where all conditions in the chain are false.",
            })

    # Unconditional raise is not branch-specific, but it is still an exception
    # objective. This metadata is emitted by ast_parser.py.
    if func.get("unconditional_raise"):
        exc_types = func.get("exception_types") or ["Exception"]
        for exc_name in exc_types:
            objectives.append({
                "id": _objective_id(["exception", "unconditional", exc_name]),
                "kind": "exception",
                "exception_type": exc_name,
                "description": "Execute unconditional raise path.",
            })

    return _dedupe_objectives(objectives)


def _dedupe_objectives(objectives: Iterable[Objective]) -> List[Objective]:
    out: List[Objective] = []
    seen: Set[str] = set()
    for obj in objectives:
        oid = obj.get("id")
        if oid and oid not in seen:
            seen.add(oid)
            out.append(obj)
    return out


# ============================================================================
# CANDIDATE GENERATION
# ============================================================================


def _max_values_per_arg(arg_count: int) -> int:
    if arg_count <= 1:
        return 100
    if arg_count == 2:
        return 25
    return 8


def _strategy_bundle(
    args: Sequence[Dict[str, Any]],
    branches: Sequence[Dict[str, Any]],
    try_except_blocks: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    return [
        build_arg_strategy(arg, list(branches), list(try_except_blocks or []))
        for arg in args
    ]


def build_candidate_cases(
    args: Sequence[Dict[str, Any]],
    branches: Sequence[Dict[str, Any]],
    try_except_blocks: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, List[Case]]:
    """
    Build normal and exception candidate input tuples.

    Normal candidates are used for statement/branch/default objectives.
    Exception candidates are used for pytest.raises(...) objectives.
    """
    if not args:
        return {"normal": [[]], "exception": [[]] if branches else []}

    strategies = _strategy_bundle(args, branches, try_except_blocks)
    smoke = [strategy.get("smoke") for strategy in strategies]

    max_per_arg = _max_values_per_arg(len(args))
    safe_lists = [
        (strategy.get("safe") or [None])[:max_per_arg]
        for strategy in strategies
    ]
    normal = _dedupe_cases(itertools.product(*safe_lists))
    if smoke not in normal:
        normal.insert(0, smoke)

    exception_cases: List[Case] = []
    for index, strategy in enumerate(strategies):
        for raise_value in strategy.get("raise") or []:
            case = list(smoke)
            case[index] = raise_value
            exception_cases.append(case)

    return {
        "normal": _dedupe_cases(normal),
        "exception": _dedupe_cases(exception_cases),
    }


# ============================================================================
# OBJECTIVE EVALUATION
# ============================================================================


def evaluate_case_objectives(
    func: Dict[str, Any],
    args: Sequence[Dict[str, Any]],
    case: Sequence[Any],
    objectives: Sequence[Objective],
    *,
    allow_exception: bool = False,
) -> Set[str]:
    """Return objective IDs covered by a candidate case."""
    values = _input_values(args, case)
    branches = list(func.get("branches") or [])
    groups = _branch_groups(branches)
    covered: Set[str] = set()

    for objective in objectives:
        kind = objective.get("kind")
        oid = objective.get("id")
        if not oid:
            continue

        if kind == "statement":
            if not allow_exception:
                covered.add(oid)
            continue

        group_index = objective.get("group_index")
        branch_index = objective.get("branch_index")

        if kind == "branch":
            if group_index is None or group_index >= len(groups):
                continue
            group = groups[group_index]
            side = objective.get("side")
            if side == "truthy" and branch_index is not None:
                if _branch_reachable_truthy(func, group, int(branch_index), values):
                    covered.add(oid)
            elif side == "falsy" and branch_index is not None:
                # Direct else/falsy path on a single branch.
                if _branch_outcomes(func, group[int(branch_index)], values)[1]:
                    covered.add(oid)

        elif kind == "default":
            if group_index is not None and group_index < len(groups):
                if _group_reaches_default(func, groups[group_index], values):
                    covered.add(oid)

        elif kind == "exception" and allow_exception:
            if objective.get("branch_index") is None:
                # Unconditional raise objective.
                covered.add(oid)
                continue
            if group_index is None or group_index >= len(groups):
                continue
            group = groups[group_index]
            side = objective.get("side")
            if side == "truthy" and branch_index is not None:
                if _branch_reachable_truthy(func, group, int(branch_index), values):
                    covered.add(oid)
            elif side == "falsy" and branch_index is not None:
                if _branch_outcomes(func, group[int(branch_index)], values)[1]:
                    covered.add(oid)

    return covered


# ============================================================================
# CASE SELECTION
# ============================================================================


def select_covering_cases(
    candidates: Sequence[Case],
    coverage_map: CoverageMap,
    objective_ids: Set[str],
) -> Dict[str, Any]:
    """
    Select a reduced set of cases that covers the requested objectives.

    Uses deterministic greedy set cover. This is practical and stable for code
    generation; it should be reported as reduced/near-minimal rather than an
    absolute mathematical optimum.
    """
    uncovered = set(objective_ids)
    selected: List[Case] = []
    selected_keys: Set[str] = set()

    while uncovered:
        best_case: Optional[Case] = None
        best_gain: Set[str] = set()

        for case in candidates:
            key = _case_key(case)
            if key in selected_keys:
                continue
            gain = coverage_map.get(key, set()) & uncovered
            if not gain:
                continue
            if best_case is None:
                best_case = list(case)
                best_gain = set(gain)
                continue
            if len(gain) > len(best_gain):
                best_case = list(case)
                best_gain = set(gain)
            elif len(gain) == len(best_gain):
                if _case_complexity(case) < _case_complexity(best_case):
                    best_case = list(case)
                    best_gain = set(gain)

        if best_case is None:
            break

        selected.append(best_case)
        selected_keys.add(_case_key(best_case))
        uncovered -= best_gain

    return {
        "selected_cases": selected,
        "covered_objectives": sorted(objective_ids - uncovered),
        "uncovered_objectives": sorted(uncovered),
    }


def plan_whitebox_cases(
    func: Dict[str, Any],
    args: Sequence[Dict[str, Any]],
    try_except_blocks: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build a white-box test plan for a parsed callable.

    Returned shape is designed for code_generator.py:
        {
            "objectives": [...],
            "statement_cases": [...],
            "branch_cases": [...],
            "exception_cases": [...],
            "unresolved_objectives": [...],
        }
    """
    objectives = build_whitebox_objectives(func)
    normal_objectives = [obj for obj in objectives if obj.get("kind") != "exception"]
    exception_objectives = [obj for obj in objectives if obj.get("kind") == "exception"]

    candidates = build_candidate_cases(args, func.get("branches") or [], try_except_blocks)
    normal_candidates = candidates["normal"]
    exception_candidates = candidates["exception"]

    normal_ids = {obj["id"] for obj in normal_objectives if obj.get("id")}
    normal_map: CoverageMap = {
        _case_key(case): evaluate_case_objectives(
            func,
            args,
            case,
            normal_objectives,
            allow_exception=False,
        )
        for case in normal_candidates
    }
    normal_selection = select_covering_cases(normal_candidates, normal_map, normal_ids)

    exception_ids = {obj["id"] for obj in exception_objectives if obj.get("id")}
    exception_map: CoverageMap = {
        _case_key(case): evaluate_case_objectives(
            func,
            args,
            case,
            exception_objectives,
            allow_exception=True,
        )
        for case in exception_candidates
    }
    exception_selection = select_covering_cases(exception_candidates, exception_map, exception_ids)

    selected_normal = normal_selection["selected_cases"]
    selected_exception = exception_selection["selected_cases"]

    has_branch_like_objectives = any(
        obj.get("kind") in {"branch", "default"}
        for obj in normal_objectives
    )

    statement_cases: List[Case] = []
    branch_cases: List[Case] = []
    if has_branch_like_objectives:
        branch_cases = selected_normal
    else:
        statement_cases = selected_normal[:1]

    unresolved_ids = set(normal_selection["uncovered_objectives"]) | set(exception_selection["uncovered_objectives"])
    unresolved = [obj for obj in objectives if obj.get("id") in unresolved_ids]

    return {
        "objectives": objectives,
        "statement_cases": _dedupe_cases(statement_cases),
        "branch_cases": _dedupe_cases(branch_cases),
        "exception_cases": _dedupe_cases(selected_exception),
        "unresolved_objectives": unresolved,
        "covered_objective_ids": sorted(
            set(normal_selection["covered_objectives"]) | set(exception_selection["covered_objectives"])
        ),
    }

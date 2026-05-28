"""
Assertion Inference Engine - suy luận expected values và đánh giá độ mạnh của assertion.

This module analyzes AST metadata and candidate input values to determine
exact expected output values and assign Assertion Strength Levels (0 to 4).
It is 100% deterministic and does not execute untrusted target code.
"""
from __future__ import annotations

import ast
import builtins
from typing import Any, Dict, List, Optional, Set, Tuple, Union

_SAFE_BUILTINS = {"len", "abs", "min", "max", "sum", "round", "str", "int", "float", "bool", "list", "dict", "set", "tuple"}
_SAFE_METHODS = {"strip", "lower", "upper", "replace", "split", "startswith", "endswith", "get"}


def _is_safe_expression(source: str, args_set: Set[str]) -> bool:
    """
    Verify if a return expression source is safe to render in the test assertion.
    It must only reference function arguments and common python builtins.
    """
    if not source or source == "None":
        return False
    try:
        tree = ast.parse(source)
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Expr):
            return False
        
        expr = tree.body[0].value
        for node in ast.walk(expr):
            if isinstance(node, ast.Name):
                # Ensure it's a variable being read
                if node.id not in args_set and node.id not in _SAFE_BUILTINS:
                    return False
            # Block unsafe complex patterns
            if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
                return False
            # Allow basic calls like len(), lower(), upper(), strip()
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                # If calling a custom function not in arguments or builtins, mark unsafe
                if func_name and func_name not in args_set and func_name not in _SAFE_BUILTINS:
                    # Allow common string/list method names
                    if func_name not in _SAFE_METHODS:
                        return False
        return True
    except Exception:
        return False


def evaluate_constraint(constraint: Dict[str, Any], input_values: Dict[str, Any]) -> bool:
    """Evaluate an AST constraint against a dictionary of runtime argument values."""
    arg = constraint.get("arg")
    if not arg or arg not in input_values:
        return False

    val = input_values[arg]

    # Apply string/collection transforms
    transform = constraint.get("transform")
    if transform == "len":
        try:
            val = len(val)
        except Exception:
            return False
    elif transform == "strip":
        try:
            val = val.strip()
        except Exception:
            return False
    elif transform == "lower":
        try:
            val = val.lower()
        except Exception:
            return False
    elif transform == "upper":
        try:
            val = val.upper()
        except Exception:
            return False

    op = constraint.get("op")
    target_val = constraint.get("value")

    try:
        if op == "Eq":
            return val == target_val
        elif op == "NotEq":
            return val != target_val
        elif op == "Lt":
            return val < target_val
        elif op == "LtE":
            return val <= target_val
        elif op == "Gt":
            return val > target_val
        elif op == "GtE":
            return val >= target_val
        elif op == "In":
            return val in target_val
        elif op == "NotIn":
            return val not in target_val
        elif op == "Is":
            return val is target_val
        elif op == "IsNot":
            return val is not target_val
        elif op == "Truthy":
            return bool(val) is True
        elif op == "Falsy":
            return bool(val) is False
    except Exception:
        return False

    return False


def evaluate_condition(cond: Dict[str, Any], input_values: Dict[str, Any]) -> bool:
    """Recursively evaluate an AST parsed condition against runtime input values."""
    cond_type = cond.get("type")
    if cond_type == "compare":
        constraints = cond.get("constraints") or []
        if not constraints:
            # Fallback to single constraint if parsed in legacy fields
            legacy_arg = cond.get("left")
            if legacy_arg and "op" in cond:
                legacy_constraint = {
                    "arg": legacy_arg,
                    "op": cond.get("op"),
                    "value": cond.get("right"),
                }
                return evaluate_constraint(legacy_constraint, input_values)
            return False
        return all(evaluate_constraint(c, input_values) for c in constraints)

    elif cond_type == "boolop":
        op = cond.get("operator")
        values = cond.get("values") or []
        if op == "And":
            return all(evaluate_condition(v, input_values) for v in values)
        elif op == "Or":
            return any(evaluate_condition(v, input_values) for v in values)

    elif cond_type == "unaryop":
        op = cond.get("operator")
        if op == "Not":
            operand = cond.get("operand")
            if operand:
                return not evaluate_condition(operand, input_values)

    elif cond_type == "name":
        name = cond.get("name")
        val = input_values.get(name)
        return bool(val)

    elif cond_type == "constant":
        return bool(cond.get("value"))

    elif cond_type in {"call", "attribute"}:
        expr = cond.get("expression") or {}
        arg_name = expr.get("comparison_arg") or cond.get("attr") or cond.get("func_name")
        if arg_name and arg_name in input_values:
            val = input_values[arg_name]
            return bool(val)

    return False


def _fallback_to_safe_assertion(func_data: Dict[str, Any]) -> Dict[str, Any]:
    """Helper to return a Level 1 Type or Level 0 Smoke assertion."""
    ret_type = func_data.get("return_type") or "Any"
    if ret_type != "Any":
        simple_map = {
            "int": "int",
            "float": "float",
            "str": "str",
            "bool": "bool",
            "list": "list",
            "dict": "dict",
        }
        clean_type = ret_type.replace("typing.", "").replace("builtins.", "").strip()
        if clean_type in simple_map:
            return {
                "kind": "type",
                "expected_type": simple_map[clean_type],
                "strength_level": 1,
                "strength_name": "type",
                "comment": "# Assertion strength: 1 (Type assertion)",
            }
        if clean_type in {"None", "NoneType"}:
            return {
                "kind": "exact",
                "expected": "None",
                "strength_level": 1,
                "strength_name": "type",
                "comment": "# Assertion strength: 1 (Type assertion - None)",
            }

    return {
        "kind": "smoke",
        "strength_level": 0,
        "strength_name": "smoke",
        "comment": "# Assertion strength: 0 (Smoke test)",
    }


def infer_assertion_plan(
    func_data: Dict[str, Any],
    input_case: Union[List[Any], Tuple[Any, ...]]
) -> Dict[str, Any]:
    """
    Analyze function metadata and input values to infer the most appropriate
    Assertion Plan (expected value, strength level 0-4, and comment).
    """
    args = func_data.get("args") or []
    args_set = {arg["name"] for arg in args}

    # Map positional values to named dictionary
    input_values = {}
    for arg_spec, val in zip(args, input_case):
        input_values[arg_spec["name"]] = val

    # 1. Trace branches (Control Flow Path Analysis - Level 4)
    branches = func_data.get("branches") or []
    for branch in branches:
        cond = branch.get("condition") or {}
        if evaluate_condition(cond, input_values):
            # If the branch matches but contains nested control flow, fallback immediately
            if branch.get("has_nested_control_flow"):
                return _fallback_to_safe_assertion(func_data)

            # Check if this branch returns a value
            body_returns = branch.get("body_returns") or []
            if body_returns:
                ret = body_returns[0]
                expr = ret.get("expression") or {}
                # Handle literal return inside matched branch
                if expr.get("kind") == "literal":
                    lit_val = expr.get("literal")
                    return {
                        "kind": "exact",
                        "expected": repr(lit_val),
                        "strength_level": 4,
                        "strength_name": "branch_path",
                        "comment": "# Assertion strength: 4 (Branch/path assertion)",
                    }
                # Handle safe expression return inside matched branch
                ret_source = ret.get("source")
                if ret_source and _is_safe_expression(ret_source, args_set):
                    return {
                        "kind": "exact",
                        "expected": ret_source,
                        "strength_level": 3,
                        "strength_name": "expression",
                        "comment": "# Assertion strength: 3 (Expression assertion inside branch)",
                    }
        else:
            # Branch condition is False. If there's a direct orelse/else return block:
            if branch.get("has_direct_else"):
                # If the else branch matches but has nested control flow inside else, fallback
                if branch.get("has_else_nested_control_flow"):
                    return _fallback_to_safe_assertion(func_data)

                else_returns = branch.get("else_returns") or []
                if else_returns:
                    ret = else_returns[0]
                    expr = ret.get("expression") or {}
                    if expr.get("kind") == "literal":
                        lit_val = expr.get("literal")
                        return {
                            "kind": "exact",
                            "expected": repr(lit_val),
                            "strength_level": 4,
                            "strength_name": "branch_path",
                            "comment": "# Assertion strength: 4 (Branch/path assertion via else)",
                        }
                    ret_source = ret.get("source")
                    if ret_source and _is_safe_expression(ret_source, args_set):
                        return {
                            "kind": "exact",
                            "expected": ret_source,
                            "strength_level": 3,
                            "strength_name": "expression",
                            "comment": "# Assertion strength: 3 (Expression assertion inside else)",
                        }

    # 2. Outer-scope / fallthrough return analysis
    returns = func_data.get("returns") or []
    outer_returns = [r for r in returns if r.get("depth", 0) == 0]
    if outer_returns:
        # Check the last return statement (the fallback / default return)
        last_ret = outer_returns[-1]
        expr = last_ret.get("expression") or {}
        if expr.get("kind") == "literal":
            lit_val = expr.get("literal")
            level = 4 if branches else 2
            name = "branch_path" if branches else "literal"
            desc = "Branch/path fallthrough" if branches else "Literal assertion"
            return {
                "kind": "exact",
                "expected": repr(lit_val),
                "strength_level": level,
                "strength_name": name,
                "comment": f"# Assertion strength: {level} ({desc})",
            }
        
        ret_source = last_ret.get("source")
        if ret_source and _is_safe_expression(ret_source, args_set):
            return {
                "kind": "exact",
                "expected": ret_source,
                "strength_level": 3,
                "strength_name": "expression",
                "comment": "# Assertion strength: 3 (Expression assertion)",
            }
    else:
        # No top-level return statement means implicit return of None!
        level = 4 if branches else 2
        name = "branch_path" if branches else "literal"
        desc = "Branch/path fallthrough (implicit None)" if branches else "Implicit None"
        return {
            "kind": "exact",
            "expected": "None",
            "strength_level": level,
            "strength_name": name,
            "comment": f"# Assertion strength: {level} ({desc})",
        }

    # 3/4. Fallback to Type/Smoke Assertion
    return _fallback_to_safe_assertion(func_data)

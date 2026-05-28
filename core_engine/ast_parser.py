from __future__ import annotations

import ast
from typing import Any, Dict, List, Optional


# ============================================================================
# LOW-LEVEL HELPERS
# ============================================================================

def _safe_unparse(node: Optional[ast.AST]) -> str:
    """Return Python source text for an AST node when possible."""
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _node_location(node: ast.AST, source_text: Optional[str] = None) -> Dict[str, Any]:
    """
    Return stable source-location metadata for debugging/reporting.

    The old parser only returned structural metadata. Adding location data makes
    generated reports easier to trace back to source lines without changing the
    existing parse_file() API.
    """
    meta: Dict[str, Any] = {
        "lineno": getattr(node, "lineno", None),
        "end_lineno": getattr(node, "end_lineno", None),
        "col_offset": getattr(node, "col_offset", None),
        "end_col_offset": getattr(node, "end_col_offset", None),
    }
    if source_text is not None:
        try:
            meta["source_segment"] = ast.get_source_segment(source_text, node)
        except Exception:
            meta["source_segment"] = None
    return meta


def _literal_value(node: Optional[ast.AST]) -> Any:
    """
    Extract literal values from AST nodes.

    Handles primitive constants, unary numeric constants, and safe Python
    literals such as list/tuple/dict/set when ast.literal_eval can evaluate them.
    Returns None if the value cannot be safely extracted.
    """
    if node is None:
        return None

    if isinstance(node, ast.Constant):
        return node.value

    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
    ):
        value = node.operand.value
        if isinstance(value, (int, float)):
            return -value if isinstance(node.op, ast.USub) else value

    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def _annotation_to_text(node: Optional[ast.AST]) -> str:
    if node is None:
        return "Any"
    try:
        return ast.unparse(node)
    except Exception:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            return node.value.id
        return "Any"


def _raise_type_name(node: ast.Raise) -> Optional[str]:
    exc = node.exc
    if exc is None:
        return "Exception"
    if isinstance(exc, ast.Name):
        return exc.id
    if isinstance(exc, ast.Call):
        func = exc.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return _safe_unparse(func)
    if isinstance(exc, ast.Attribute):
        return _safe_unparse(exc)
    return None


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> List[str]:
    """Return decorator names in source-like form."""
    names: List[str] = []
    for dec in getattr(node, "decorator_list", []):
        names.append(_safe_unparse(dec))
    return names


def _method_kind(node: ast.FunctionDef | ast.AsyncFunctionDef, is_method: bool) -> str:
    """Classify a callable so the generator can call it correctly later."""
    if not is_method:
        return "function"
    decorators = set(_decorator_names(node))
    if "staticmethod" in decorators:
        return "staticmethod"
    if "classmethod" in decorators:
        return "classmethod"
    if "property" in decorators:
        return "property"
    return "instance"


# ============================================================================
# EXPRESSION RESOLUTION
# ============================================================================

def _resolve_expression(node: Optional[ast.AST]) -> Dict[str, Any]:
    """
    Resolve an expression into semantic metadata used by condition extraction.

    This is intentionally conservative. It does not execute code; it only
    preserves enough structural information for the heuristic layer.
    """
    if node is None:
        return {
            "kind": "none",
            "source": "",
            "is_symbol": False,
            "name": None,
            "base": None,
            "path": None,
            "leaf": None,
            "transform": None,
            "comparison_arg": None,
            "literal": None,
        }

    source = _safe_unparse(node)

    if isinstance(node, ast.Name):
        return {
            "kind": "name",
            "source": source,
            "is_symbol": True,
            "name": node.id,
            "base": node.id,
            "path": node.id,
            "leaf": node.id,
            "transform": None,
            "comparison_arg": node.id,
            "literal": None,
        }

    if isinstance(node, ast.Attribute):
        base_meta = _resolve_expression(node.value)
        base_path = base_meta.get("path") or base_meta.get("name")
        path = f"{base_path}.{node.attr}" if base_path else node.attr
        return {
            "kind": "attribute",
            "source": source,
            "is_symbol": True,
            "name": node.attr,
            "base": base_meta.get("base") or base_path,
            "path": path,
            "leaf": node.attr,
            "transform": None,
            # Preserve old behavior for backward compatibility: user.age -> age
            "comparison_arg": node.attr,
            "literal": None,
            "base_expression": base_meta,
        }

    if isinstance(node, ast.Call):
        func_meta = _resolve_expression(node.func)
        func_name = None
        method_name = None
        base_meta: Optional[Dict[str, Any]] = None
        transform = None
        kind = "call"

        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            transform = func_name
            if node.args:
                base_meta = _resolve_expression(node.args[0])
        elif isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            func_name = method_name
            transform = method_name
            base_meta = _resolve_expression(node.func.value)
            kind = "method_call"

        base = base_meta.get("base") if base_meta else None
        path = base_meta.get("path") if base_meta else None

        return {
            "kind": kind,
            "source": source,
            "is_symbol": True,
            "name": func_name,
            "func_name": func_name,
            "method_name": method_name,
            "base": base,
            "path": path,
            "leaf": base_meta.get("leaf") if base_meta else None,
            "transform": transform,
            "comparison_arg": base,
            "literal": None,
            "func_expression": func_meta,
            "arg_count": len(node.args),
        }

    if isinstance(node, ast.Subscript):
        base_meta = _resolve_expression(node.value)
        return {
            "kind": "subscript",
            "source": source,
            "is_symbol": True,
            "name": base_meta.get("name"),
            "base": base_meta.get("base"),
            "path": f"{base_meta.get('path')}[]" if base_meta.get("path") else source,
            "leaf": base_meta.get("leaf"),
            "transform": "subscript",
            "comparison_arg": base_meta.get("base"),
            "literal": None,
            "base_expression": base_meta,
        }

    literal = _literal_value(node)
    if literal is not None or isinstance(node, ast.Constant):
        return {
            "kind": "literal",
            "source": source,
            "is_symbol": False,
            "name": None,
            "base": None,
            "path": None,
            "leaf": None,
            "transform": None,
            "comparison_arg": None,
            "literal": literal,
            "literal_type": type(literal).__name__,
        }

    return {
        "kind": type(node).__name__,
        "source": source,
        "is_symbol": False,
        "name": None,
        "base": None,
        "path": None,
        "leaf": None,
        "transform": None,
        "comparison_arg": None,
        "literal": None,
    }


def _extract_symbol_name(node: ast.AST) -> Optional[str]:
    """
    Backward-compatible symbol extraction.

    New code should prefer _resolve_expression(), but existing heuristic and
    generator code still expect branch['arg'] for simple comparisons.
    """
    meta = _resolve_expression(node)
    return meta.get("comparison_arg") or meta.get("name")


def _invert_operator(op_name: str) -> str:
    """Invert an operator when literal/symbol sides are swapped."""
    return {
        "Lt": "Gt",
        "LtE": "GtE",
        "Gt": "Lt",
        "GtE": "LtE",
    }.get(op_name, op_name)


def _constraint_from_pair(
    left_node: ast.AST,
    op_node: ast.cmpop,
    right_node: ast.AST,
    source_text: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build a normalized constraint from a two-sided comparison."""
    op_name = type(op_node).__name__
    left_expr = _resolve_expression(left_node)
    right_expr = _resolve_expression(right_node)
    left_value = _literal_value(left_node)
    right_value = _literal_value(right_node)

    # Usual case: age >= 18, len(text) < 10, name.strip() == ""
    if left_expr.get("is_symbol") and not right_expr.get("is_symbol"):
        return {
            "arg": left_expr.get("comparison_arg"),
            "base_arg": left_expr.get("base"),
            "path": left_expr.get("path"),
            "transform": left_expr.get("transform"),
            "expression_kind": left_expr.get("kind"),
            "op": op_name,
            "value": right_value,
            "value_type": type(right_value).__name__ if right_value is not None else None,
            "source": f"{left_expr.get('source')} {op_name} {right_expr.get('source')}",
            "left_expression": left_expr,
            "right_expression": right_expr,
        }

    # Reversed case: 18 <= age -> age >= 18
    if right_expr.get("is_symbol") and not left_expr.get("is_symbol"):
        return {
            "arg": right_expr.get("comparison_arg"),
            "base_arg": right_expr.get("base"),
            "path": right_expr.get("path"),
            "transform": right_expr.get("transform"),
            "expression_kind": right_expr.get("kind"),
            "op": _invert_operator(op_name),
            "value": left_value,
            "value_type": type(left_value).__name__ if left_value is not None else None,
            "source": f"{left_expr.get('source')} {op_name} {right_expr.get('source')}",
            "left_expression": left_expr,
            "right_expression": right_expr,
        }

    # Symbol-vs-symbol comparisons are useful metadata, but not directly
    # convertible to a fixed literal test value yet.
    if left_expr.get("is_symbol") and right_expr.get("is_symbol"):
        return {
            "arg": left_expr.get("comparison_arg"),
            "base_arg": left_expr.get("base"),
            "path": left_expr.get("path"),
            "transform": left_expr.get("transform"),
            "expression_kind": left_expr.get("kind"),
            "op": op_name,
            "value": right_expr.get("comparison_arg") or right_expr.get("path"),
            "value_is_symbol": True,
            "source": f"{left_expr.get('source')} {op_name} {right_expr.get('source')}",
            "left_expression": left_expr,
            "right_expression": right_expr,
        }

    return None


# ============================================================================
# CONDITION PARSING
# ============================================================================

def _parse_condition(node: ast.AST, source_text: Optional[str] = None) -> Dict[str, Any]:
    """Parse an AST condition node into rich metadata and constraints."""
    source = _safe_unparse(node)
    location = _node_location(node, source_text)

    # Compare: age >= 18, len(name) < 3, x in [1, 2], x is None, 0 < age < 100
    if isinstance(node, ast.Compare):
        constraints: List[Dict[str, Any]] = []
        operands = [node.left] + list(node.comparators)
        for left_node, op_node, right_node in zip(operands, node.ops, operands[1:]):
            constraint = _constraint_from_pair(left_node, op_node, right_node, source_text)
            if constraint:
                constraints.append(constraint)

        if len(node.ops) == 1 and len(node.comparators) == 1:
            left_expr = _resolve_expression(node.left)
            right_expr = _resolve_expression(node.comparators[0])
            left_value = _literal_value(node.left)
            right_value = _literal_value(node.comparators[0])
            meta: Dict[str, Any] = {
                "type": "compare",
                "source": source,
                "op": type(node.ops[0]).__name__,
                "left": left_expr.get("comparison_arg") or left_value,
                "left_is_symbol": bool(left_expr.get("is_symbol")),
                "right": right_expr.get("comparison_arg") or right_value,
                "right_is_symbol": bool(right_expr.get("is_symbol")),
                "left_expression": left_expr,
                "right_expression": right_expr,
                "constraints": constraints,
                **location,
            }
            return meta

        return {
            "type": "compare",
            "source": source,
            "chained": True,
            "op_count": len(node.ops),
            "constraints": constraints,
            **location,
        }

    # BoolOp: a and b, a or b
    if isinstance(node, ast.BoolOp):
        operator = type(node.op).__name__
        values = [_parse_condition(value_node, source_text) for value_node in node.values]
        constraints: List[Dict[str, Any]] = []
        for value in values:
            constraints.extend(value.get("constraints", []))

        return {
            "type": "boolop",
            "source": source,
            "operator": operator,
            "values": values,
            "operand_count": len(node.values),
            "constraints": constraints,
            **location,
        }

    # UnaryOp: not flag, not items
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            operand = _parse_condition(node.operand, source_text)
            constraints = []
            if operand.get("type") in {"name", "attribute", "call"}:
                expr = operand.get("expression")
                if expr:
                    constraints.append({
                        "arg": expr.get("comparison_arg"),
                        "base_arg": expr.get("base"),
                        "path": expr.get("path"),
                        "transform": expr.get("transform"),
                        "expression_kind": expr.get("kind"),
                        "op": "Falsy",
                        "value": False,
                        "source": source,
                    })
            return {
                "type": "unaryop",
                "source": source,
                "operator": "Not",
                "operand": operand,
                "constraints": constraints,
                **location,
            }

        return {
            "type": "unaryop",
            "source": source,
            "operator": type(node.op).__name__,
            "constraints": [],
            **location,
        }

    # Name: if flag:
    if isinstance(node, ast.Name):
        expr = _resolve_expression(node)
        return {
            "type": "name",
            "source": source,
            "name": node.id,
            "expression": expr,
            "constraints": [{
                "arg": node.id,
                "base_arg": node.id,
                "path": node.id,
                "transform": None,
                "expression_kind": "name",
                "op": "Truthy",
                "value": True,
                "source": source,
            }],
            **location,
        }

    # Constant: if True:
    if isinstance(node, ast.Constant):
        value = node.value
        return {
            "type": "constant",
            "source": source,
            "value": value,
            "value_type": type(value).__name__,
            "constraints": [],
            **location,
        }

    # Call truthiness: if len(items):, if name.strip():, if validate(x):
    if isinstance(node, ast.Call):
        expr = _resolve_expression(node)
        return {
            "type": "call",
            "source": source,
            "func_name": expr.get("func_name"),
            "arg_count": expr.get("arg_count", 0),
            "expression": expr,
            "constraints": [{
                "arg": expr.get("comparison_arg"),
                "base_arg": expr.get("base"),
                "path": expr.get("path"),
                "transform": expr.get("transform"),
                "expression_kind": expr.get("kind"),
                "op": "Truthy",
                "value": True,
                "source": source,
            }] if expr.get("comparison_arg") else [],
            **location,
        }

    # Attribute truthiness: if user.active:
    if isinstance(node, ast.Attribute):
        expr = _resolve_expression(node)
        return {
            "type": "attribute",
            "source": source,
            "attr": node.attr,
            "expression": expr,
            "constraints": [{
                "arg": expr.get("comparison_arg"),
                "base_arg": expr.get("base"),
                "path": expr.get("path"),
                "transform": expr.get("transform"),
                "expression_kind": expr.get("kind"),
                "op": "Truthy",
                "value": True,
                "source": source,
            }],
            **location,
        }

    return {
        "type": "unknown",
        "source": source,
        "node_type": type(node).__name__,
        "constraints": [],
        **location,
    }


# ============================================================================
# RAISE INSPECTION
# ============================================================================

class _BlockRaiseInspector(ast.NodeVisitor):
    """Inspect a statement block while refusing to enter nested defs/classes."""

    def __init__(self) -> None:
        self.has_raise = False
        self.exception_types: List[str] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Raise(self, node: ast.Raise) -> None:
        self.has_raise = True
        exc_name = _raise_type_name(node)
        if exc_name:
            self.exception_types.append(exc_name)


def _inspect_block_for_raise(statements: List[ast.stmt]) -> Dict[str, Any]:
    inspector = _BlockRaiseInspector()
    for stmt in statements:
        inspector.visit(stmt)
    unique = list(dict.fromkeys(inspector.exception_types))
    return {
        "has_raise": inspector.has_raise,
        "exception_types": unique,
    }


class _BlockReturnInspector(ast.NodeVisitor):
    """Inspect a statement block for return nodes while refusing to enter nested defs/classes or control flows."""

    def __init__(self, source_text: Optional[str] = None) -> None:
        self.source_text = source_text
        self.returns: List[Dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_If(self, node: ast.If) -> None:
        return

    def visit_For(self, node: ast.For) -> None:
        return

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        return

    def visit_While(self, node: ast.While) -> None:
        return

    def visit_Try(self, node: ast.Try) -> None:
        return

    def visit_Match(self, node: ast.Match) -> None:
        return

    def visit_With(self, node: ast.With) -> None:
        return

    def visit_Return(self, node: ast.Return) -> None:
        expr = _resolve_expression(node.value) if node.value is not None else None
        self.returns.append({
            "source": _safe_unparse(node.value) if node.value is not None else "None",
            "expression": expr,
            **_node_location(node, self.source_text),
        })
        self.generic_visit(node)


def _inspect_block_for_returns(statements: List[ast.stmt], source_text: Optional[str] = None) -> List[Dict[str, Any]]:
    inspector = _BlockReturnInspector(source_text)
    for stmt in statements:
        inspector.visit(stmt)
    return inspector.returns


def _has_nested_control_flow(statements: List[ast.stmt]) -> bool:
    """Check if a statement list contains nested control flow nodes."""
    class ControlFlowVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.has_control_flow = False

        def visit_FunctionDef(self, node) -> None:
            return

        def visit_AsyncFunctionDef(self, node) -> None:
            return

        def visit_ClassDef(self, node) -> None:
            return

        def visit_If(self, node: ast.If) -> None:
            self.has_control_flow = True

        def visit_For(self, node: ast.For) -> None:
            self.has_control_flow = True

        def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
            self.has_control_flow = True

        def visit_While(self, node: ast.While) -> None:
            self.has_control_flow = True

        def visit_Try(self, node: ast.Try) -> None:
            self.has_control_flow = True

        def visit_Match(self, node: ast.Match) -> None:
            self.has_control_flow = True

        def visit_With(self, node: ast.With) -> None:
            self.has_control_flow = True

    visitor = ControlFlowVisitor()
    for stmt in statements:
        visitor.visit(stmt)
        if visitor.has_control_flow:
            return True
    return False



# ============================================================================
# BODY VISITOR
# ============================================================================

class BodyVisitor(ast.NodeVisitor):
    def __init__(self, source_text: Optional[str] = None) -> None:
        self.source_text = source_text
        self.branches: List[Dict[str, Any]] = []
        self.raises = False
        self.unconditional_raise = False
        self.exception_types: List[str] = []
        self.has_try_except = False
        self.try_except_blocks: List[Dict[str, Any]] = []
        self.loops: List[Dict[str, Any]] = []
        self.returns: List[Dict[str, Any]] = []
        self._conditional_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Return(self, node: ast.Return) -> None:
        expr = _resolve_expression(node.value) if node.value is not None else None
        self.returns.append({
            "source": _safe_unparse(node.value) if node.value is not None else "None",
            "expression": expr,
            "depth": self._conditional_depth,
            **_node_location(node, self.source_text),
        })
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.raises = True
        if self._conditional_depth == 0:
            self.unconditional_raise = True
        exc_name = _raise_type_name(node)
        if exc_name:
            self.exception_types.append(exc_name)

    def visit_If(self, node: ast.If) -> None:
        self._capture_if_branch(node)
        self._conditional_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
            for stmt in node.orelse:
                self.visit(stmt)
        finally:
            self._conditional_depth -= 1

    def visit_While(self, node: ast.While) -> None:
        condition_meta = _parse_condition(node.test, self.source_text)
        self.loops.append({
            "type": "while",
            "source": condition_meta.get("source"),
            "condition": condition_meta,
            "constraints": condition_meta.get("constraints", []),
            **_node_location(node, self.source_text),
        })
        self._conditional_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._conditional_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self.loops.append({
            "type": "for",
            "target": _safe_unparse(node.target),
            "iter": _safe_unparse(node.iter),
            **_node_location(node, self.source_text),
        })
        self._conditional_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._conditional_depth -= 1

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.loops.append({
            "type": "async_for",
            "target": _safe_unparse(node.target),
            "iter": _safe_unparse(node.iter),
            **_node_location(node, self.source_text),
        })
        self._conditional_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._conditional_depth -= 1

    def visit_Match(self, node: ast.Match) -> None:
        self._conditional_depth += 1
        try:
            for case in node.cases:
                for stmt in case.body:
                    self.visit(stmt)
        finally:
            self._conditional_depth -= 1

    def visit_Try(self, node: ast.Try) -> None:
        self.has_try_except = True
        except_types: List[str] = []
        for handler in node.handlers:
            if handler.type is None:
                except_types.append("Exception")
            elif isinstance(handler.type, ast.Name):
                except_types.append(handler.type.id)
            elif isinstance(handler.type, ast.Attribute):
                except_types.append(_safe_unparse(handler.type))
            elif isinstance(handler.type, ast.Tuple):
                for exc in handler.type.elts:
                    if isinstance(exc, ast.Name):
                        except_types.append(exc.id)
                    elif isinstance(exc, ast.Attribute):
                        except_types.append(_safe_unparse(exc))

        unique_except_types = list(dict.fromkeys(except_types))
        self.try_except_blocks.append({
            "except_types": unique_except_types,
            "handler_count": len(node.handlers),
            **_node_location(node, self.source_text),
        })

        self._conditional_depth += 1
        try:
            for stmt in node.body:
                self.visit(stmt)
            for handler in node.handlers:
                for stmt in handler.body:
                    self.visit(stmt)
            for stmt in node.orelse:
                self.visit(stmt)
            for stmt in node.finalbody:
                self.visit(stmt)
        finally:
            self._conditional_depth -= 1

    def _capture_if_branch(self, node: ast.If) -> None:
        condition_meta = _parse_condition(node.test, self.source_text)
        body_meta = _inspect_block_for_raise(node.body)
        else_meta = _inspect_block_for_raise(node.orelse)

        body_returns = _inspect_block_for_returns(node.body, self.source_text)
        has_nested_control_flow = _has_nested_control_flow(node.body)
        
        has_elif = len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If)
        if has_elif:
            else_returns = []
            has_else_nested_control_flow = False
        else:
            else_returns = _inspect_block_for_returns(node.orelse, self.source_text)
            has_else_nested_control_flow = _has_nested_control_flow(node.orelse)

        raise_when = None
        exception_type = None
        if body_meta["has_raise"] and not else_meta["has_raise"]:
            raise_when = "truthy"
            exception_type = (
                body_meta["exception_types"][0]
                if len(body_meta["exception_types"]) == 1
                else None
            )
        elif else_meta["has_raise"] and not body_meta["has_raise"]:
            raise_when = "falsy"
            exception_type = (
                else_meta["exception_types"][0]
                if len(else_meta["exception_types"]) == 1
                else None
            )

        branch_info: Dict[str, Any] = {
            "source": condition_meta.get("source", ""),
            "raise_when": raise_when,
            "exception_type": exception_type,
            "condition": condition_meta,
            "constraints": condition_meta.get("constraints", []),
            "has_body_raise": body_meta["has_raise"],
            "has_else_raise": else_meta["has_raise"],
            "body_exceptions": body_meta["exception_types"],
            "else_exceptions": else_meta["exception_types"],
            "body_returns": body_returns,
            "else_returns": else_returns,
            "has_elif": has_elif,
            "has_direct_else": bool(node.orelse) and not has_elif,
            "has_nested_control_flow": has_nested_control_flow,
            "has_else_nested_control_flow": has_else_nested_control_flow,
            **_node_location(node, self.source_text),
        }

        # Backward-compatible fields consumed by heuristics.py/code_generator.py.
        constraints = condition_meta.get("constraints", [])
        first_constraint = constraints[0] if constraints else None
        if first_constraint and first_constraint.get("arg") is not None:
            branch_info["arg"] = first_constraint.get("arg")
            branch_info["op"] = first_constraint.get("op")
            branch_info["value"] = first_constraint.get("value")
            branch_info["base_arg"] = first_constraint.get("base_arg")
            branch_info["arg_path"] = first_constraint.get("path")
            branch_info["transform"] = first_constraint.get("transform")
        elif condition_meta.get("type") == "compare" and "op" in condition_meta:
            branch_info["arg"] = condition_meta.get("left")
            branch_info["op"] = condition_meta.get("op")
            if not condition_meta.get("left_is_symbol") and condition_meta.get("right_is_symbol"):
                branch_info["arg"] = condition_meta.get("right")
            value = condition_meta.get("right")
            if condition_meta.get("right_is_symbol") and not condition_meta.get("left_is_symbol"):
                value = condition_meta.get("left")
            branch_info["value"] = value

        self.branches.append(branch_info)


# ============================================================================
# FUNCTION / CLASS EXTRACTOR
# ============================================================================

class FuncExtractor(ast.NodeVisitor):
    def __init__(self, source_text: Optional[str] = None) -> None:
        self.source_text = source_text
        self.functions: List[Dict[str, Any]] = []
        self.classes: List[Dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        class_info: Dict[str, Any] = {
            "class_name": node.name,
            "bases": [_safe_unparse(base) for base in node.bases],
            "decorators": [_safe_unparse(dec) for dec in node.decorator_list],
            "methods": [],
            "constructor": None,
            **_node_location(node, self.source_text),
        }

        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name == "__init__":
                    class_info["constructor"] = self._build_callable_metadata(
                        item,
                        is_async=isinstance(item, ast.AsyncFunctionDef),
                        is_method=True,
                        include_dunder=True,
                    )
                    continue

                method_info = self._process_method(
                    item,
                    is_async=isinstance(item, ast.AsyncFunctionDef),
                )
                if method_info:
                    class_info["methods"].append(method_info)

        self.classes.append(class_info)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._process_function(node, is_async=True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._process_function(node, is_async=False)

    def _process_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
    ) -> None:
        if node.name.startswith("__"):
            return
        if hasattr(node, "parent") and isinstance(node.parent, ast.ClassDef):
            return
        self.functions.append(self._build_callable_metadata(node, is_async))

    def _process_method(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
    ) -> Dict[str, Any]:
        if node.name.startswith("__"):
            return {}
        return self._build_callable_metadata(node, is_async, is_method=True)

    def _build_callable_metadata(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        is_async: bool,
        is_method: bool = False,
        include_dunder: bool = False,
    ) -> Dict[str, Any]:
        unsupported_reasons: List[str] = []
        if node.args.vararg or node.args.kwarg:
            unsupported_reasons.append("varargs/kwargs are currently not supported")
        if any(isinstance(stmt, (ast.Yield, ast.YieldFrom)) for stmt in ast.walk(node)):
            unsupported_reasons.append("generator functions are partially supported")

        args_meta: List[Dict[str, Any]] = []
        positional = list(node.args.posonlyargs) + list(node.args.args)
        defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)

        for idx, arg in enumerate(positional):
            if is_method and arg.arg in ("self", "cls"):
                continue
            args_meta.append({
                "name": arg.arg,
                "annotation": _annotation_to_text(arg.annotation),
                "kind": "positional_only" if idx < len(node.args.posonlyargs) else "positional_or_keyword",
                "has_default": defaults[idx] is not None,
                "default": _literal_value(defaults[idx]) if defaults[idx] is not None else None,
                **_node_location(arg, self.source_text),
            })

        for kw_arg, kw_default in zip(node.args.kwonlyargs, node.args.kw_defaults):
            args_meta.append({
                "name": kw_arg.arg,
                "annotation": _annotation_to_text(kw_arg.annotation),
                "kind": "keyword_only",
                "has_default": kw_default is not None,
                "default": _literal_value(kw_default) if kw_default is not None else None,
                **_node_location(kw_arg, self.source_text),
            })

        body_visitor = BodyVisitor(self.source_text)
        for stmt in node.body:
            body_visitor.visit(stmt)

        support_level = "full" if not unsupported_reasons else "partial"
        legacy_unsupported_reason = unsupported_reasons[0] if unsupported_reasons else None

        return {
            "name": node.name,
            "args": args_meta,
            "return_type": _annotation_to_text(node.returns),
            "branches": body_visitor.branches,
            "raises": body_visitor.raises,
            "unconditional_raise": body_visitor.unconditional_raise,
            "exception_types": list(dict.fromkeys(body_visitor.exception_types)),
            "has_try_except": body_visitor.has_try_except,
            "try_except_blocks": body_visitor.try_except_blocks,
            # Legacy key retained for existing code.
            "unsupported_reason": legacy_unsupported_reason,
            # New richer support metadata.
            "unsupported_reasons": unsupported_reasons,
            "support_level": support_level,
            "docstring": ast.get_docstring(node),
            "is_async": is_async,
            "decorators": _decorator_names(node),
            "method_kind": _method_kind(node, is_method),
            "loops": body_visitor.loops,
            "returns": body_visitor.returns,
            **_node_location(node, self.source_text),
        }


# ============================================================================
# ATTACH PARENT REFERENCES
# ============================================================================

class ParentSetter(ast.NodeVisitor):
    """Attach parent references to every AST node."""

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            child.parent = node
        super().generic_visit(node)


# ============================================================================
# ENTRY POINT
# ============================================================================

def parse_file(file_path: str) -> Dict[str, Any]:
    source = None
    encodings = ["utf-8", "utf-8-sig", "cp1252"]
    last_error = None
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                source = f.read()
            break
        except UnicodeDecodeError as e:
            last_error = e

    if source is None:
        raise last_error or ValueError(f"Could not decode file {file_path}")

    tree = ast.parse(source, filename=file_path)
    ParentSetter().visit(tree)

    extractor = FuncExtractor(source)
    extractor.visit(tree)

    return {
        "functions": extractor.functions,
        "classes": extractor.classes,
    }

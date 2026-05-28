"""Tkinter GUI for the Python Auto Test Generator.

The GUI is an interface layer. It should coordinate the workflow, not
reimplement parser/heuristic/generator logic:
    select source -> analyze AST -> generate pytest -> run pytest/report.

This version is aligned with the improved core modules:
- ast_parser.py exposes richer metadata: constructor, method_kind, constraints,
  transforms, loops, returns, source locations.
- code_generator.py generates plain pytest by default and supports optional
  Allure via use_allure=True.
- coverage_config.py centralizes pytest-cov argument generation and coverage XML
  summary parsing.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Allow running this file directly: python core_engine/gui.py
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if __package__ is None or __package__ == "":
    __package__ = "core_engine"

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import tkinter.scrolledtext as scrolledtext

from .ast_parser import parse_file
from .code_generator import generate_test_file
from .coverage_config import (
    cleanup_coverage_artifacts,
    ensure_coverage_config,
    get_coverage_cli_args,
    get_project_root,
    read_coverage_summary_from_xml,
)


# ============================================================================
# SMALL UTILITY HELPERS
# ============================================================================


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _format_bool(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _format_annotation(arg: Dict[str, Any]) -> str:
    name = arg.get("name", "<arg>")
    annotation = arg.get("annotation") or "Any"
    suffix = ""
    if arg.get("has_default"):
        suffix = f" = {arg.get('default')!r}"
    return f"{name}: {annotation}{suffix}"


def _callable_signature(func: Dict[str, Any]) -> str:
    args = ", ".join(_format_annotation(arg) for arg in func.get("args", []))
    prefix = "async " if func.get("is_async") else ""
    return f"{prefix}{func.get('name', '<callable>')}({args}) -> {func.get('return_type', 'Any')}"


def _append_lines(lines: List[str], text: Iterable[str]) -> None:
    lines.extend(text)


# ============================================================================
# GUI APPLICATION
# ============================================================================


class AutoTestGenGUI:
    """Tkinter desktop GUI for one-file test generation and report running."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto Test Generator")
        self.root.geometry("1220x760")
        self.root.minsize(980, 620)

        self.current_file_path: Optional[Path] = None
        self.current_parse_result: Optional[Dict[str, Any]] = None
        self.generated_test_path: Optional[Path] = None
        self.project_root: Path = Path(parent_dir).resolve()
        self.output_dir: Path = self.project_root / "tests_output"

        # Default report options. Allure/pytest-html are optional plugins, so do
        # not enable them by default. Coverage is useful for the thesis/demo flow.
        self.var_cov = tk.BooleanVar(value=True)
        self.var_html = tk.BooleanVar(value=False)
        self.var_allure = tk.BooleanVar(value=False)
        self.var_clean_output = tk.BooleanVar(value=True)

        self._is_busy = False
        self._setup_ui()
        self._set_status("Sẵn sàng. Hãy chọn một file Python.")

    # ------------------------------------------------------------------
    # UI SETUP
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        top_frame = tk.Frame(self.root, pady=8, padx=10)
        top_frame.pack(side="top", fill="x")

        self.btn_select_file = tk.Button(
            top_frame,
            text="Chọn File Python",
            command=self.select_file,
        )
        self.btn_select_file.pack(side="left")

        self.lbl_file_path = tk.Label(top_frame, text="Chưa chọn file nào", fg="gray", anchor="w")
        self.lbl_file_path.pack(side="left", padx=10, fill="x", expand=True)

        self.lbl_project_root = tk.Label(top_frame, text="Project root: -", fg="#666", anchor="e")
        self.lbl_project_root.pack(side="right")

        main_pane = ttk.PanedWindow(self.root, orient="horizontal")
        main_pane.pack(side="top", fill="both", expand=True, padx=10, pady=5)

        left_frame = tk.Frame(main_pane)
        right_frame = tk.Frame(main_pane)
        main_pane.add(left_frame, weight=1)
        main_pane.add(right_frame, weight=1)

        lbl_left = tk.Label(
            left_frame,
            text="AST Metadata / Constraints",
            font=("Arial", 10, "bold"),
        )
        lbl_left.pack(anchor="w")

        self.txt_ast_info = scrolledtext.ScrolledText(left_frame, wrap="word", bg="#f4f4f4")
        self.txt_ast_info.pack(fill="both", expand=True)
        self.txt_ast_info.config(state="disabled")

        right_notebook = ttk.Notebook(right_frame)
        right_notebook.pack(fill="both", expand=True)

        preview_tab = tk.Frame(right_notebook)
        log_tab = tk.Frame(right_notebook)
        right_notebook.add(preview_tab, text="Generated Test Preview")
        right_notebook.add(log_tab, text="Run Log")

        self.txt_preview = scrolledtext.ScrolledText(preview_tab, wrap="none")
        self.txt_preview.pack(fill="both", expand=True)

        self.txt_run_log = scrolledtext.ScrolledText(log_tab, wrap="word", bg="#111", fg="#ddd")
        self.txt_run_log.pack(fill="both", expand=True)

        bottom_frame = tk.Frame(self.root, pady=8, padx=10)
        bottom_frame.pack(side="bottom", fill="x")

        self.btn_preview = tk.Button(
            bottom_frame,
            text="Sinh Test Preview",
            command=self.preview_test,
            state="disabled",
            bg="#e0e0e0",
        )
        self.btn_preview.pack(side="left", padx=(0, 12))

        self.btn_run = tk.Button(
            bottom_frame,
            text="Chạy Pytest",
            command=self.run_test,
            state="disabled",
            bg="#d4edda",
        )
        self.btn_run.pack(side="left", padx=(0, 12))

        self.chk_html = tk.Checkbutton(bottom_frame, text="Pytest HTML", variable=self.var_html)
        self.chk_html.pack(side="left", padx=(0, 10))

        self.chk_cov = tk.Checkbutton(bottom_frame, text="Coverage", variable=self.var_cov)
        self.chk_cov.pack(side="left", padx=(0, 10))

        self.chk_allure = tk.Checkbutton(bottom_frame, text="Allure", variable=self.var_allure)
        self.chk_allure.pack(side="left", padx=(0, 10))

        self.chk_clean = tk.Checkbutton(bottom_frame, text="Clean output", variable=self.var_clean_output)
        self.chk_clean.pack(side="left", padx=(0, 10))

        self.btn_open_allure = tk.Button(
            bottom_frame,
            text="Mở Allure Report",
            command=self.open_allure_report,
            bg="#cce5ff",
            state="disabled",
        )
        self.btn_open_allure.pack(side="right", padx=(10, 0))

        self.lbl_status = tk.Label(self.root, text="", anchor="w", fg="#333", bd=1, relief="sunken")
        self.lbl_status.pack(side="bottom", fill="x")

    # ------------------------------------------------------------------
    # UI STATE & THREAD-SAFE HELPERS
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self.lbl_status.config(text=text)

    def _append_log(self, text: str) -> None:
        self.txt_run_log.config(state="normal")
        self.txt_run_log.insert(tk.END, text)
        if not text.endswith("\n"):
            self.txt_run_log.insert(tk.END, "\n")
        self.txt_run_log.see(tk.END)
        self.txt_run_log.config(state="normal")

    def _clear_log(self) -> None:
        self.txt_run_log.delete(1.0, tk.END)

    def _run_on_ui(self, callback, *args, **kwargs) -> None:
        self.root.after(0, lambda: callback(*args, **kwargs))

    def _set_busy(self, busy: bool, status: Optional[str] = None) -> None:
        self._is_busy = busy
        state = "disabled" if busy else "normal"
        self.btn_select_file.config(state=state)
        self.chk_html.config(state=state)
        self.chk_cov.config(state=state)
        self.chk_allure.config(state=state)
        self.chk_clean.config(state=state)

        if busy:
            self.btn_preview.config(state="disabled")
            self.btn_run.config(state="disabled")
            self.btn_open_allure.config(state="disabled")
        else:
            self.btn_preview.config(state="normal" if self.current_file_path else "disabled")
            self.btn_run.config(state="normal" if self.generated_test_path else "disabled")
            self.btn_open_allure.config(state="normal" if self.var_allure.get() else "disabled")

        if status:
            self._set_status(status)

    # ------------------------------------------------------------------
    # SOURCE / PROJECT PATH HELPERS
    # ------------------------------------------------------------------

    def _refresh_project_context(self, file_path: Path) -> None:
        self.project_root = get_project_root(file_path)
        self.output_dir = self.project_root / "tests_output"
        self.lbl_project_root.config(text=f"Project root: {self.project_root}")

    def _derive_module_prefix(self, file_path: Path) -> Optional[str]:
        """Return package prefix only when the file is inside a clear package."""
        try:
            rel = file_path.resolve().relative_to(self.project_root.resolve())
        except ValueError:
            return None

        parts = list(rel.with_suffix("").parts)
        if len(parts) <= 1:
            return None

        package_dirs = parts[:-1]
        current = self.project_root
        for part in package_dirs:
            current = current / part
            if not (current / "__init__.py").exists():
                return None

        return ".".join(package_dirs) if package_dirs else None

    def _coverage_target_for_current_file(self) -> str:
        """Return a pytest-cov target that matches the generated import.

        pytest-cov treats --cov values primarily as importable packages/modules
        or source directories. Passing a concrete .py file path can produce
        "module was never imported" warnings in some environments. Since the
        generated test imports the selected file as module_name or
        package.module_name, use the same import target here.
        """
        if not self.current_file_path:
            return ""
        module_prefix = self._derive_module_prefix(self.current_file_path)
        module_name = self.current_file_path.stem
        return f"{module_prefix}.{module_name}" if module_prefix else module_name

    def _clean_output_artifacts(self) -> None:
        if not self.var_clean_output.get():
            self.output_dir.mkdir(parents=True, exist_ok=True)
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        for pattern in ("test_*.py", "*.pyc", "report.html"):
            for item in self.output_dir.glob(pattern):
                if item.is_file() or item.is_symlink():
                    item.unlink()
        for folder_name in ("__pycache__", ".pytest_cache"):
            folder = self.output_dir / folder_name
            if folder.exists() and folder.is_dir():
                shutil.rmtree(folder)

    # ------------------------------------------------------------------
    # AST DISPLAY
    # ------------------------------------------------------------------

    def _format_branch(self, branch: Dict[str, Any], indent: str = "      ") -> List[str]:
        lines: List[str] = []
        source = branch.get("source") or branch.get("source_segment") or "<condition>"
        line = branch.get("lineno")
        line_text = f"line {line}: " if line else ""
        lines.append(f"{indent}- {line_text}{source}")

        constraints = branch.get("constraints") or []
        for constraint in constraints:
            arg = constraint.get("arg") or constraint.get("base_arg") or "<unknown>"
            transform = constraint.get("transform")
            op = constraint.get("op")
            value = constraint.get("value")
            transform_text = f" via {transform}" if transform else ""
            lines.append(f"{indent}    constraint: {arg}{transform_text} {op} {value!r}")

        if branch.get("raise_when"):
            lines.append(
                f"{indent}    raise_when: {branch.get('raise_when')} / "
                f"exception: {branch.get('exception_type') or '-'}"
            )
        return lines

    def _format_callable(self, func: Dict[str, Any], indent: str = "  ") -> List[str]:
        lines: List[str] = [f"{indent}- {_callable_signature(func)}"]

        method_kind = func.get("method_kind")
        if method_kind and method_kind != "function":
            lines.append(f"{indent}  method_kind: {method_kind}")

        unsupported = func.get("unsupported_reasons") or []
        if func.get("unsupported_reason"):
            unsupported.append(func.get("unsupported_reason"))
        if unsupported:
            lines.append(f"{indent}  unsupported: {', '.join(str(item) for item in unsupported)}")

        branches = func.get("branches", [])
        lines.append(f"{indent}  branches: {len(branches)}")
        for branch in branches:
            lines.extend(self._format_branch(branch, indent=f"{indent}    "))

        loops = func.get("loops", [])
        if loops:
            lines.append(f"{indent}  loops: {len(loops)}")
            for loop in loops:
                lines.append(
                    f"{indent}    - {loop.get('type')} line {loop.get('lineno', '-')}: "
                    f"{loop.get('source') or loop.get('iter') or loop.get('condition', '')}"
                )

        returns = func.get("returns", [])
        if returns:
            lines.append(f"{indent}  returns: {len(returns)}")
            for ret in returns[:5]:
                line = ret.get("lineno")
                line_text = f"line {line}: " if line else ""
                lines.append(f"{indent}    - {line_text}{ret.get('source', '<return>')}")

        exc_types = func.get("exception_types") or []
        lines.append(
            f"{indent}  raises: {_format_bool(func.get('raises'))}; "
            f"unconditional_raise: {_format_bool(func.get('unconditional_raise'))}; "
            f"exceptions: {', '.join(exc_types) if exc_types else '-'}"
        )
        return lines

    def _render_ast_info(self, parsed: Dict[str, Any]) -> str:
        lines: List[str] = []
        functions = parsed.get("functions", [])
        classes = parsed.get("classes", [])

        lines.append(f"Functions: {len(functions)}")
        for func in functions:
            lines.extend(self._format_callable(func, indent="  "))
            lines.append("")

        lines.append(f"Classes: {len(classes)}")
        for cls in classes:
            lines.append(f"  Class {cls.get('class_name', '<class>')}")
            constructor = cls.get("constructor")
            if constructor:
                lines.append("    constructor:")
                lines.extend(self._format_callable(constructor, indent="      "))
            else:
                lines.append("    constructor: none")

            methods = cls.get("methods", [])
            lines.append(f"    methods: {len(methods)}")
            for method in methods:
                lines.extend(self._format_callable(method, indent="      "))
                lines.append("")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------
    # USER ACTIONS
    # ------------------------------------------------------------------

    def select_file(self) -> None:
        if self._is_busy:
            return

        file_path = filedialog.askopenfilename(
            title="Chọn File Python",
            filetypes=[("Python files", "*.py"), ("All files", "*.*")],
        )
        if not file_path:
            return

        selected = Path(file_path).expanduser().resolve()
        if selected.suffix.lower() != ".py":
            messagebox.showerror("Lỗi", "Vui lòng chọn file Python (.py).")
            return

        try:
            parsed = parse_file(str(selected))
        except SyntaxError as exc:
            self.current_file_path = selected
            self.current_parse_result = None
            self.generated_test_path = None
            self.lbl_file_path.config(text=str(selected), fg="black")
            self.txt_ast_info.config(state="normal")
            self.txt_ast_info.delete(1.0, tk.END)
            self.txt_ast_info.insert(tk.END, f"LỖI CÚ PHÁP:\n{exc}\n")
            self.txt_ast_info.config(state="disabled")
            self.btn_preview.config(state="disabled")
            self.btn_run.config(state="disabled")
            self._set_status("File có lỗi cú pháp, chưa thể sinh test.")
            return
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không thể đọc/phân tích file:\n{type(exc).__name__}: {exc}")
            return

        self.current_file_path = selected
        self.current_parse_result = parsed
        self.generated_test_path = None
        self._refresh_project_context(selected)

        self.lbl_file_path.config(text=str(selected), fg="black")
        self.txt_ast_info.config(state="normal")
        self.txt_ast_info.delete(1.0, tk.END)
        self.txt_ast_info.insert(tk.END, self._render_ast_info(parsed))
        self.txt_ast_info.config(state="disabled")

        self.txt_preview.delete(1.0, tk.END)
        self._clear_log()
        self.btn_preview.config(state="normal")
        self.btn_run.config(state="disabled")
        self.btn_open_allure.config(state="normal" if self.var_allure.get() else "disabled")
        self._set_status("Đã phân tích AST. Có thể sinh test preview.")

    def preview_test(self) -> None:
        if self._is_busy or not self.current_file_path:
            return

        if self.var_allure.get() and not _has_module("allure"):
            messagebox.showerror(
                "Thiếu dependency",
                "Allure đang bật nhưng chưa cài allure-pytest.\n"
                "Hãy cài allure-pytest hoặc tắt checkbox Allure.",
            )
            return

        try:
            self._set_busy(True, "Đang sinh test...")
            self._clean_output_artifacts()
            module_prefix = self._derive_module_prefix(self.current_file_path)
            result = generate_test_file(
                source_file=str(self.current_file_path),
                output_dir=str(self.output_dir),
                module_path=module_prefix,
                dry_run=False,
                use_allure=self.var_allure.get(),
            )

            status = result.get("status")
            if status == "syntax_error":
                messagebox.showerror("Lỗi cú pháp", result.get("message", "Unknown syntax error"))
                return
            if status == "no_supported_functions":
                messagebox.showwarning("Không có callable phù hợp", "Không tìm thấy hàm/phương thức được hỗ trợ để sinh test.")
                return
            if status != "generated":
                messagebox.showerror("Lỗi", f"Không sinh được test. Status: {status}")
                return

            output_file = result.get("output_file")
            if not output_file:
                messagebox.showerror("Lỗi", "Generator không trả về output_file.")
                return

            self.generated_test_path = Path(output_file).resolve()
            if not self.generated_test_path.exists():
                messagebox.showerror("Lỗi", f"Không tìm thấy file test đã sinh:\n{self.generated_test_path}")
                return

            self.txt_preview.delete(1.0, tk.END)
            self.txt_preview.insert(tk.END, _safe_read_text(self.generated_test_path))
            self.btn_run.config(state="normal")
            self._set_status(f"Đã sinh {result.get('generated_tests', 0)} test case: {self.generated_test_path}")
            messagebox.showinfo("Hoàn thành", f"Đã sinh {result.get('generated_tests', 0)} test case.")
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Có lỗi khi sinh test:\n{type(exc).__name__}: {exc}")
        finally:
            self._set_busy(False)

    def _validate_run_dependencies(self) -> bool:
        if self.var_html.get() and not _has_module("pytest_html"):
            messagebox.showerror(
                "Thiếu dependency",
                "Pytest HTML đang bật nhưng chưa cài pytest-html.\n"
                "Hãy cài pytest-html hoặc tắt checkbox Pytest HTML.",
            )
            return False
        if self.var_cov.get() and not _has_module("pytest_cov"):
            messagebox.showerror(
                "Thiếu dependency",
                "Coverage đang bật nhưng chưa cài pytest-cov.\n"
                "Hãy cài pytest-cov hoặc tắt checkbox Coverage.",
            )
            return False
        if self.var_allure.get() and not _has_module("allure"):
            messagebox.showerror(
                "Thiếu dependency",
                "Allure đang bật nhưng chưa cài allure-pytest.\n"
                "Hãy cài allure-pytest hoặc tắt checkbox Allure.",
            )
            return False
        return True

    def run_test(self) -> None:
        if self._is_busy:
            return
        if not self.generated_test_path or not self.generated_test_path.exists():
            messagebox.showerror("Lỗi", "Vui lòng sinh test trước khi chạy.")
            return
        if not self._validate_run_dependencies():
            return

        self._set_busy(True, "Đang chạy pytest...")
        self._clear_log()
        self._append_log(f"Generated test: {self.generated_test_path}")
        self._append_log(f"Project root: {self.project_root}")

        thread = threading.Thread(target=self._run_pytest_worker, daemon=True)
        thread.start()

    def _run_pytest_worker(self) -> None:
        assert self.generated_test_path is not None
        assert self.current_file_path is not None

        html_report_path = self.output_dir / "report.html"
        allure_dir = self.project_root / "allure-results"
        coverage_xml_path = self.project_root / "coverage.xml"
        coverage_html_index = self.project_root / "htmlcov" / "index.html"

        cmd: List[str] = [sys.executable, "-m", "pytest", str(self.generated_test_path), "-v"]

        if self.var_html.get():
            cmd.extend([f"--html={html_report_path}", "--self-contained-html"])

        if self.var_cov.get():
            try:
                cleanup_coverage_artifacts(
                    self.project_root,
                    include_html=True,
                    include_xml=True,
                    include_json=True,
                    include_pytest_cache=False,
                    raise_errors=False,
                )
                coveragerc_path = ensure_coverage_config(self.project_root)
                coverage_target = self._coverage_target_for_current_file()
                cmd.extend(
                    get_coverage_cli_args(
                        coverage_target=coverage_target,
                        include_branch=True,
                        include_term=True,
                        include_html=True,
                        include_xml=True,
                        include_json=False,
                        project_root=self.project_root,
                        config_path=coveragerc_path,
                    )
                )
            except Exception as exc:
                self._run_on_ui(self._handle_run_error, 2, "", f"Coverage configuration error: {exc}")
                return

        if self.var_allure.get():
            cmd.append(f"--alluredir={allure_dir}")

        env = os.environ.copy()
        current_pp = env.get("PYTHONPATH", "")
        py_paths = [str(self.project_root), str(self.current_file_path.parent)]
        if current_pp:
            py_paths.append(current_pp)
        env["PYTHONPATH"] = os.pathsep.join(py_paths)

        self._run_on_ui(self._append_log, "\nCommand:\n" + " ".join(cmd) + "\n")

        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(self.project_root),
                env=env,
                check=False,
            )
        except Exception as exc:
            self._run_on_ui(self._handle_run_error, 2, "", f"{type(exc).__name__}: {exc}")
            return

        if completed.returncode == 0:
            coverage_summary = read_coverage_summary_from_xml(coverage_xml_path) if self.var_cov.get() else {}
            self._run_on_ui(
                self._handle_run_success,
                completed.stdout,
                completed.stderr,
                html_report_path,
                coverage_html_index,
                allure_dir,
                coverage_summary,
            )
        else:
            self._run_on_ui(self._handle_run_error, completed.returncode, completed.stdout, completed.stderr)

    def _handle_run_success(
        self,
        stdout: str,
        stderr: str,
        html_report_path: Path,
        coverage_html_index: Path,
        allure_dir: Path,
        coverage_summary: Dict[str, Any],
    ) -> None:
        self._append_log("\n--- STDOUT ---\n" + (stdout or "(no stdout)"))
        if stderr:
            self._append_log("\n--- STDERR ---\n" + stderr)

        msg_lines = ["Pytest chạy thành công."]
        if self.var_html.get() and html_report_path.exists():
            msg_lines.append(f"HTML report: {html_report_path}")
            webbrowser.open("file://" + str(html_report_path.resolve()))
        if self.var_cov.get():
            line_percent = coverage_summary.get("line_percent")
            branch_percent = coverage_summary.get("branch_percent")
            if line_percent is not None:
                msg_lines.append(f"Line coverage: {line_percent}%")
            if branch_percent is not None:
                msg_lines.append(f"Branch coverage: {branch_percent}%")
            if coverage_html_index.exists():
                msg_lines.append(f"Coverage HTML: {coverage_html_index}")
        if self.var_allure.get():
            msg_lines.append(f"Allure results: {allure_dir}")
            self.btn_open_allure.config(state="normal")

        self._set_busy(False, "Pytest pass.")
        messagebox.showinfo("Hoàn thành", "\n".join(msg_lines))

    def _handle_run_error(self, returncode: int, stdout: str, stderr: str) -> None:
        self._append_log("\n--- STDOUT ---\n" + (stdout or "(no stdout)"))
        self._append_log("\n--- STDERR ---\n" + (stderr or "(no stderr)"))
        self._set_busy(False, f"Pytest failed. Exit code: {returncode}")

        short_stdout = (stdout or "").strip()
        short_stderr = (stderr or "").strip()
        detail = short_stderr or short_stdout or "Không có output chi tiết. Xem tab Run Log."
        if len(detail) > 1200:
            detail = detail[:1200] + "\n...\n(Xem đầy đủ trong tab Run Log)"
        messagebox.showerror("Pytest failed", f"Exit code: {returncode}\n\n{detail}")

    def open_allure_report(self) -> None:
        allure_dir = self.project_root / "allure-results"
        
        if not allure_dir.exists():
            messagebox.showwarning(
                "Chưa có Allure results",
                f"Chưa tìm thấy thư mục:\n{allure_dir}"
            )
            return
        
        allure_cmd = r"C:\allure-2.32.0\bin\allure.bat"
        
        try:
            subprocess.Popen(
                [allure_cmd, "serve", str(allure_dir)],
                cwd=str(self.project_root),
                shell=True
            )
            
            messagebox.showinfo(
                "Allure",
                "Đang mở Allure report..."
            )
            
        except Exception as exc:
            messagebox.showerror(
                "Lỗi",
                f"Không thể mở Allure report:\n{type(exc).__name__}: {exc}"
            )

# ============================================================================
# ENTRY POINT
# ============================================================================


def main() -> None:
    root = tk.Tk()
    AutoTestGenGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()

# Auto Test Generator for Python (AST-Based)

Công cụ nghiên cứu và xây dựng khả năng **tự động sinh Unit Test cho Python dựa trên phân tích cấu trúc mã nguồn bằng AST**. Hệ thống đọc file Python, phân tích cấu trúc hàm/lớp/nhánh điều kiện/ngoại lệ, suy luận dữ liệu kiểm thử bằng heuristic, sinh file test theo định dạng `pytest`, sau đó có thể chạy test và đo coverage.

Dự án này phù hợp với bối cảnh đồ án/nghiên cứu về:

- Static Analysis cho Python.
- Phân tích cây cú pháp trừu tượng AST.
- Xử lý thách thức Dynamic Typing khi sinh test tự động.
- Sinh test case từ Type Hints, AST Constraints và Semantic Heuristic.
- Đánh giá test sinh ra bằng `pytest` và `coverage.py`.

---

## 1. Mục tiêu đề tài

Tên đề tài có thể mô tả là:

> Nghiên cứu và xây dựng công cụ tự động sinh Unit Test dựa trên phân tích cấu trúc mã nguồn cho ngôn ngữ Python.

Mục tiêu chính của công cụ:

```txt
Python source file / folder
→ AST Parser
→ Metadata extraction
→ Heuristic test value generation
→ Pytest code generation
→ Optional pytest / coverage / report execution
```

Công cụ không nhằm thay thế hoàn toàn lập trình viên khi viết unit test nghiệp vụ. Vai trò thực tế của nó là **tự động tạo bộ test ban đầu** để developer có điểm xuất phát nhanh hơn, nhìn thấy các nhánh điều kiện, case biên, case ngoại lệ và coverage cơ bản.

---

## 2. Phạm vi hỗ trợ hiện tại

### 2.1. Phân tích mã nguồn bằng AST

Module `core_engine/ast_parser.py` sử dụng `ast.parse()` và các lớp `ast.NodeVisitor` để trích xuất metadata từ mã nguồn Python.

Hỗ trợ chính:

- Hàm đồng bộ `def`.
- Hàm bất đồng bộ `async def`.
- Class và method.
- Constructor `__init__` dưới dạng metadata để phục vụ khởi tạo object.
- `staticmethod`, `classmethod`, `property`, instance method.
- Tham số hàm, type hints, default values.
- Return annotation và return expression metadata.
- Nhánh điều kiện `if` / `elif` / `else`.
- Biểu thức so sánh `Compare`, `BoolOp`, `UnaryOp`, `Name`, `Call`, `Attribute`.
- Ràng buộc dạng đơn giản như `age >= 18`, `code == 200`.
- Chained comparison như `0 < age < 100`.
- Transform phổ biến như `len(text)`, `name.strip()`.
- `raise`, `try/except` và exception type.
- Metadata vòng lặp `for`, `while`, `async for` ở mức nhận diện cấu trúc.
- Source location: `lineno`, `end_lineno`, `col_offset`, `source_segment`.

### 2.2. Suy luận dữ liệu test trong môi trường Dynamic Typing

Module `core_engine/heuristics.py` kết hợp nhiều nguồn thông tin:

1. **Type Hints**  
   Ví dụ: `age: int`, `name: str`, `items: list`.

2. **AST Constraints**  
   Ví dụ: `age >= 18` → sinh quanh biên `17`, `18`, `19`.

3. **Transform-aware constraints**  
   Ví dụ: `len(text) < 10` → sinh chuỗi có độ dài `9`, `10`, `11`.

4. **Semantic Heuristic theo tên tham số**  
   Ví dụ: `email`, `age`, `password`, `phone`, `url`, `price`, `count`, `is_active`.

5. **Exception-trigger heuristic**  
   Ví dụ: `ZeroDivisionError`, `ValueError`, `TypeError`, `KeyError`, `IndexError`.

Output của heuristic có dạng chiến lược dữ liệu:

```python
{
    "safe": [...],
    "raise": [...],
    "smoke": ...,
}
```

### 2.3. Sinh mã Pytest

Module `core_engine/code_generator.py` sinh file `test_*.py` theo định dạng `pytest`.

Hỗ trợ:

- Smoke test.
- Boundary test bằng `pytest.mark.parametrize`.
- Exception test bằng `pytest.raises`.
- Async test bằng `pytest.mark.asyncio`.
- Test cho class method.
- Khởi tạo object theo constructor metadata.
- Gọi đúng `instance`, `staticmethod`, `classmethod`, `property`.
- Assertion theo type và một số exact assertion an toàn cho literal return.
- Allure optional qua `use_allure=True`; mặc định sinh pytest thuần.

---

## 3. Kiến trúc thư mục

```txt
auto-test-gen-python-main-root-new/
│
├── core_engine/
│   ├── __init__.py
│   ├── ast_parser.py          # Phân tích AST và trích xuất metadata
│   ├── heuristics.py          # Suy luận kiểu dữ liệu và sinh test values
│   ├── code_generator.py      # Sinh file pytest
│   ├── coverage_config.py     # Cấu hình coverage.py / pytest-cov
│   ├── cli.py                 # Giao diện dòng lệnh
│   └── gui.py                 # Giao diện Tkinter
│
├── demo_inputs/
│   ├── abs_utils.py
│   ├── age_utils.py
│   ├── condition_utils.py
│   ├── math_utils.py
│   └── string_utils.py
│
├── conftest.py                # Optional pytest-html enrichment
├── requirements.txt
├── pyproject.toml
└── README.md
```

## 4. Cài đặt

Yêu cầu:

- Python `>= 3.10`.
- Nên dùng virtual environment.

Tạo môi trường ảo:

```bash
python -m venv .venv
```

Kích hoạt trên Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Kích hoạt trên macOS/Linux:

```bash
source .venv/bin/activate
```

Cài dependencies:

```bash
pip install -r requirements.txt
```

`requirements.txt` hiện bao gồm các dependency phục vụ CLI, test, coverage, pytest-html và Allure:

```txt
pytest
pytest-cov
pytest-asyncio
rich
pytest-html
allure-pytest
```

---

## 5. Sử dụng CLI

CLI là cách chạy ổn định nhất để demo pipeline.

Xem help:

```bash
python -m core_engine.cli --help
```

### 5.1. Dry-run: chỉ phân tích AST, không ghi file test

```bash
python -m core_engine.cli demo_inputs --dry-run
```

Khi thêm `--verbose`, CLI hiển thị chi tiết hơn về function, class, method, branch và constraint:

```bash
python -m core_engine.cli demo_inputs --dry-run --verbose
```

### 5.5. Sinh test, chạy pytest và đo coverage

```bash
python -m core_engine.cli demo_inputs -o tests_output --run --cov
```

CLI sẽ dùng `coverage_config.py` để tạo/tìm `.coveragerc`, sau đó truyền args cho `pytest-cov`.

Output coverage gồm:

```txt
Terminal coverage summary
htmlcov/index.html
coverage.xml
```

Có thể chỉ định coverage target riêng:

```bash
python -m core_engine.cli demo_inputs -o tests_output --run --cov --cov-target demo_inputs
```

### 5.6. Sinh test có Allure metadata và chạy Allure result

```bash
python -m core_engine.cli demo_inputs -o tests_output --run --allure
```

Điều kiện:

- Đã cài `allure-pytest`.
- Nếu muốn xem report bằng lệnh `allure serve`, máy cần cài Allure CLI riêng.

Sau khi chạy, kết quả Allure nằm ở:

```txt
allure-results/
```

Xem report:

```bash
allure serve allure-results
```

## 6. Sử dụng GUI

Chạy giao diện Tkinter:

```bash
python -m core_engine.gui
```

Flow GUI:

```txt
Chọn file Python
→ Xem metadata AST
→ Sinh test preview
→ Chạy pytest
→ Xem run log / coverage summary / report
```

GUI hiện hỗ trợ chọn một file `.py`. Nếu muốn generate nhiều file/folder, nên dùng CLI.

Ghi chú:

- Allure mặc định tắt để tránh lỗi thiếu optional plugin.
- Coverage dùng `coverage_config.py`.
- GUI có log panel để xem stdout/stderr đầy đủ khi chạy pytest.
- Cần chạy GUI trên môi trường có desktop display.

---

## 7. Coverage và report

Dự án dùng `coverage.py` thông qua plugin `pytest-cov`.

Các thành phần liên quan:

```txt
core_engine/coverage_config.py
.coveragerc
coverage.xml
htmlcov/
conftest.py
```

`coverage_config.py` chịu trách nhiệm:

- Tạo/tìm `.coveragerc`.
- Build args cho `pytest-cov`.
- Cleanup coverage artifact.
- Đọc summary từ `coverage.xml`.

`conftest.py` chỉ làm nhiệm vụ optional:

- Nếu bật `pytest-html`, nó thêm Coverage Summary vào phần summary của HTML report.
- Nếu không có `coverage.xml` hoặc không bật `pytest-html`, nó không làm gì và không làm fail test session.

---

## 9. Ví dụ pipeline với `demo_inputs`

### Dry-run

```bash
python -m core_engine.cli demo_inputs --dry-run --verbose
```

Ví dụ metadata có thể thấy:

```txt
name.strip() == ''  → arg=name, transform=strip
len(text) == 1      → arg=text, transform=len
len(text) < 10      → arg=text, transform=len
```

---

## 10. Giới hạn hiện tại

Đây là công cụ **static-analysis-based test generator**, không phải AI code reviewer, symbolic execution engine, fuzzing engine hay formal verifier.

Các giới hạn chính:

- Không thực thi target code trong giai đoạn phân tích.
- Không hiểu đầy đủ nghiệp vụ/domain logic.
- Generated tests là test khởi đầu, không thay thế test do developer viết.
- Assertion chính xác chỉ an toàn với một số return expression đơn giản/literal.
- Chưa xử lý sâu các pattern Python phức tạp như dynamic import, monkey patching, metaclass, dependency injection phức tạp, database/network side effects.
- GUI hiện tập trung vào một file; batch/folder nên dùng CLI.

Cách hiểu đúng:

```txt
Tool giúp tạo test skeleton và boundary/exception cases ban đầu.
Developer vẫn cần review, chỉnh expected output và bổ sung test nghiệp vụ.
```

---
---

## 12. Liên hệ với báo cáo đồ án

README này tương ứng với các chương sau:

### Chương 2 - Cơ sở lý thuyết

- AST / Abstract Syntax Tree trong Python.
- Dynamic Typing và thách thức khi sinh test tự động.
- Type Hints, AST Constraints và Semantic Heuristic.

### Chương 3 - Phân tích và thiết kế hệ thống

- Phân tích tham số hàm bằng `ast.arguments`.
- Tree Walking bằng `ast.NodeVisitor`.
- Trích xuất điều kiện, constraint, exception, return metadata.
- Bộ sinh dữ liệu kiểm thử đơn giản từ ràng buộc.

### Chương 4 - Cài đặt chương trình

- `ast_parser.py`: module trích xuất logic.
- `heuristics.py`: module suy luận dữ liệu test.
- `code_generator.py`: module sinh mã Pytest.
- `cli.py` và `gui.py`: giao diện sử dụng.
- `coverage_config.py` và `conftest.py`: đánh giá và report.

### Chương 5 - Thử nghiệm và đánh giá

- Chạy tool trên `demo_inputs`.
- Sinh `test_*.py` tự động.
- Chạy `pytest`.
- Đo coverage bằng `coverage.py` / `pytest-cov`.
- Đánh giá khả năng bao phủ nhánh thường và nhánh ngoại lệ.

---

## 13. Tài liệu tham khảo kỹ thuật

- Python `ast` module: https://docs.python.org/3/library/ast.html
- Pytest documentation: https://docs.pytest.org/
- Pytest parametrize: https://docs.pytest.org/en/stable/how-to/parametrize.html
- Coverage.py configuration: https://coverage.readthedocs.io/en/latest/config.html
- pytest-cov configuration: https://pytest-cov.readthedocs.io/en/latest/config.html
- pytest-html user guide: https://pytest-html.readthedocs.io/en/latest/user_guide.html

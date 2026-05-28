# Auto Test Generator for Python (AST-Based White-box Testing)

Công cụ nghiên cứu và xây dựng khả năng **tự động sinh Unit Test cho Python dựa trên phân tích cấu trúc mã nguồn bằng AST**. Hệ thống đọc file hoặc thư mục Python, phân tích hàm/lớp/điều kiện/ngoại lệ, xác định các mục tiêu kiểm thử hộp trắng, sinh tập test case rút gọn theo tiêu chí **Branch/Decision Coverage**, sau đó có thể chạy `pytest` và đo coverage bằng `coverage.py` thông qua `pytest-cov`.

Công cụ phù hợp với bối cảnh đồ án/nghiên cứu về:

- Static Analysis cho mã nguồn Python.
- Phân tích cây cú pháp trừu tượng AST.
- Sinh Unit Test tự động dựa trên cấu trúc mã nguồn.
- Kiểm thử hộp trắng theo Statement Coverage và Branch/Decision Coverage.
- Suy luận dữ liệu kiểm thử từ Type Hints, AST constraints và semantic heuristic.
- Đánh giá test sinh ra bằng `pytest`, `pytest-cov` và `coverage.py`.

---

## 1. Mục tiêu đề tài

Tên đề tài:

> Nghiên cứu và xây dựng công cụ tự động sinh Unit Test dựa trên phân tích cấu trúc mã nguồn cho ngôn ngữ Python.

Mục tiêu chính của công cụ:

```txt
Python source file / folder
→ AST Parser
→ White-box Objective Planner
→ Candidate Test Data Generator
→ Reduced Test Case Selector
→ Pytest Code Generator
→ Pytest + coverage.py Verification
```

Công cụ không nhằm thay thế hoàn toàn lập trình viên khi viết Unit Test nghiệp vụ. Vai trò thực tế của công cụ là **tự động tạo bộ test ban đầu có định hướng kiểm thử hộp trắng**, giúp developer quan sát các nhánh điều kiện, nhánh ngoại lệ và mức độ bao phủ mã nguồn.

---

## 2. Tư duy thiết kế hiện tại

Phiên bản hiện tại sử dụng **Branch/Decision Coverage** làm tiêu chí kiểm thử hộp trắng chính.

Quy trình sinh test được hiểu như sau:

1. Phân tích AST để xác định hàm, class, method, tham số, `return`, `raise` và các điều kiện rẽ nhánh.
2. Xây dựng các mục tiêu kiểm thử hộp trắng, gồm:
   - statement cần thực thi;
   - nhánh điều kiện cần đi qua;
   - nhánh mặc định hoặc fallback branch;
   - nhánh ngoại lệ chủ động.
3. Sinh dữ liệu ứng viên từ Type Hints, AST constraints, giá trị biên và semantic heuristic.
4. Chọn tập test case rút gọn sao cho các mục tiêu kiểm thử được bao phủ ít nhất một lần.
5. Sinh file `test_*.py` theo định dạng Pytest.
6. Chạy Pytest và dùng coverage.py để xác minh Statement Coverage và Branch Coverage.

Điểm quan trọng: công cụ **không sinh toàn bộ dữ liệu ứng viên ra file test cuối cùng**. Các dữ liệu chỉ được ưu tiên giữ lại nếu phục vụ mục tiêu kiểm thử hộp trắng hoặc nhánh ngoại lệ.

---

## 3. Phạm vi hỗ trợ hiện tại

### 3.1. Phân tích mã nguồn bằng AST

Module `core_engine/ast_parser.py` sử dụng `ast.parse()` và `ast.NodeVisitor` để trích xuất metadata từ mã nguồn Python.

Hỗ trợ chính:

- Hàm đồng bộ `def`.
- Hàm bất đồng bộ `async def`.
- Class và method.
- Constructor `__init__` để phục vụ khởi tạo object.
- `staticmethod`, `classmethod`, `property`, instance method.
- Tham số hàm, Type Hints, default values.
- Return annotation và return expression metadata.
- Nhánh điều kiện `if` / `elif` / `else`.
- Biểu thức so sánh `Compare`, `BoolOp`, `UnaryOp`, `Name`, `Call`, `Attribute`.
- Ràng buộc đơn giản như `age >= 18`, `code == 200`.
- Chained comparison như `0 < age < 100`.
- Transform phổ biến như `len(text)`, `name.strip()`.
- `raise`, `try/except` và exception type.
- Metadata vòng lặp `for`, `while`, `async for` ở mức nhận diện cấu trúc.
- Source location: `lineno`, `end_lineno`, `col_offset`, `source_segment`.

### 3.2. Lập kế hoạch test case theo kiểm thử hộp trắng

Module `core_engine/whitebox_planner.py` chịu trách nhiệm chuyển metadata AST thành các mục tiêu kiểm thử và lựa chọn tập test case rút gọn.

Vai trò chính:

- Xây dựng mục tiêu kiểm thử dạng statement, branch, default path và exception path.
- Dùng dữ liệu ứng viên từ `heuristics.py` để đánh giá case nào có khả năng phủ mục tiêu nào.
- Chọn tập test case rút gọn theo hướng phủ nhiều mục tiêu cần thiết, không ưu tiên case không làm tăng khả năng bao phủ.
- Trả về các nhóm test logic cho `code_generator.py`:
  - `statement_cases`;
  - `branch_cases`;
  - `exception_cases`;
  - `unresolved_objectives`.

### 3.3. Suy luận dữ liệu test trong môi trường Dynamic Typing

Module `core_engine/heuristics.py` kết hợp nhiều nguồn thông tin:

1. **Type Hints**  
   Ví dụ: `age: int`, `name: str`, `items: list`.

2. **AST Constraints**  
   Ví dụ: `age >= 18` tạo dữ liệu quanh điểm biên như `17`, `18`, `19`.

3. **Transform-aware constraints**  
   Ví dụ: `len(text) < 10` tạo chuỗi có độ dài đại diện cho các nhánh điều kiện.

4. **Semantic heuristic theo tên tham số**  
   Ví dụ: `email`, `age`, `password`, `phone`, `url`, `price`, `count`, `is_active`.

5. **Exception-trigger heuristic**  
   Ví dụ: dữ liệu kích hoạt `ZeroDivisionError`, `ValueError`, `TypeError`, `KeyError`, `IndexError` trong phạm vi có thể suy luận.

Lưu ý: các dữ liệu này là **candidate test data**. Tập test cuối cùng do `whitebox_planner.py` lựa chọn dựa trên mục tiêu kiểm thử hộp trắng.

### 3.4. Sinh mã Pytest

Module `core_engine/code_generator.py` sinh file `test_*.py` theo định dạng `pytest`.

Các nhóm test sinh ra:

- `test_<name>_statement_coverage`: dùng cho hàm không có nhánh rõ ràng hoặc cần case đại diện để thực thi statement chính.
- `test_<name>_branch_coverage`: dùng cho hàm có điều kiện rẽ nhánh, thường kết hợp `pytest.mark.parametrize`.
- `test_<name>_exception`: dùng cho nhánh phát sinh ngoại lệ, sử dụng `pytest.raises()`.

Hỗ trợ thêm:

- Async test bằng `pytest.mark.asyncio`.
- Test cho class method.
- Khởi tạo object theo constructor metadata.
- Gọi đúng `instance`, `staticmethod`, `classmethod`, `property`.
- Assertion dựa trên literal return, return expression đơn giản hoặc type assertion khi chưa suy luận được expected output an toàn.
- Allure metadata tùy chọn qua `use_allure=True`; mặc định sinh Pytest thuần.

---

## 4. Kiến trúc thư mục

```txt
auto-test-gen-python-main-root/
│
├── core_engine/
│   ├── __init__.py
│   ├── ast_parser.py            # Phân tích AST và trích xuất metadata
│   ├── heuristics.py            # Sinh dữ liệu ứng viên từ type/constraint/semantic
│   ├── whitebox_planner.py      # Lập kế hoạch test case theo mục tiêu white-box
│   ├── assertion_inference.py   # Suy luận assertion an toàn khi có thể
│   ├── code_generator.py        # Sinh file Pytest từ kế hoạch test
│   ├── coverage_config.py       # Cấu hình coverage.py / pytest-cov
│   ├── cli.py                   # Giao diện dòng lệnh
│   └── gui.py                   # Giao diện Tkinter
│
├── demo_inputs/
│   ├── abs_utils.py
│   ├── age_utils.py
│   ├── condition_utils.py
│   ├── loop.py
│   ├── math_utils.py
│   └── string_utils.py
│
├── demo_inputs_buggy/
│   └── ...
│
├── conftest.py                  # Optional pytest-html enrichment
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## 5. Cài đặt

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

`requirements.txt` gồm các dependency phục vụ CLI, test, coverage, pytest-html và Allure:

```txt
pytest
pytest-cov
pytest-asyncio
rich
pytest-html
allure-pytest
```

---

## 6. Sử dụng CLI

CLI là cách chạy ổn định nhất để demo pipeline.

Xem help:

```bash
python -m core_engine.cli --help
```

### 6.1. Dry-run: chỉ phân tích AST, không ghi file test

```bash
python -m core_engine.cli demo_inputs --dry-run
```

Có thể thêm `--verbose` để xem chi tiết function, class, method, branch và constraint:

```bash
python -m core_engine.cli demo_inputs --dry-run --verbose
```

### 6.2. Sinh test ra thư mục đầu ra

```bash
python -m core_engine.cli demo_inputs -o tests_output
```

Kết quả là các file `test_*.py` được sinh trong thư mục `tests_output`.

### 6.3. Sinh test, chạy Pytest và đo coverage

```bash
python -m core_engine.cli demo_inputs -o tests_output --run --cov --clean-all
```

Lệnh trên thực hiện toàn bộ pipeline:

```txt
Phân tích AST
→ Lập kế hoạch test case theo Branch/Decision Coverage
→ Sinh Pytest
→ Chạy Pytest
→ Đo coverage bằng pytest-cov / coverage.py
```

Kết quả thực nghiệm hiện tại trên `demo_inputs`:

```txt
White-box criterion   : Branch/Decision Coverage
Input files scanned   : 6
Supported callables   : 10
Skipped callables     : 0
Generated test files  : 6
Generated test cases  : 22
Unresolved objectives : 0
Pytest result         : Passed
Pytest exit code      : 0
Statements            : 50/50
Missing statements    : 0
Branches              : 28/28
Statement coverage    : 100.00%
Branch coverage       : 100.00%
```

Coverage output gồm:

```txt
Terminal coverage summary
htmlcov/index.html
coverage.xml
```

Có thể chỉ định coverage target riêng:

```bash
python -m core_engine.cli demo_inputs -o tests_output --run --cov --cov-target demo_inputs
```

### 6.4. Sinh test có Allure metadata và chạy Allure result

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

---

## 7. Sử dụng GUI

Chạy giao diện Tkinter:

```bash
python -m core_engine.gui
```

Flow GUI:

```txt
Chọn file Python
→ Xem metadata AST
→ Sinh test preview
→ Chạy Pytest
→ Xem run log / coverage summary / report
```

GUI hiện hỗ trợ chọn một file `.py`. Nếu muốn generate nhiều file hoặc cả folder, nên dùng CLI.

Ghi chú:

- Allure mặc định tắt để tránh lỗi thiếu optional plugin.
- Coverage dùng `coverage_config.py`.
- GUI có log panel để xem stdout/stderr đầy đủ khi chạy Pytest.
- Cần chạy GUI trên môi trường có desktop display.

---

## 8. Coverage và report

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

- Tạo hoặc tìm `.coveragerc`.
- Build args cho `pytest-cov`.
- Cleanup coverage artifact.
- Đọc summary từ `coverage.xml`.

`conftest.py` chỉ làm nhiệm vụ optional:

- Nếu bật `pytest-html`, nó thêm Coverage Summary vào phần summary của HTML report.
- Nếu không có `coverage.xml` hoặc không bật `pytest-html`, nó không làm fail test session.

---

## 9. Ví dụ output test sinh ra

Ví dụ với một hàm có nhiều nhánh điều kiện như `check_status(code)`, công cụ sinh nhóm test dạng `branch_coverage`:

```python
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (0, "Unknown"),
        (200, "OK"),
        (400, "Bad Request"),
        (401, "Unauthorized"),
        (404, "Not Found"),
        (500, "Server Error"),
    ],
)
def test_check_status_branch_coverage(code, expected):
    result = check_status(code)
    assert result == expected
```

Ví dụ với nhánh ngoại lệ:

```python
@pytest.mark.parametrize(
    ("a", "b"),
    [
        (-1.0, 0.0),
    ],
)
def test_calculator_divide_exception(a, b):
    obj_calculator = Calculator()
    with pytest.raises(ArithmeticError):
        obj_calculator.divide(a, b)
```

---

## 10. Giới hạn hiện tại

Đây là công cụ **static-analysis-based test generator**, không phải AI code reviewer, symbolic execution engine, fuzzing engine hay formal verifier.

Các giới hạn chính:

- Không thực thi target code trong giai đoạn phân tích.
- Không hiểu đầy đủ nghiệp vụ/domain logic.
- Generated tests là test khởi đầu, không thay thế test do developer viết.
- Assertion chính xác chỉ an toàn với một số return expression đơn giản hoặc literal return.
- Chưa xử lý triệt để object phức tạp hoặc nested attribute như `user.profile.age`.
- Chưa xử lý sâu dynamic import, monkey patching, metaclass, dependency injection phức tạp, database/network/file-system side effects.
- Chưa cam kết full Path Coverage cho mọi hàm Python, vì số lượng đường đi có thể tăng nhanh khi có điều kiện lồng nhau hoặc vòng lặp.
- GUI hiện tập trung vào một file; batch/folder nên dùng CLI.

Cách hiểu đúng:

```txt
Tool sinh tập Unit Test ban đầu theo mục tiêu kiểm thử hộp trắng.
Developer vẫn cần review assertion, chỉnh expected output và bổ sung test nghiệp vụ nếu cần.
```

---

## 11. Liên hệ với báo cáo đồ án

README này tương ứng với các chương sau:

### Chương 2 - Cơ sở lý thuyết

- AST / Abstract Syntax Tree trong Python.
- Dynamic Typing và thách thức khi sinh test tự động.
- Type Hints, AST Constraints và Semantic Heuristic.
- Pytest, Parametrize và coverage.py.

### Chương 3 - Phân tích và thiết kế hệ thống

- Phân tích tham số hàm bằng `ast.arguments`.
- Tree Walking bằng `ast.NodeVisitor`.
- Trích xuất điều kiện, constraint, exception, return metadata.
- Xây dựng mục tiêu kiểm thử hộp trắng theo Branch/Decision Coverage.
- Chọn tập test case rút gọn theo objective.

### Chương 4 - Cài đặt chương trình

- `ast_parser.py`: module trích xuất logic.
- `whitebox_planner.py`: module lập kế hoạch test case theo mục tiêu kiểm thử hộp trắng.
- `heuristics.py`: module sinh dữ liệu ứng viên.
- `assertion_inference.py`: module suy luận assertion.
- `code_generator.py`: module sinh mã Pytest.
- `cli.py` và `gui.py`: giao diện sử dụng.
- `coverage_config.py` và `conftest.py`: đánh giá và report.

### Chương 5 - Thử nghiệm và đánh giá

- Chạy tool trên `demo_inputs`.
- Sinh `test_*.py` tự động.
- Chạy `pytest`.
- Đo coverage bằng `coverage.py` / `pytest-cov`.
- Đánh giá statement coverage, branch coverage và nhánh ngoại lệ.

---

## 12. Tài liệu tham khảo kỹ thuật

- Python `ast` module: https://docs.python.org/3/library/ast.html
- Pytest documentation: https://docs.pytest.org/
- Pytest parametrize: https://docs.pytest.org/en/stable/how-to/parametrize.html
- Pytest exception assertion: https://docs.pytest.org/en/stable/how-to/assert.html
- Coverage.py branch coverage: https://coverage.readthedocs.io/en/latest/branch.html
- pytest-cov reporting: https://pytest-cov.readthedocs.io/en/latest/reporting.html
- pytest-html user guide: https://pytest-html.readthedocs.io/en/latest/user_guide.html

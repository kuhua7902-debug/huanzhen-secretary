"""G. ``verify_output`` 回归 —— ``core/new_tools.py``。

背景（真实事故）：``_verify_sheet`` 以前把「合计[金额]: 100.00」「验证通过」
这类**正常信息**也 append 进 ``issues``，而函数末尾用 ``if issues: return FAIL``
判定，于是"只要用了 check_sum 就永远 FAIL" —— 校验完全正确也报失败，
模型因此反复重做数据、用户认为工具坏了。

本文件只测 ``verify_output`` 这一个纯函数；导入 ``core.new_tools`` 会连带拉起
chromadb / 向量库模块（较慢但无网络、无子进程），所以整个模块标记 slow。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

openpyxl = pytest.importorskip("openpyxl", reason="需要 openpyxl 构造 .xlsx 用例")


@pytest.fixture(scope="module")
def verify_output():
    """惰性导入被测函数（导入 core.new_tools 会注册全局工具并加载 chromadb）。"""
    mod = pytest.importorskip(
        "core.new_tools", reason="core.new_tools 依赖（chromadb 等）不可用"
    )
    return mod.verify_output


@pytest.fixture
def sales_xlsx(tmp_path):
    """造一张小表：表头 2 列 + 2 行数据，金额合计恰好 100。"""
    path = tmp_path / "sales.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["名称", "金额"])
    ws.append(["甲", 60])
    ws.append(["乙", 40])
    wb.save(path)
    wb.close()
    return path


# ────────────────── check_sum：成功必须 PASS（历史 bug 的正主） ──────────────────


def test_check_sum_pass_when_sum_matches(verify_output, sales_xlsx):
    """风险（核心回归）：合计正确却返回 FAIL —— 曾导致工具完全不可用。"""
    result = verify_output(path=str(sales_xlsx), check_sum="金额=100")
    assert result.startswith("PASS"), result
    assert "FAIL" not in result


def test_check_sum_accepts_float_expected_value(verify_output, sales_xlsx):
    """风险：期望值写成 100.0 / 100.00 时被当成"不相等"。"""
    for expected in ("100", "100.0", "100.00", "99.995"):
        result = verify_output(path=str(sales_xlsx), check_sum=f"金额={expected}")
        assert result.startswith("PASS"), f"{expected} -> {result}"


def test_check_sum_fail_when_sum_is_wrong(verify_output, sales_xlsx):
    """风险：合计不符却返回 PASS —— 会放过真正算错的数据（比假报警更危险）。"""
    result = verify_output(path=str(sales_xlsx), check_sum="金额=999")
    assert result.startswith("FAIL"), result
    assert "合计" in result


def test_check_sum_dash_means_auto_sum_and_should_pass(verify_output, sales_xlsx):
    """风险：``金额=-`` 是"只求和、不校验"的约定用法，不能被判成失败。"""
    result = verify_output(path=str(sales_xlsx), check_sum="金额=-")
    assert result.startswith("PASS"), result
    assert "100" in result


def test_check_sum_on_missing_column_fails(verify_output, sales_xlsx):
    """风险：列名写错时应报 FAIL 并指出列不存在，而不是静默通过。"""
    result = verify_output(path=str(sales_xlsx), check_sum="不存在的列=1")
    assert result.startswith("FAIL"), result


def test_check_sum_multiple_parts_all_pass(verify_output, sales_xlsx):
    """风险：多条件用 ``;`` 分隔时，只要有一条通过就整体 PASS。"""
    result = verify_output(path=str(sales_xlsx), check_sum="金额=100;金额=100.0")
    assert result.startswith("PASS"), result


def test_check_sum_multiple_parts_one_wrong_fails(verify_output, sales_xlsx):
    """风险：多条件里有错的那个被忽略。"""
    result = verify_output(path=str(sales_xlsx), check_sum="金额=100;金额=1")
    assert result.startswith("FAIL"), result


def test_check_sum_with_no_check_still_passes(verify_output, sales_xlsx):
    """风险：不传 check_sum 时不应受影响（回归修复不能破坏原路径）。"""
    result = verify_output(path=str(sales_xlsx))
    assert result.startswith("PASS"), result


def test_check_sum_on_non_numeric_column_fails(verify_output, tmp_path):
    """风险：对文本列求和应报 FAIL 而不是抛异常。"""
    path = tmp_path / "text_col.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["名称", "备注"])
    ws.append(["甲", "abc"])
    wb.save(path)
    wb.close()

    assert verify_output(path=str(path), check_sum="备注=10").startswith("FAIL")


# ────────────────────────── 行数 / 列名 / 空值 ──────────────────────────


def test_expect_rows_match_passes(verify_output, sales_xlsx):
    """风险：数据行数统计把表头算进去 → 永远差 1，用户以为数据丢了。"""
    assert verify_output(path=str(sales_xlsx), expect_rows=2).startswith("PASS")


def test_expect_rows_mismatch_fails(verify_output, sales_xlsx):
    """风险：行数不符却 PASS —— 少写/多写数据没有被发现。"""
    result = verify_output(path=str(sales_xlsx), expect_rows=5)
    assert result.startswith("FAIL"), result
    assert "行数不符" in result


def test_check_columns_present_passes_and_missing_fails(verify_output, sales_xlsx):
    """风险：列名检查失效会让"表头写错"的数据流到下游。"""
    assert verify_output(path=str(sales_xlsx), check_columns="名称,金额").startswith("PASS")
    missing = verify_output(path=str(sales_xlsx), check_columns="名称,数量")
    assert missing.startswith("FAIL") and "缺少列" in missing


def test_empty_cell_is_reported_as_failure(verify_output, tmp_path):
    """风险：空值检测是最常用的数据质量闸门，失效会放过脏数据。"""
    path = tmp_path / "nulls.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["名称", "金额"])
    ws.append(["甲", 10])
    ws.append(["乙", None])
    ws.append(["丙", 30])
    wb.save(path)
    wb.close()

    result = verify_output(path=str(path))
    assert result.startswith("FAIL") and "空值" in result


def test_missing_sheet_name_fails(verify_output, sales_xlsx):
    """风险：sheet 名写错静默当成"没有 sheet 要查" → 空校验通过。"""
    result = verify_output(path=str(sales_xlsx), sheet_name="不存在的表")
    assert result.startswith("FAIL") and "未找到 Sheet" in result


def test_existing_sheet_name_is_checked(verify_output, sales_xlsx):
    """风险：指定 sheet 后漏检该 sheet 的内容。"""
    result = verify_output(path=str(sales_xlsx), sheet_name="Sheet1", check_sum="金额=100")
    assert result.startswith("PASS"), result


# ────────────────────────── 边界与错误路径 ──────────────────────────


def test_missing_file_returns_fail(verify_output, tmp_path):
    """风险：文件不存在必须返回 FAIL 文本（工具层不能抛异常）。"""
    result = verify_output(path=str(tmp_path / "nope.xlsx"))
    assert result.startswith("FAIL") and "不存在" in result


def test_unsupported_extension_returns_skip(verify_output, tmp_path):
    """风险：不支持的格式应明确 SKIP（而非 PASS，避免"假通过"）。"""
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    assert verify_output(path=str(p)).startswith("SKIP")


def test_multi_sheet_checks_every_sheet(verify_output, tmp_path):
    """风险：多 sheet 报表只校验第一个 sheet，漏掉后面的错误数据。"""
    path = tmp_path / "multi.xlsx"
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "一月"
    ws1.append(["名称", "金额"])
    ws1.append(["甲", 10])
    ws2 = wb.create_sheet("二月")
    ws2.append(["名称", "金额"])
    ws2.append(["乙", 20])
    wb.save(path)
    wb.close()

    result = verify_output(path=str(path), check_sum="金额=10")
    # 一月合计 10 通过，二月合计 20 不符 → 整体必须 FAIL
    assert result.startswith("FAIL"), result


def test_csv_verification_path(verify_output, tmp_path):
    """风险：CSV 分支与 Excel 分支行为不一致（CSV 是导出常用格式）。"""
    good = tmp_path / "good.csv"
    good.write_text("名称,金额\n甲,60\n乙,40\n", encoding="utf-8")

    assert verify_output(path=str(good), expect_rows=2, check_columns="名称,金额").startswith("PASS")
    assert verify_output(path=str(good), expect_rows=9).startswith("FAIL")
    assert verify_output(path=str(good), check_columns="数量").startswith("FAIL")

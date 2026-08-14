"""核心解析逻辑的单元测试（不依赖网络）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lecture_spider import (  # noqa: E402
    _extract_answer,
    _extract_challenge_id,
    _extract_field,
    _simple_hash,
)


# ---------- simpleHash ----------

def test_simple_hash_single_char():
    # 手算: ((0<<5)-0)+97 = 97
    assert _simple_hash("a") == 97


def test_simple_hash_two_chars():
    # 手算: (97<<5)-97+98 = 3104-97+98 = 3105
    assert _simple_hash("ab") == 3105


def test_simple_hash_empty():
    assert _simple_hash("") == 0


def test_simple_hash_deterministic():
    assert _simple_hash("hello") == _simple_hash("hello")


# ---------- challengeId 提取 ----------

def test_challenge_id_double_quotes():
    assert _extract_challenge_id('var challengeId = "abc123XYZ";') == "abc123XYZ"


def test_challenge_id_single_quotes():
    assert _extract_challenge_id("var challengeId = 'xyz_789';") == "xyz_789"


def test_challenge_id_with_spaces():
    assert _extract_challenge_id('var   challengeId   =   "qwe"') == "qwe"


def test_challenge_id_missing():
    assert _extract_challenge_id("no challenge here") is None


# ---------- answer 计算 ----------

def test_answer_new_format_multiplication():
    html = "var a = 18; var b = 13; var operator = '*';"
    assert _extract_answer(html) == 234


def test_answer_new_format_addition():
    html = "var a = 5; var b = 3; var operator = '+';"
    assert _extract_answer(html) == 8


def test_answer_new_format_subtraction():
    html = "var a = 10; var b = 4; var operator = '-';"
    assert _extract_answer(html) == 6


def test_answer_old_format():
    html = "var answer = 42;"
    assert _extract_answer(html) == 42


def test_answer_negative_result():
    html = "var a = 3; var b = 7; var operator = '-';"
    assert _extract_answer(html) == -4


def test_answer_missing():
    assert _extract_answer("nothing here") is None


# ---------- 详情字段提取 ----------

def test_extract_field_basic():
    content = "报告人：张三\n时间：2026年8月15日\n地点：数学楼2-3会议室"
    assert _extract_field(content, ["报告人"]) == "张三"
    assert _extract_field(content, ["时间"]) == "2026年8月15日"
    assert _extract_field(content, ["地点"]) == "数学楼2-3会议室"


def test_extract_field_with_spaces_in_label():
    content = "报 告 人：李四"
    assert _extract_field(content, ["报告人"]) == "李四"


def test_extract_field_colon_variants():
    assert _extract_field("报告人:王五", ["报告人"]) == "王五"
    assert _extract_field("报告人：赵六", ["报告人"]) == "赵六"


def test_extract_field_fallback_labels():
    content = "主讲人：钱七"
    assert _extract_field(content, ["报告人", "主讲人"]) == "钱七"


def test_extract_field_preserves_comma():
    # 逗号不应截断（支持多人）
    content = "报告人：张三，李四"
    assert _extract_field(content, ["报告人"]) == "张三，李四"


def test_extract_field_terminates_at_newline():
    content = "地点：数学楼会议室\n时间：2026年"
    assert _extract_field(content, ["地点"]) == "数学楼会议室"


def test_extract_field_not_found():
    assert _extract_field("随便一段文字", ["报告人"]) == ""


def test_extract_field_after_colon_newline():
    # 表格布局中值可能在下一行
    content = "报告人：\n孙八"
    assert _extract_field(content, ["报告人"]) == "孙八"

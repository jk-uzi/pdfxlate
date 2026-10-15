"""Tests for the translated-paragraph font fitting.

`fit_font_size` is a pure function so the layout policy can be tested without
constructing pdfminer layout objects. Widths are modelled per typesetting unit
as ``scaled * size + fixed`` (see ``Unit``), which mirrors the drawing loop.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfxlate.pdf.converter import (  # noqa: E402
    MIN_FIT_SCALE,
    Unit,
    _wrapped_lines,
    fit_font_size,
)

# 中文默认行高
LH = 1.4


def chars(n: int, width_em: float = 1.0):
    """n 个等宽排版单元，宽度为 width_em em（随字号缩放）。"""
    return [Unit(width_em, 0.0, 0.0) for _ in range(n)]


def lines_at(size, width, units, start=0.0):
    return _wrapped_lines(size, width, units, start)


def need(size, width, units, line_height, start=0.0):
    return lines_at(size, width, units, start) * size * line_height


# ---------------------------------------------------------------- 放得下

def test_fits_leaves_size_and_line_height_untouched():
    """零溢出的段落必须原样返回，修复不能改变它们的输出。"""
    units = chars(10)  # size 10、宽 100 → 每行 10 个，共 1 行
    size, lh = fit_font_size(10.0, 100.0, 50.0, units, LH)
    assert size == 10.0
    assert lh == LH


def test_fits_by_squeezing_line_height_only():
    """靠压行高就能放下时，沿用历史逻辑：字号不变，行高按 0.05 步进下调。"""
    units = chars(20)  # size 10、宽 100 → 2 行
    size, lh = fit_font_size(10.0, 100.0, 25.0, units, LH)
    # 旧实现: 2*10*1.4=28>25 → 1.25 时 25<=25 停
    assert size == 10.0
    assert lh == pytest.approx(1.25)
    assert need(size, 100.0, units, lh) <= 25.0


# ---------------------------------------------------------------- 放不下

def test_shrinks_size_when_line_height_cannot_fit():
    """行高压到下限仍溢出时缩小字号，且缩到刚好能容纳。"""
    units = chars(15)
    size, lh = fit_font_size(10.0, 100.0, 15.0, units, LH)
    assert size < 10.0
    assert lh == LH  # 缩字号后恢复默认行高，不再挤行
    # 恰好容纳：再大一点就放不下
    assert need(size, 100.0, units, LH) <= 15.0 + 1e-9
    assert need(size + 0.01, 100.0, units, LH) > 15.0


def test_shrink_never_below_floor():
    """缩到下限仍放不下时返回下限，不无限缩。"""
    units = chars(100)
    size, lh = fit_font_size(10.0, 100.0, 5.0, units, LH)
    assert size == pytest.approx(10.0 * MIN_FIT_SCALE)
    assert lh == LH


def test_floor_result_accepts_overflow():
    """下限结果确实放不下，说明调用方接受了溢出而不是继续压行高。"""
    units = chars(100)
    size, lh = fit_font_size(10.0, 100.0, 5.0, units, LH)
    assert need(size, 100.0, units, lh) > 5.0


def test_shrink_is_monotone_safe():
    """对若干随机化的宽高组合，结果要么原样，要么真正放得下或触及下限。"""
    units = chars(40)
    for height in (5.0, 12.0, 20.0, 40.0, 80.0):
        size, lh = fit_font_size(12.0, 200.0, height, units, LH)
        assert size >= 12.0 * MIN_FIT_SCALE - 1e-9
        assert size <= 12.0
        if size > 12.0 * MIN_FIT_SCALE:
            assert need(size, 200.0, units, lh) <= height + 1e-6


# ---------------------------------------------------------------- 公式宽度

def test_formula_width_does_not_scale_with_size():
    """公式单元宽度固定：缩字号不会让公式变窄，行数估计必须计入。"""
    # 5 个公式单元，每个固定宽 100（每行一个），高度容不下压行高后的 5 行
    units = [Unit(0.0, 100.0, 0.0) for _ in range(5)]
    assert lines_at(10.0, 150.0, units) == 5
    assert lines_at(6.0, 150.0, units) == 5  # 字号变化不影响公式换行
    # 缩字号不能减少固定宽公式的行数，只能缩到下限
    size, lh = fit_font_size(10.0, 150.0, 40.0, units, LH)
    assert size == pytest.approx(10.0 * MIN_FIT_SCALE)


def test_mixed_text_and_formula_units():
    """正文随字号缩放、公式固定，混合时行数随字号单调不增。"""
    units = chars(10) + [Unit(0.0, 40.0, 0.0)] + chars(10)
    prev = None
    for s in (10.0, 9.0, 8.0, 7.0, 6.0):
        n = lines_at(s, 100.0, units)
        if prev is not None:
            assert n <= prev  # 字号变小，行数不会增多
        prev = n


# ---------------------------------------------------------------- 边界

def test_empty_paragraph_returns_input():
    size, lh = fit_font_size(10.0, 100.0, 50.0, [], LH)
    assert size == 10.0
    assert lh == LH


def test_zero_height_does_not_shrink_size():
    size, lh = fit_font_size(10.0, 100.0, 0.0, chars(5), LH)
    assert size == 10.0  # 没有可用高度，缩字号无意义


def test_zero_width_does_not_shrink_size():
    size, lh = fit_font_size(10.0, 0.0, 50.0, chars(5), LH)
    assert size == 10.0


def test_single_character_fits():
    size, lh = fit_font_size(10.0, 100.0, 20.0, chars(1), LH)
    assert size == 10.0
    assert lh == LH


def test_start_offset_limits_first_line():
    """首行起始偏移（段落初始 x 大于左边界）会占掉首行可用宽度。"""
    units = chars(10)
    # 宽 100、偏移 95：首行只剩 5，只能放 1 个字符，其余 9 个挤到下一行
    assert lines_at(10.0, 100.0, units, start=95.0) == 2
    assert lines_at(10.0, 100.0, units, start=0.0) == 1


def test_observed_lines_used_for_line_height_squeeze():
    """传入实排行数时，行高压缩按它计算，保证与修复前的输出一致。"""
    # 模型估 1 行，但实排 3 行；3*10*1.4=42>40 → 压到 1.30 时 39<=40
    units = chars(10)
    size, lh = fit_font_size(10.0, 100.0, 40.0, units, LH, lines=3)
    assert size == 10.0
    assert lh == pytest.approx(1.30)


def test_no_wrap_counts_single_line():
    """wrap=False（纯公式段落）恒算一行，不会因宽度超限而估出多行。"""
    units = chars(100)  # 远宽于段落
    assert lines_at(10.0, 100.0, units) > 1
    assert _wrapped_lines(10.0, 100.0, units, 0.0, False) == 1
    # 单行放不下高度时，不换行意味着只能压行高/缩字号
    size, lh = fit_font_size(10.0, 100.0, 5.0, units, LH, wrap=False)
    assert size == pytest.approx(10.0 * MIN_FIT_SCALE)

"""数值归一化：SQLite 是弱类型，REAL 列历史上可能被写入空字符串等文本。

写入前统一清洗（避免再次产生脏数据），读取时统一容错（保证历史数据仍可查询）。
"""
from __future__ import annotations

from typing import Any

# OCR / 表单可能带千分位、货币符号或百分号；仅用于清洗，不改变业务含义。
_NOISE_CHARS = (",", "，", "￥", "¥", "%", " ", "\u00a0")


def to_optional_float(value: Any) -> float | None:
    """把数值字段规整为 float 或 None。

    - None / 空串 / 纯空白 / 无法解析的文本 -> None（写库即 NULL，读接口不报错）
    - 已是 int / float -> 转成 float
    - 带千分位、货币符号、百分号的字符串 -> 去符号后按数字解析
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        for ch in _NOISE_CHARS:
            text = text.replace(ch, "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None

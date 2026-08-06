"""
数值格式化工具模块，提供将数值转为人类可读字符串的工具函数。

当前提供字节大小格式化（:func:`format_bytes`），可用于文件大小、内存占用、
网络流量等任意字节计数场景。
"""

from pykunlun.data.enums import ByteUnit

#: 最大单位（自动推断时的兜底）
_MAX_UNIT = max(ByteUnit)


def format_bytes(size: float, unit: ByteUnit | str | None = None) -> str:
    """
    格式化字节大小为带单位的可读字符串。

    Args:
        size: 字节数。
        unit: 目标单位。可为 :class:`~pykunlun.data.enums.ByteUnit` 成员、
            字符串标签（B/KB/MB/.../YB，大小写不敏感）或 ``None``。
            ``None`` 或空字符串时自动选取首个不超过 1024 的单位（YB 兜底）。

    Returns:
        形如 ``"1.5 MB"`` 的可读字符串。

    Raises:
        ValueError: ``unit`` 非空但不在支持的单位列表中时抛出。

    Examples:
        >>> format_bytes(1536)
        '1.5 KB'
        >>> format_bytes(1536, unit="B")
        '1536.0 B'
        >>> format_bytes(1536, unit="MB")
        '0.0 MB'
        >>> format_bytes(1536, unit=ByteUnit.KB)
        '1.5 KB'
    """
    if not unit:
        # 自动推断：依次尝试各单位，取首个不超过 1024 的（最大单位兜底）
        for u in ByteUnit:
            if size < 1024 or u is _MAX_UNIT:
                return f"{size:.1f} {u.name}"
            size /= 1024
        # _MAX_UNIT 保证上面循环必定 return，此行逻辑上不可达
        raise RuntimeError("format_bytes 自动推断未收敛")

    # 归一化为 ByteUnit
    if isinstance(unit, str):
        unit = ByteUnit.from_str(unit)

    value = size / (1024 ** unit.value)
    return f"{value:.1f} {unit.name}"

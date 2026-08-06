"""
字节单位枚举。
"""

from enum import IntEnum


class ByteUnit(IntEnum):
    """
    字节单位枚举。

    继承 :class:`enum.IntEnum`，成员值即该单位相对 B（字节）的 1024 次幂指数，
    可直接用于换算：``size / (1024 ** unit.value)``。成员名即显示标签
   （B / KB / MB / ...），通过 :attr:`unit.name <Enum.name>` 获取。

    从字符串构造可使用 :meth:`from_str`（大小写不敏感）或 ``ByteUnit['KB']``
   （大小写敏感）::

        ByteUnit.from_str('kb')  # → ByteUnit.KB
        ByteUnit['KB']           # → ByteUnit.KB
    """

    B = 0
    KB = 1
    MB = 2
    GB = 3
    TB = 4
    PB = 5
    EB = 6
    ZB = 7
    YB = 8

    @classmethod
    def from_str(cls, label: str) -> 'ByteUnit':
        """
        从字符串标签构造枚举成员（大小写不敏感）。

        Args:
            label: 单位标签（如 ``'B'``、``'KB'``、``'mb'``）。

        Returns:
            对应的 :class:`ByteUnit` 成员。

        Raises:
            ValueError: 标签不在支持列表中时抛出。
        """
        key = label.strip().upper()
        try:
            return cls[key]
        except KeyError:
            raise ValueError(
                f"不支持的单位: {label!r}，支持: {[m.name for m in cls]}"
            )

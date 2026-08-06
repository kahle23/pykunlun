"""
通用枚举定义包。

集中管理跨模块复用的枚举类型，一个枚举一个子模块，通过本 ``__init__`` 统一 re-export。
新增枚举时：建子模块 + 在此处加一行 import + ``__all__`` 即可。
"""

from .byte_unit import ByteUnit

__all__ = [
    'ByteUnit',
]

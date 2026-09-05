"""
动作（Action）能力包。

提供动作的注册、查找、执行框架：动作是任意可调用对象，由 name 唯一标识，
业务代码通过 :class:`ActionManager` 统一存取，实现可替换、可增强，按子模块组织。

  - :mod:`pykunlun.action.manager`：动作管理器与动作注册表。
"""

from .manager import ActionManager

__all__ = [
    'ActionManager',
]

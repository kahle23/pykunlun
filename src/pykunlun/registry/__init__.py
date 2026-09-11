"""
通用注册表基建包。

提供"双层注册表"模式的通用件，供 oss / ocr / rdb 等领域的 manager 派生：

  - :mod:`~pykunlun.registry.atom`：:class:`~pykunlun.registry.atom.Registry`
    注册表原子——线程安全的 ``str -> T`` 存取表，只管存取，不涉领域语义；
  - :mod:`~pykunlun.registry.base`：:class:`~pykunlun.registry.base.RegistryManager`
    类注册表 + 实例注册表的管理器基类，领域差异经词汇常量与钩子注入，
    派生方式见 :mod:`~pykunlun.registry.base` 模块 docstring。
"""

from .atom import Registry
from .base import ConfigLoader, RegistryManager

__all__ = [
    'ConfigLoader',
    'Registry',
    'RegistryManager',
]

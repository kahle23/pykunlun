"""
模块导入工具函数。

提供动态导入模块（支持自动安装）以及模块懒加载功能，
简化依赖管理和服务懒初始化流程。
"""

import importlib
from collections.abc import Callable
from types import ModuleType
from typing import Any

from ..system import pip
from . import logutil

log = logutil.getLogger(__name__)


def import_module(module_name: str, install_name: str | None = None) -> ModuleType:
    """
    动态导入模块，未安装时自动安装。

    先尝试导入指定模块，若模块不存在则自动通过 pip 安装后重新导入。

    Args:
        module_name: 要导入的模块名（如 "requests"）。
        install_name: pip 安装时使用的包名。默认与 module_name 相同。

    Returns:
        导入成功后的模块对象。

    Raises:
        ImportError: 模块导入失败且自动安装也失败时抛出。
    """
    # 如果未指定安装包名，则使用模块名
    if not install_name:
        install_name = module_name
    # 尝试导入模块
    try:
        log.debug(f"正在导入模块 {module_name}")
        return importlib.import_module(module_name)
    except ImportError:
        log.warning("模块 %s 未安装，开始安装 %s", module_name, install_name)
        # 尝试安装模块
        success, msg = pip.install(install_name)
        if not success:
            raise ImportError(f"安装 {install_name} 失败: {msg}")
        # 安装成功后，重新导入模块
        log.info(f"{install_name} 安装成功，重新导入 {module_name}")
        return importlib.import_module(module_name)


def create_lazy_loader(lazy_imports: dict[str, str]) -> Callable[[str], Any]:
    """
    创建模块懒加载器。

    生成一个 __getattr__ 函数，用于实现模块属性的延迟导入。
    首次访问属性时才导入对应模块，导入后缓存到全局变量中。

    Args:
        lazy_imports: 懒加载映射字典。
            - key: 属性名称
            - value: 对应的模块路径（如 "mypkg.test.t1"）

    Returns:
        Callable[[str], Any]: 可用作模块 __getattr__ 的函数。

    Example:
        在包的 __init__.py 中使用::

            _LAZY_IMPORTS = {
                "test": "mypkg.test.t1",
                "test1": "mypkg.test.t2",
            }

            __getattr__ = create_lazy_loader(_LAZY_IMPORTS)

        访问 ``mypkg.test`` 时才会实际导入 ``mypkg.test.t1`` 模块。
    """
    def __getattr__(name: str) -> Any:
        if name in lazy_imports:
            module_path = lazy_imports[name]
            module = importlib.import_module(module_path)
            value = getattr(module, name)
            globals()[name] = value
            return value
        raise AttributeError(f"module has no attribute {name!r}")
    # 返回懒加载函数
    return __getattr__

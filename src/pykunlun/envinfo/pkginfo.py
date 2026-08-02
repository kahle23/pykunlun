"""
模块与包信息查询模块。

提供本库顶级包名识别、调用方顶级包名探测、第三方包版本查询等能力，
常用于日志标记、包管理与环境适配场景。

环境变量的读写与 PATH 管理能力由 :mod:`pykunlun.system.env_var`（抽象接口）
提供，平台具体实现由上层包注册，本模块不涉及。

仅依赖 Python 标准库。
"""

import inspect
from functools import cache
from importlib.metadata import (
    PackageNotFoundError,  # noqa: F401  透出给调用方捕获
    version,
)

# 本库顶级包名：用于自身识别（如 python -m pykunlun 场景），避免在多处硬编码 "pykunlun"
_PACKAGE_NAME = __name__.split('.')[0]


# region ======== 模块与包信息 ========

def get_own_top_package_name() -> str:
    """
    获取本库（pykunlun）的顶级包名。

    用于 ``python -m pykunlun`` 等场景下识别自身包名，供版本查询、日志标记、
    配置目录等模块统一引用，避免在代码中硬编码 ``"pykunlun"``。

    注意：返回的是 pykunlun 自身的包名。上层库若需要自身的包名，
    请直接使用字面量或自行维护，不要调用本函数。

    Returns:
        本库的顶级包名。
    """
    return _PACKAGE_NAME


def get_caller_top_package_name(skip_packages: list[str] | None = None) -> str:
    """
    获取调用方的顶级包名（跳过库内部调用）。

    典型场景：外部应用 A 引入某库后，在库内部识别出 A 的包名；
    也兼容 ``python -m A`` 启动方式。遍历调用栈找到第一个顶级包名不在
    跳过集合中的帧；若整条调用栈都在库内部（如库自身的脚本/测试），
    则回退到最近遇到的库包名，再否则回退到本库包名。

    本函数已自动跳过 pykunlun 自身。当 pykunlun 作为底层依赖被其它库封装时，
    调用方应将自身（及中间层）的包名通过 ``skip_packages`` 传入，以便穿透
    这些库找到真正的应用调用方。

    边界说明：REPL、``python -c``、``__main__`` 等场景下 ``__package__``
    可能为空，会被跳过继续向上查找，找不到外部调用方时回退到库包名。

    Args:
        skip_packages: 需视为"库内部"而一并跳过的额外顶级包名列表。
            传入完整包名（如 ``"mylib"``）或带点的包名（如 ``"mylib.core"``）
            均可，内部统一取顶级包名比较。

    Returns:
        调用方的顶级包名；找不到外部调用方时回退到最近的库包名。

    Examples:
        >>> # pykunlun 内部直接调用
        >>> get_caller_top_package_name()
        >>> # 上层库调用，需穿透该库找到其上层应用
        >>> get_caller_top_package_name(["mylib"])
    """
    # 跳过集合：默认含本库（pykunlun）自身，合并调用方传入的额外库包名
    skip = {_PACKAGE_NAME}
    if skip_packages:
        skip.update(str(p).split('.')[0] for p in skip_packages)
    # 记录遍历中最近遇到的"库"包名（非本库），作为找不到外部调用方时的回退值。
    # 例如某上层库调用本函数且整条栈都在该库/pykunlun 内时，回退到该库包名。
    fallback = _PACKAGE_NAME
    # 从当前帧的上一帧（调用方）开始向上遍历；用 f_back 链避免 inspect.stack
    # 抓取每帧源码上下文的开销，也避免持有整个栈的帧引用
    frame = inspect.currentframe()
    frame = frame.f_back if frame is not None else None
    while frame is not None:
        pkg = frame.f_globals.get('__package__')
        if pkg:
            top = pkg.split('.')[0]
            # 严格按顶级包名比较，避免前缀误判（如 mylib_ext / mylib2）
            if top not in skip:
                return str(top)
            if top != _PACKAGE_NAME:
                fallback = str(top)
        frame = frame.f_back
    return fallback


@cache
def get_package_version(package_name: str) -> str:
    """
    获取指定包的版本号。

    包未安装时直接抛出 PackageNotFoundError，不做静默回退。
    如果包代码能被执行（如 __init__.py），说明包已加载，
    若 metadata 仍找不到则说明安装有问题，报错有助于定位。

    版本元数据在运行时不变，已通过 lru_cache 缓存避免重复磁盘 I/O；
    PackageNotFoundError 不会被缓存，安装后重试仍可生效。

    Args:
        package_name: 包名

    Returns:
        包的版本号

    Raises:
        PackageNotFoundError: 包未安装时抛出
    """
    return version(package_name)


# endregion

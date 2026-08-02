"""
路径解析工具模块，提供相对路径到绝对路径的转换能力。

核心函数 :func:`resolve_relative` 根据解析类型将相对路径拼接到对应的基准目录上，
支持当前工作目录、用户主目录、应用数据目录三种基准，跨平台返回规范化绝对路径。
本模块仅做路径转换，不检查文件/目录是否存在。
"""

import os
from collections.abc import Callable
from enum import IntEnum

from pykunlun.envinfo import osenv, pkginfo

# region ======== 解析类型 ========

class ResolveType(IntEnum):
    """路径解析类型枚举。

    继承 :class:`enum.IntEnum`，成员本身即为整数，可直接与整数字面量比较，
    因此 ``resolve_type`` 参数既可传本枚举成员，也可传对应整数（1/2/3）。
    """

    CURRENT = 1   # 当前工作目录
    USER = 2      # 用户主目录
    APP_DATA = 3  # 应用数据目录


# endregion


# region ======== 基准目录解析 ========

def _resolve_to_current(app_name: str | None) -> str:
    """基准目录：当前工作目录。"""
    return os.getcwd()


def _resolve_to_user(app_name: str | None) -> str:
    """基准目录：用户主目录。"""
    return osenv.get_user_home()


def _resolve_to_app_data(app_name: str | None) -> str:
    """基准目录：应用数据目录，app_name 缺省时取调用者顶级包名。"""
    return osenv.get_app_home(app_name or pkginfo.get_caller_top_package_name())


# 解析类型 → 基准目录解析函数的分发表；新增类型只需在此注册一行
_BASE_RESOLVERS: dict[ResolveType, Callable[[str | None], str]] = {
    ResolveType.CURRENT: _resolve_to_current,
    ResolveType.USER: _resolve_to_user,
    ResolveType.APP_DATA: _resolve_to_app_data,
}

# endregion


# region ======== 路径解析 ========

def resolve_relative(relative_path: str, resolve_type: int = ResolveType.CURRENT,
                     app_name: str | None = None) -> str:
    """
    将相对路径按解析类型转换为绝对路径。

    根据解析类型将相对路径拼接到对应的基准目录上，返回规范化后的绝对路径。
    本方法仅做路径转换，不检查文件/目录是否存在。

    三种解析类型对应的基准目录：
    - ``ResolveType.CURRENT``  (1)：当前工作目录（``os.getcwd()``）
    - ``ResolveType.USER``     (2)：用户主目录（``~``），如 ``aa/test.cfg`` → ``/home/user/aa/test.cfg``
    - ``ResolveType.APP_DATA`` (3)：应用数据目录（跨平台，见 :func:`pykunlun.envinfo.osenv.get_app_home`）：
        - Windows：``%APPDATA%/<app_name>``
        - macOS：``~/Library/Application Support/<app_name>``
        - Linux：``$XDG_CONFIG_HOME/<app_name>`` 或 ``~/.config/<app_name>``

    Args:
        relative_path: 相对路径，如 ``aa/test.cfg``。必须为非空相对路径，
            传入空串或绝对路径将抛出 ValueError。
        resolve_type: 解析类型，取值为 :class:`ResolveType` 成员或对应整数（1/2/3），
            默认为 :attr:`ResolveType.CURRENT`。
        app_name: 应用名，仅 ``resolve_type=ResolveType.APP_DATA`` 时使用，决定应用数据目录下的子目录名。
            默认为 None，此时自动取调用者顶级包名（:func:`pykunlun.envinfo.pkginfo.get_caller_top_package_name`）。

    Returns:
        规范化后的绝对路径字符串。

    Raises:
        ValueError: relative_path 为空或绝对路径，或 resolve_type 取值非法时抛出。

    Examples:
        >>> resolve_relative("aa/test.cfg", ResolveType.USER)
        '/home/user/aa/test.cfg'
        >>> resolve_relative("config.ini", ResolveType.APP_DATA, app_name="myapp")
        'C:\\\\Users\\\\xxx\\\\AppData\\\\Roaming\\\\myapp\\\\config.ini'
    """
    # 拒绝空路径：空串经 os.path.join 会被规约为基准目录本身（返回目录而非文件路径），语义错误
    if not relative_path:
        raise ValueError("relative_path 不能为空")
    # 仅支持相对路径
    if os.path.isabs(relative_path):
        raise ValueError(f"仅支持相对路径，传入的为绝对路径: {relative_path}")

    # 校验并归一化解析类型，非法值给出可选范围
    try:
        rtype = ResolveType(resolve_type)
    except ValueError:
        valid = ", ".join(f"{t.value}({t.name})" for t in ResolveType)
        raise ValueError(f"不支持的解析类型: {resolve_type}，可选值: {valid}")

    # 查表得到基准目录，拼接并规范化
    base_dir = _BASE_RESOLVERS[rtype](app_name)
    return os.path.normpath(os.path.join(base_dir, relative_path))


# endregion

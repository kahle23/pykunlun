"""
路径工具模块，提供两类纯路径转换能力：

  - 字符串路径的规范化与组合：:func:`normpath` / :func:`join_path` /
    :func:`sub_path` / :func:`sub_path_by_index`，以 ``/`` 为统一分隔符的
    纯字符串操作，
    不触碰文件系统（:func:`normpath` 为标准库 :func:`posixpath.normpath` 的浅层
    包装，相对与绝对路径都支持；适用于对象存储键、URL 路径、包路径等场景）；
  - 相对路径到绝对路径的解析：:func:`resolve_relative` 根据解析类型将相对路径拼接到
    对应的基准目录上，支持当前工作目录、用户主目录、应用数据目录三种基准。

"是否目录"不参与规范化：:func:`normpath` 一律剥除尾部 ``/``（posixpath 标准），
目录语义由明确知道其为目录的调用方自行表达（组合 ``normpath(p) + '/'``）。

本模块仅做路径转换，不检查文件/目录是否存在。
"""

import os
import posixpath
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


# region ======== 字符串路径规范化与组合（纯字符串，不触碰文件系统） ========
def normpath(path: str) -> str:
    """
    把路径规范化为标准形式，相对路径与绝对路径都支持（纯字符串操作，不触碰文件系统）。

    :func:`posixpath.normpath` 的浅层包装：消解工作全部委托标准库
    （平台无关的 ``/`` 风格路径），仅做两点域归一：

      - ``\\`` 归一为 ``/``（容忍 Windows 风格入参；POSIX 中 ``\\`` 本是合法
        文件名字符，不归一会被 normpath 当作普通字符保留）——调用方自行调
        posixpath 就得自己处理这一步；
      - 空串与空白串拒绝（posixpath 会静默返回 ``.``，本模块显式报错）。

    其余语义与 posixpath 一致：``..`` 弹出上一段（``a/b/../c`` → ``a/c``；
    绝对路径下到根即止，``/../a`` → ``/a``），无法消解时保留（``../a`` →
    ``../a``）；消解后为空归一 ``.``（``a/..`` → ``.``）；尾部 ``/`` 剥除
    （``a/b/c/`` → ``a/b/c``，posixpath 标准——是否目录不由规范化表达，
    需要目录形态时由调用方组合 ``normpath(p) + '/'``）；绝对形态保留
    （``/a//b`` → ``/a/b``）。

    Args:
        path: 原始路径，如 ``a\\b//c/../d``、``/a//b``。

    Returns:
        规范化后的路径；可能是 ``.``、``..`` 开头的相对路径，或 ``/`` 开头的绝对路径。

    Raises:
        ValueError: 路径为空时抛出。

    Examples:
        >>> normpath('a\\\\b//./c/')
        'a/b/c'
        >>> normpath('/a//b/../c')
        '/a/c'
        >>> normpath('a/b/../c')
        'a/c'
        >>> normpath('a/..')
        '.'
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("路径不能为空")
    # 消解委托 posixpath.normpath；唯一前置归一：反斜杠统一（容忍 Windows 风格入参）
    return posixpath.normpath(path.replace('\\', '/'))


def join_path(*parts: str | None) -> str:
    """
    以 ``/`` 合并多个路径段并整体规范化（纯字符串操作，不触碰文件系统；
    语义与平台相关的 :func:`os.path.join` 不同）。

    空段（``None``、空白串）直接跳过；全部为空时返回空串。
    合并后经 :func:`normpath` 整体规范化：首段为绝对路径（``/`` 开头）时
    结果保留绝对形态（``join_path('/a', 'b')`` → ``/a/b``）；后续段的
    开头 ``/`` 只作重复分隔符消解，**不会**像 :func:`os.path.join` 那样
    重置前缀（``join_path('a', '/b')`` → ``a/b``）；段间衔接出的 ``..``
    会消解前段（``join_path('a/b', '../c')`` → ``a/c``）。

    Args:
        parts: 路径段（容忍 ``None``/空白段，直接跳过），如 ``('a/', 'b', 'c.txt')``。

    Returns:
        合并并规范化后的路径，如 ``a/b/c.txt``；无有效段时为空串。

    Examples:
        >>> join_path('a/', '/b//', 'c.txt')
        'a/b/c.txt'
        >>> join_path('/a', 'b')
        '/a/b'
        >>> join_path('a/b', '../c')
        'a/c'
    """
    valid = [part for part in parts if part and part.strip()]
    if not valid:
        return ''
    return normpath('/'.join(valid))


def sub_path(path: str, prefix: str) -> str:
    """
    以 ``prefix`` 为基准剥除 ``path`` 的对应前缀，返回剩余的相对子路径
    （宽容版：不构成前缀关系时不抛异常、原样返回）。

    ``prefix`` 是基准目录前缀而非文件系统根：纯字符串前缀匹配，绝对、
    相对形态均可，但须与 ``path`` 同域（同绝对或同相对）；大小写敏感
    （POSIX 键/URL 语义）。剥后缀、中段等按段截取需求见
    :func:`sub_path_by_index`。

    Args:
        path: 目标路径（不做规范化，由调用方保证与基准同域）。
        prefix: 基准目录前缀；内部组合 ``normpath(prefix) + '/'`` 归一为目录形态
            （空值表示不剥除）。

    Returns:
        剥除基准前缀后的相对子路径；``path`` 与 ``prefix`` 相同时为空串；
        不以 ``prefix`` 开头时原样返回。

    Examples:
        >>> sub_path('a/b/c/d.txt', 'a/b')
        'c/d.txt'
        >>> sub_path('a/b', 'a/b')
        ''
        >>> sub_path('x/y.txt', 'a/b')
        'x/y.txt'
    """
    normalized_prefix = ''
    if prefix and prefix.strip():
        normalized_prefix = normpath(prefix) + '/'
    if not normalized_prefix:
        return path
    if path.startswith(normalized_prefix):
        return path[len(normalized_prefix):]
    if path == normalized_prefix.rstrip('/'):
        return ''
    return path


def sub_path_by_index(path: str, from_index: int, to_index: int) -> str:
    """
    按 ``[from_index, to_index)`` 段区间截取路径的若干段。

    段索引基于 :func:`normpath` 规范化后的路径，**根不计段**：绝对路径
    ``/a/b`` 的段为 ``['a', 'b']``，结果为相对形态。负索引从尾部回数
    （``-1`` 为最后一段）；越界收敛到 ``[0, 段数]``；
    ``to_index < from_index`` 时二者交换；无段可取返回空串。

    Args:
        path: 原始路径，先经 :func:`normpath` 规范化。
        from_index: 起始段索引（含）。
        to_index: 结束段索引（不含）。

    Returns:
        截取出的路径（若干段以 ``/`` 连接）；无段可取时为空串。

    Examples:
        >>> sub_path_by_index('a/b/c/d', 1, 3)
        'b/c'
        >>> sub_path_by_index('a/b/c', -1, 3)
        'c'
        >>> sub_path_by_index('/a/b', 0, 2)
        'a/b'
    """
    cleaned = normpath(path).lstrip('/')  # 根不计段（对齐 Java Path.subpath）
    segments = cleaned.split('/') if cleaned else []
    length = len(segments)
    if from_index < 0:
        from_index = max(length + from_index, 0)
    elif from_index > length:
        from_index = length
    if to_index < 0:
        to_index = max(length + to_index, 0)
    elif to_index > length:
        to_index = length
    if to_index < from_index:
        from_index, to_index = to_index, from_index
    return '/'.join(segments[from_index:to_index])
# endregion

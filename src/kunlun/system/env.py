"""
环境检测模块，提供运行时环境信息获取功能。

支持获取 Python 解释器路径、当前模块包名、调用者模块名等运行时信息，
常用于日志标记、包管理和环境适配场景。

注意：环境变量的读写与 PATH 管理能力已迁移至 :mod:`baibao.system.env_var`，
本模块仅保留平台信息、Python 解释器信息与模块/包信息查询能力。
"""

import inspect
import os
import platform
import sys
import sysconfig
from functools import lru_cache
from importlib.metadata import (
    PackageNotFoundError,  # noqa: F401  透出给调用方捕获
    version,
)
from typing import Callable, List, Optional

# 本库顶级包名：用于自身识别（如 python -m kunlun 场景），避免在多处硬编码 "kunlun"
_PACKAGE_NAME = __name__.split('.')[0]


# region ======== 平台信息 ========

def get_os_name() -> str:
    """
    获取操作系统名称（小写）。

    常见返回值：windows、linux、darwin（macOS）、freebsd 等。

    Returns:
        操作系统名称（已转为小写）。
    """
    return platform.system().lower()


def is_windows() -> bool:
    """
    判断当前是否运行在 Windows 系统。
    """
    return get_os_name() == "windows"


def is_macos() -> bool:
    """
    判断当前是否运行在 macOS 系统。
    """
    return get_os_name() == "darwin"


def is_linux() -> bool:
    """
    判断当前是否运行在 Linux 系统。
    """
    return get_os_name() == "linux"


def get_os_arch() -> str:
    """
    获取操作系统机器架构（小写）。

    常见返回值：amd64/x86_64、arm64/aarch64、i386/i686 等。

    Returns:
        机器架构名称（已转为小写）。
    """
    return platform.machine().lower()


def get_os_release() -> str:
    """
    获取操作系统发行版本号。

    常见返回值：2.2.0、NT 等。

    Returns:
        操作系统发行版本字符串。
    """
    return platform.release()


def get_user_home() -> str:
    """
    获取当前用户的主目录路径。

    跨平台：依据 ``HOME``（Unix）或 ``USERPROFILE``（Windows）解析。

    Returns:
        用户主目录路径。
    """
    return os.path.expanduser("~")


def get_app_home(app_name: Optional[str] = None) -> str:
    """
    获取应用的数据基础目录（跨平台）。

    返回应用存放配置、数据等文件的标准目录，遵循各操作系统的约定。
    路径末尾已包含 app_name 子目录，调用方直接在其下拼接相对路径即可。

    - Windows：``%APPDATA%/<app_name>``，APPDATA 缺失时回退到 ``~/AppData/Roaming``
    - macOS：``~/Library/Application Support/<app_name>``
    - Linux/Unix：``$XDG_CONFIG_HOME/<app_name>``，XDG 未设置时回退到 ``~/.config/<app_name>``

    兜底逻辑：当 app_name 为空时，返回应用数据的基础目录（不拼接 app_name）；
    当各平台特定目录均无法确定时，回退到 ``~/.appdata/<app_name>``。

    Args:
        app_name: 应用名，作为数据目录下的子目录名。为空时返回应用数据的基础目录。

    Returns:
        应用数据目录的绝对路径。

    Examples:
        >>> get_app_home("myapp")  # Windows
        'C:\\\\Users\\\\xxx\\\\AppData\\\\Roaming\\\\myapp'
        >>> get_app_home()  # Windows，无 app_name
        'C:\\\\Users\\\\xxx\\\\AppData\\\\Roaming'
    """
    # 确定平台特定的基础目录
    if is_windows():
        base = os.environ.get("APPDATA") or os.path.join(get_user_home(), "AppData", "Roaming")
    elif is_macos():
        base = os.path.join(get_user_home(), "Library", "Application Support")
    else:
        # Linux/Unix: XDG Base Directory Specification
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(get_user_home(), ".config")
    # 兜底：如果基础目录为空或不存在，回退到 ~/.appdata
    if not base or not os.path.isdir(base):
        base = os.path.join(get_user_home(), ".appdata")
    # app_name 为空时返回基础目录
    if not app_name:
        return base
    # 拼接 app_name 子目录
    return os.path.join(base, app_name)


def get_shell_profile_path(
    resolver: Optional[Callable[[str, str], Optional[str]]] = None
) -> Optional[str]:
    """
    获取当前用户的 shell 配置文件路径。

    Windows 无 shell 配置文件概念，直接返回 None。
    Unix 下依据 ``SHELL`` 环境变量推断对应 rc 文件：bash、zsh、fish、csh、
    tcsh、ksh 各自的配置文件；sh 及未知 shell 兜底走 POSIX ``.profile``。

    Args:
        resolver: 自定义 shell 解析器，签名 ``(shell_name, home) -> Optional[str]``。
            优先于内置映射调用，返回非 None 则直接采用；返回 None 则回退到内置映射，
            可用于补充自定义 shell（如 nushell）或覆盖默认路径。
            需要平台判断时可在内部自行调用 ``is_macos()``。

    Returns:
        shell 配置文件路径；Windows 下返回 None。
    """
    # Windows 无 shell 配置文件概念
    if is_windows():
        return None
    # 平台标记：macOS 与 Linux 的 rc 文件习惯不同
    macos = is_macos()
    # 未设置 SHELL 时按平台给默认值（macOS 默认 zsh，Linux 默认 bash）
    shell_path = os.environ.get("SHELL", "/bin/zsh" if macos else "/bin/bash")
    # 提取 shell 名并小写，便于精确匹配
    shell_name = os.path.basename(shell_path).lower()
    # 用户主目录
    user_home = get_user_home()
    # 优先调用自定义解析器，命中则直接采用（可补充或覆盖内置映射）
    if resolver is not None:
        custom_path = resolver(shell_name, user_home)
        if custom_path is not None:
            return custom_path
    # 按 shell 名返回对应 rc 文件路径
    if shell_name == "bash":
        # macOS 习惯用 .bash_profile，Linux 习惯用 .bashrc
        return os.path.join(user_home, ".bash_profile" if macos else ".bashrc")
    if shell_name == "zsh":
        # zsh 主配置
        return os.path.join(user_home, ".zshrc")
    if shell_name == "fish":
        # fish 采用 XDG 目录结构
        return os.path.join(user_home, ".config", "fish", "config.fish")
    if shell_name == "csh":
        # C shell 配置
        return os.path.join(user_home, ".cshrc")
    if shell_name == "tcsh":
        # TENEX C shell 配置
        return os.path.join(user_home, ".tcshrc")
    if shell_name == "ksh":
        # KornShell 配置
        return os.path.join(user_home, ".kshrc")
    # sh / dash / 未知 shell：兜底走 POSIX .profile
    return os.path.join(user_home, ".profile")


# endregion


# region ======== Python 解释器 ========

def get_python_executable() -> str:
    """
    获取当前 Python 解释器的可执行文件路径。

    ``sys.executable`` 是 Python 标准库推荐的运行时解释器路径，
    准确性高于 ``sysconfig.get_config_var('EXE')``（仅编译期路径）。
    """
    return sys.executable


def get_python_home() -> str:
    """
    获取 Python 安装根目录（对齐 JAVA_HOME 语义）。

    返回 ``sys.prefix``：venv 下为虚拟环境根目录、系统级安装下为
    ``/usr``、Windows 下为 ``C:\\PythonXXX``、macOS framework build 下为
    ``.../Versions/X.Y``。

    注意：该路径下通常不直接包含 ``python`` 可执行文件，
    解释器实际位于 ``get_python_bin_dir()`` 返回的 Scripts/bin 目录中。

    Returns:
        Python 安装根目录路径。
    """
    # return os.path.dirname(get_python_executable())
    return sys.prefix


def get_python_bin_dir() -> str:
    """
    获取 Python 可执行文件目录（Scripts/bin 目录）。

    使用标准库 ``sysconfig`` 解析，自动适配 venv、conda、Homebrew、
    macOS framework build、Cygwin/MSYS2 等各种安装方式与平台。

    Returns:
        Python 可执行文件目录路径。
    """
    return sysconfig.get_path("scripts")


# endregion


# region ======== 模块与包信息 ========

def get_own_top_package_name() -> str:
    """
    获取本库（kunlun）的顶级包名。

    用于 ``python -m kunlun`` 等场景下识别自身包名，供版本查询、日志标记、
    配置目录等模块统一引用，避免在代码中硬编码 ``"kunlun"``。

    注意：返回的是 kunlun 自身的包名。上层库（如 baibao）若需要自身的包名，
    请直接使用字面量或自行维护，不要调用本函数。

    Returns:
        本库的顶级包名。
    """
    return _PACKAGE_NAME


def get_caller_top_package_name(skip_packages: Optional[List[str]] = None) -> str:
    """
    获取调用方的顶级包名（跳过库内部调用）。

    典型场景：外部应用 A 引入某库后，在库内部识别出 A 的包名；
    也兼容 ``python -m A`` 启动方式。遍历调用栈找到第一个顶级包名不在
    跳过集合中的帧；若整条调用栈都在库内部（如库自身的脚本/测试），
    则回退到最近遇到的库包名，再否则回退到本库包名。

    本函数已自动跳过 kunlun 自身。当 kunlun 作为底层依赖被其它库封装时，
    调用方应将自身（及中间层）的包名通过 ``skip_packages`` 传入，以便穿透
    这些库找到真正的应用调用方。

    边界说明：REPL、``python -c``、``__main__`` 等场景下 ``__package__``
    可能为空，会被跳过继续向上查找，找不到外部调用方时回退到库包名。

    Args:
        skip_packages: 需视为"库内部"而一并跳过的额外顶级包名列表。
            传入完整包名（如 ``"baibao"``）或带点的包名（如 ``"baibao.base"``）
            均可，内部统一取顶级包名比较。

    Returns:
        调用方的顶级包名；找不到外部调用方时回退到最近的库包名。

    Examples:
        >>> # kunlun 内部直接调用
        >>> get_caller_top_package_name()
        >>> # baibao 调用，需穿透 baibao 找到其上层应用
        >>> get_caller_top_package_name(["baibao"])
    """
    # 跳过集合：默认含本库（kunlun）自身，合并调用方传入的额外库包名
    skip = {_PACKAGE_NAME}
    if skip_packages:
        skip.update(str(p).split('.')[0] for p in skip_packages)
    # 记录遍历中最近遇到的"库"包名（非本库），作为找不到外部调用方时的回退值。
    # 例如 baibao 调用本函数且整条栈都在 baibao/kunlun 内时，回退到 "baibao"。
    fallback = _PACKAGE_NAME
    # 从当前帧的上一帧（调用方）开始向上遍历；用 f_back 链避免 inspect.stack
    # 抓取每帧源码上下文的开销，也避免持有整个栈的帧引用
    frame = inspect.currentframe()
    frame = frame.f_back if frame is not None else None
    while frame is not None:
        pkg = frame.f_globals.get('__package__')
        if pkg:
            top = pkg.split('.')[0]
            # 严格按顶级包名比较，避免前缀误判（如 baibao_ext / baibao2）
            if top not in skip:
                return str(top)
            if top != _PACKAGE_NAME:
                fallback = str(top)
        frame = frame.f_back
    return fallback


@lru_cache(maxsize=None)
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

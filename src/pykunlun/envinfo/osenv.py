"""
操作系统环境（OS environment）信息模块。

提供操作系统平台识别（os/arch/release）、跨平台用户主目录与应用数据目录解析、
shell 配置文件路径推断等运行时环境探测能力，作为只读的环境信息基础。

仅依赖 Python 标准库。
"""

import os
import platform
from collections.abc import Callable

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


def get_app_home(app_name: str | None = None) -> str:
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
    resolver: Callable[[str, str], str | None] | None = None
) -> str | None:
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

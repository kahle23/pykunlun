"""
Python 解释器信息模块。

提供当前 Python 解释器路径、安装根目录、可执行文件目录（Scripts/bin）的查询能力，
自动适配 venv、conda、Homebrew、macOS framework build、Cygwin/MSYS2 等
各种安装方式与平台。

仅依赖 Python 标准库。
"""

import sys
import sysconfig

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
    解释器实际位于 :func:`get_python_bin_dir` 返回的 Scripts/bin 目录中。

    Returns:
        Python 安装根目录路径。
    """
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

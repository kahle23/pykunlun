"""
文件操作工具模块，提供目录清理与路径处理相关的工具函数。

包含三类能力：
- 目录清理：按名称或后缀匹配删除目录，可选递归，常用于清理构建产物（如 __pycache__、.egg-info）。
- 路径转换：将相对路径按解析类型转换到当前目录、用户目录、应用数据目录的绝对路径。
- 文件读取：自动判断绝对/相对路径读取文件，返回字节流或字符串。
"""

import os
import shutil
from typing import IO, Optional

from kunlun.base import log
from kunlun.system import env

# 解析类型常量，供 resolve_path 的 resolve_type 参数使用
RESOLVE_TYPE_CURRENT = 1   # 当前工作目录
RESOLVE_TYPE_USER = 2      # 用户主目录
RESOLVE_TYPE_APP_DATA = 3  # 应用数据目录


def remove_target_dirs(base_dir: str, target_name: str, recursive: bool = False) -> int:
    """
    删除指定名称的目录。

    在指定目录中查找并删除匹配名称的目录。支持递归搜索所有子目录。

    Args:
        base_dir: 搜索的根目录路径。
        target_name: 要删除的目录名（如 __pycache__、build、dist）。
        recursive: 是否递归搜索所有子目录中的匹配目录，默认为 False。

    Returns:
        成功删除的目录数量。

    Raises:
        FileNotFoundError: 当 base_dir 目录不存在时。
        PermissionError: 当没有权限删除目录时。

    Examples:
        >>> # 删除顶层的 build 目录
        >>> remove_target_dirs("/path/to/project", "build")

        >>> # 递归删除所有 __pycache__ 目录
        >>> remove_target_dirs("/path/to/project", "__pycache__", recursive=True)
    """
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"目录不存在: {base_dir}")

    removed = 0

    if recursive:
        # 递归遍历所有子目录
        for root, dirs, _ in os.walk(base_dir):
            if target_name in dirs:
                target_path = os.path.join(root, target_name)
                try:
                    shutil.rmtree(target_path)
                    log.info(f"已删除: {target_path}")
                    removed += 1
                except PermissionError as e:
                    log.warning("删除失败 (权限不足): %s - %s", target_path, e)
    else:
        # 只检查顶层目录
        target_path = os.path.join(base_dir, target_name)
        if os.path.isdir(target_path):
            try:
                shutil.rmtree(target_path)
                log.info(f"已删除: {target_path}")
                removed = 1
            except PermissionError as e:
                log.warning("删除失败 (权限不足): %s - %s", target_path, e)

    return removed


def remove_suffix_dirs(base_dir: str, suffix: str, recursive: bool = False) -> int:
    """
    删除匹配后缀的目录。

    在指定目录中查找并删除以指定后缀结尾的目录。支持递归搜索所有子目录。

    Args:
        base_dir: 搜索的根目录路径。
        suffix: 目录名后缀（如 .egg-info、.dist-info）。
        recursive: 是否递归搜索所有子目录中的匹配目录，默认为 False。

    Returns:
        成功删除的目录数量。

    Raises:
        FileNotFoundError: 当 base_dir 目录不存在时。
        PermissionError: 当没有权限删除目录时。

    Examples:
        >>> # 删除顶层的 .egg-info 目录
        >>> remove_suffix_dirs("/path/to/project", ".egg-info")

        >>> # 递归删除所有 .dist-info 目录
        >>> remove_suffix_dirs("/path/to/project", ".dist-info", recursive=True)
    """
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"目录不存在: {base_dir}")

    removed = 0

    if recursive:
        # 递归遍历所有子目录
        for root, dirs, _ in os.walk(base_dir):
            for dir_name in dirs[:]:  # 使用副本避免修改正在迭代的列表
                if dir_name.endswith(suffix):
                    target_path = os.path.join(root, dir_name)
                    try:
                        shutil.rmtree(target_path)
                        log.info(f"已删除: {target_path}")
                        removed += 1
                    except PermissionError as e:
                        log.warning("删除失败 (权限不足): %s - %s", target_path, e)
    else:
        # 只检查顶层目录
        for entry in os.listdir(base_dir):
            full_path = os.path.join(base_dir, entry)
            if os.path.isdir(full_path) and entry.endswith(suffix):
                    try:
                        shutil.rmtree(full_path)
                        log.info(f"已删除: {full_path}")
                        removed += 1
                    except PermissionError as e:
                        log.warning("删除失败 (权限不足): %s - %s", full_path, e)

    return removed


# region ======== 路径转换 ========

def resolve_path(relative_path: str, resolve_type: int = RESOLVE_TYPE_CURRENT,
                 app_name: Optional[str] = None) -> str:
    """
    将相对路径按解析类型转换为绝对路径。

    根据解析类型将相对路径拼接到对应的基准目录上，返回规范化后的绝对路径。
    本方法仅做路径转换，不检查文件/目录是否存在。

    三种解析类型对应的基准目录：
    - RESOLVE_TYPE_CURRENT  (1)：当前工作目录（``os.getcwd()``）
    - RESOLVE_TYPE_USER     (2)：用户主目录（``~``），如 ``aa/test.cfg`` → ``/home/user/aa/test.cfg``
    - RESOLVE_TYPE_APP_DATA (3)：应用数据目录（跨平台，见 ``env.get_app_home``）：
        - Windows：``%APPDATA%/<app_name>``
        - macOS：``~/Library/Application Support/<app_name>``
        - Linux：``$XDG_CONFIG_HOME/<app_name>`` 或 ``~/.config/<app_name>``

    Args:
        relative_path: 相对路径，如 ``aa/test.cfg``。必须为相对路径，传入绝对路径将抛出 ValueError。
        resolve_type: 解析类型，取值为 RESOLVE_TYPE_CURRENT / RESOLVE_TYPE_USER / RESOLVE_TYPE_APP_DATA，
            默认为 RESOLVE_TYPE_CURRENT。
        app_name: 应用名，仅 resolve_type=RESOLVE_TYPE_APP_DATA 时使用，决定应用数据目录下的子目录名。
            默认为 None，此时自动取调用者顶级包名（``env.get_caller_top_package_name()``）。

    Returns:
        规范化后的绝对路径字符串。

    Raises:
        ValueError: relative_path 为绝对路径，或 resolve_type 取值非法时抛出。

    Examples:
        >>> resolve_path("aa/test.cfg", RESOLVE_TYPE_USER)
        '/home/user/aa/test.cfg'
        >>> resolve_path("config.ini", RESOLVE_TYPE_APP_DATA, app_name="myapp")
        'C:\\\\Users\\\\xxx\\\\AppData\\\\Roaming\\\\myapp\\\\config.ini'
    """
    # 仅支持相对路径
    if os.path.isabs(relative_path):
        raise ValueError(f"仅支持相对路径，传入的为绝对路径: {relative_path}")
    # 按解析类型选择基准目录
    if resolve_type == RESOLVE_TYPE_CURRENT:
        base_dir = os.getcwd()
    elif resolve_type == RESOLVE_TYPE_USER:
        base_dir = env.get_user_home()
    elif resolve_type == RESOLVE_TYPE_APP_DATA:
        app = app_name if app_name else env.get_caller_top_package_name()
        base_dir = env.get_app_home(app)
    else:
        raise ValueError(f"不支持的解析类型: {resolve_type}，可选值: "
                         f"{RESOLVE_TYPE_CURRENT}(当前目录)/"
                         f"{RESOLVE_TYPE_USER}(用户目录)/"
                         f"{RESOLVE_TYPE_APP_DATA}(应用数据目录)")
    # 拼接并规范化路径
    return os.path.normpath(os.path.join(base_dir, relative_path))


# endregion


# region ======== 文件读取 ========

def read_file_stream(file_path: str) -> IO[bytes]:
    """
    读取文件并返回字节 IO 流。

    自动判断路径类型：
    - 绝对路径：直接打开读取
    - 相对路径：相对当前工作目录解析（等价于 PATH_TYPE_CURRENT）后打开

    文件不存在时直接抛出 FileNotFoundError，不做静默处理。

    注意：返回的文件句柄由调用方负责关闭（推荐配合 ``with`` 语句使用）。

    Args:
        file_path: 文件路径，可为绝对路径或相对路径。

    Returns:
        以二进制读模式 ('rb') 打开的文件对象。

    Raises:
        FileNotFoundError: 文件不存在时抛出。

    Examples:
        >>> # 绝对路径直接读取
        >>> with read_file_stream("/etc/hosts") as f:
        ...     data = f.read()
        >>> # 相对路径基于当前目录解析
        >>> with read_file_stream("config.ini") as f:
        ...     data = f.read()
    """
    # 相对路径基于当前目录解析
    if not os.path.isabs(file_path):
        file_path = resolve_path(file_path, RESOLVE_TYPE_CURRENT)
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    return open(file_path, "rb")


def read_file_text(file_path: str, encoding: str = "utf-8") -> str:
    """
    读取文件并以字符串形式返回全部内容。

    read_file_stream 的便捷包装，内部读取字节流后按指定编码解码为字符串，
    并自动关闭文件句柄。

    Args:
        file_path: 文件路径，可为绝对路径或相对路径（相对当前目录）。
        encoding: 文本编码，默认为 utf-8。

    Returns:
        文件的字符串内容。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        UnicodeDecodeError: 按指定编码解码失败时抛出。

    Examples:
        >>> content = read_file_text("/etc/hosts")
        >>> content = read_file_text("config.ini")
    """
    with read_file_stream(file_path) as f:
        return f.read().decode(encoding)


# endregion

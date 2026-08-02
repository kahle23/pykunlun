"""
文件操作工具模块，提供目录清理与文件读取相关的工具函数。

包含两类能力：
- 目录清理：按名称或后缀匹配删除目录，可选递归，常用于清理构建产物（如 __pycache__、.egg-info）。
- 文件读取：自动判断绝对/相对路径读取文件，返回字节流或字符串。

路径解析能力（resolve_relative）见 :mod:`pykunlun.util.pathutil`。
"""

import os
import shutil
from typing import IO

from pykunlun.util import logutil, pathutil

log = logutil.getLogger(__name__)


# region ======== 目录清理 ========

def _safe_rmtree(path: str) -> bool:
    """
    删除目录，失败时仅告警、不抛出。

    :func:`shutil.rmtree` 的安全包装：捕获删除过程中的 ``OSError``（权限不足、文件被占用、
    路径过长、只读文件系统等），统一记日志后返回是否成功。这样调用方可在"尽力清理"语义下
    逐个处理多个目录，不因单个失败中断整体流程。

    Args:
        path: 待删除目录的绝对路径。

    Returns:
        删除成功返回 True，失败（捕获到 OSError）返回 False。
    """
    try:
        shutil.rmtree(path)
        log.info("已删除: %s", path)
        return True
    except OSError as e:
        log.warning("删除失败: %s - %s", path, e)
        return False


def remove_target_dirs(base_dir: str, target_name: str, recursive: bool = False) -> int:
    """
    删除指定名称的目录。

    在指定目录中查找并删除匹配名称的目录。支持递归搜索所有子目录。

    Args:
        base_dir: 搜索的根目录路径。
        target_name: 要删除的目录名（如 __pycache__、build、dist）。
        recursive: 是否递归搜索所有子目录中的匹配目录，默认为 False。

    Returns:
        成功删除的目录数量。单个目录删除失败（权限/占用等 OSError）仅告警，不计入返回值，不影响其余目录的清理。

    Raises:
        FileNotFoundError: 当 base_dir 目录不存在时。

    Examples:
        >>> # 删除顶层的 build 目录
        >>> remove_target_dirs("/path/to/project", "build")

        >>> # 递归删除所有 __pycache__ 目录
        >>> remove_target_dirs("/path/to/project", "__pycache__", recursive=True)
    """
    # 校验根目录：必须存在且为目录，否则继续无意义
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"根目录不存在: {base_dir}")

    removed = 0
    if recursive:
        # 自顶向下遍历；命中后从 dirs 剔除，避免 os.walk 下探已删除的目录
        for root, dirs, _ in os.walk(base_dir):
            if target_name not in dirs:
                continue
            dirs.remove(target_name)
            if _safe_rmtree(os.path.join(root, target_name)):
                removed += 1
    else:
        # 非递归：仅检查 base_dir 顶层
        target_path = os.path.join(base_dir, target_name)
        if os.path.isdir(target_path) and _safe_rmtree(target_path):
            removed = 1

    return removed


def remove_suffix_dirs(base_dir: str, suffix: str, recursive: bool = False) -> int:
    """
    删除匹配后缀的目录。

    在指定目录中查找并删除以指定后缀结尾的目录。支持递归搜索所有子目录。

    Args:
        base_dir: 搜索的根目录路径。
        suffix: 目录名后缀（如 .egg-info、.dist-info），不能为空（空后缀会匹配所有目录）。
        recursive: 是否递归搜索所有子目录中的匹配目录，默认为 False。

    Returns:
        成功删除的目录数量。单个目录删除失败（权限/占用等 OSError）仅告警，不计入返回值，不影响其余目录的清理。

    Raises:
        FileNotFoundError: 当 base_dir 目录不存在时。
        ValueError: 当 suffix 为空字符串时。

    Examples:
        >>> # 删除顶层的 .egg-info 目录
        >>> remove_suffix_dirs("/path/to/project", ".egg-info")

        >>> # 递归删除所有 .dist-info 目录
        >>> remove_suffix_dirs("/path/to/project", ".dist-info", recursive=True)
    """
    # 空后缀会使 str.endswith("") 恒为 True，误删所有目录，属于明显误用，直接拦截
    if not suffix:
        raise ValueError("suffix 不能为空（空后缀会匹配所有目录）")
    # 校验根目录：必须存在且为目录，否则继续无意义
    if not os.path.isdir(base_dir):
        raise FileNotFoundError(f"根目录不存在: {base_dir}")

    removed = 0
    if recursive:
        # 自顶向下遍历；命中后从 dirs 剔除，避免 os.walk 下探已删除的目录
        for root, dirs, _ in os.walk(base_dir):
            # 取副本遍历，删除时不影响原 dirs 列表，再用 remove 同步剔除以防下探
            for dir_name in [d for d in dirs if d.endswith(suffix)]:
                dirs.remove(dir_name)
                if _safe_rmtree(os.path.join(root, dir_name)):
                    removed += 1
    else:
        # 非递归：os.scandir 一次读取目录流，entry.is_dir/entry.name/entry.path 均复用其缓存，避免对每个条目单独 stat
        with os.scandir(base_dir) as entries:
            for entry in entries:
                if entry.is_dir() and entry.name.endswith(suffix) and _safe_rmtree(entry.path):
                    removed += 1

    return removed


# endregion


# region ======== 文件读取 ========

def read_stream(file_path: str) -> IO[bytes]:
    """
    读取文件并返回字节 IO 流。

    自动判断路径类型：
    - 绝对路径：直接打开读取
    - 相对路径：相对当前工作目录解析（等价于 ResolveType.CURRENT）后打开

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
        >>> with read_stream("/etc/hosts") as f:
        ...     data = f.read()
        >>> # 相对路径基于当前目录解析
        >>> with read_stream("config.ini") as f:
        ...     data = f.read()
    """
    # 相对路径基于当前工作目录解析；绝对路径原样使用（pathutil.resolve_relative 仅接受相对路径）
    if not os.path.isabs(file_path):
        file_path = pathutil.resolve_relative(file_path, pathutil.ResolveType.CURRENT)
    # 校验文件存在：区分"不存在/是目录"等异常情形，给出明确错误而非交由 open 抛模糊异常
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")
    # 以二进制读模式打开，文件句柄交由调用方关闭（推荐配合 with 使用）
    return open(file_path, "rb")


def read_text(file_path: str, encoding: str = "utf-8") -> str:
    """
    读取文件并以字符串形式返回全部内容。

    read_stream 的便捷包装，内部读取字节流后按指定编码解码为字符串，并自动关闭文件句柄。

    Args:
        file_path: 文件路径，可为绝对路径或相对路径（相对当前目录）。
        encoding: 文本编码，默认为 utf-8。

    Returns:
        文件的字符串内容。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        UnicodeDecodeError: 按指定编码解码失败时抛出。

    Examples:
        >>> content = read_text("/etc/hosts")
        >>> content = read_text("config.ini")
    """
    with read_stream(file_path) as f:
        return f.read().decode(encoding)


# endregion

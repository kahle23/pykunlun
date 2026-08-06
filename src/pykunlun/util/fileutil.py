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
from pykunlun.util.pathutil import ResolveType

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

def read_stream(file_path: str, *,
                search_dirs: list[ResolveType] | None = None) -> IO[bytes]:
    """
    读取文件并返回字节 IO 流。

    自动判断路径类型并按 ``search_dirs`` 顺序解析：
    - 绝对路径：直接打开读取（忽略 ``search_dirs``）
    - 相对路径：按 ``search_dirs`` 顺序逐个拼接基准目录查找，首个存在的文件即打开
        - ``None``（默认）：等价于 ``[ResolveType.CURRENT]``，即仅当前工作目录
        - 显式传入：按列表顺序依次尝试；如需保留当前目录兜底，用户需自行将 ``CURRENT`` 写入列表
        - 显式传入空列表：抛出 :class:`ValueError`（相对路径至少需要一个基准目录）

    所有候选位置均未命中时抛出 :class:`FileNotFoundError`，不做静默处理。

    注意：返回的文件句柄由调用方负责关闭（推荐配合 ``with`` 语句使用）。

    Args:
        file_path: 文件路径，可为绝对路径或相对路径。
        search_dirs: 相对路径的基准目录解析顺序（仅相对路径生效，绝对路径忽略此参数）。
            ``None`` 时默认 ``[ResolveType.CURRENT]``；显式传入则原样使用，不做隐式追加。

    Returns:
        以二进制读模式 ('rb') 打开的文件对象。

    Raises:
        ValueError: 相对路径但 ``search_dirs`` 显式传入空列表时抛出。
        FileNotFoundError: 所有候选位置均未命中文件时抛出。

    Examples:
        >>> # 绝对路径直接读取
        >>> with read_stream("/etc/hosts") as f:
        ...     data = f.read()
        >>> # 相对路径默认仅当前目录
        >>> with read_stream("config.ini") as f:
        ...     data = f.read()
        >>> # 相对路径：当前目录优先，回退用户目录
        >>> with read_stream("myapp.ini",
        ...                  search_dirs=[ResolveType.CURRENT, ResolveType.USER]) as f:
        ...     data = f.read()
    """
    # search_dirs 归一化：None 默认当前目录
    if search_dirs is None:
        search_dirs = [ResolveType.CURRENT]

    # 绝对路径：单候选，忽略 search_dirs；相对路径：按 search_dirs 拼接候选列表
    if os.path.isabs(file_path):
        candidates = [file_path]
    else:
        # 显式空列表 + 相对路径：无法解析，前置拦截
        if not search_dirs:
            raise ValueError("相对路径要求 search_dirs 非空")
        candidates = [pathutil.resolve_relative(file_path, rt) for rt in search_dirs]

    # 逐个候选判断，首个存在的文件即打开
    for candidate in candidates:
        if os.path.isfile(candidate):
            return open(candidate, "rb")

    raise FileNotFoundError(f"文件不存在: {file_path}，已尝试: {', '.join(candidates)}")


def read_text(file_path: str, encoding: str = "utf-8", *,
              search_dirs: list[ResolveType] | None = None) -> str:
    """
    读取文件并以字符串形式返回全部内容。

    :func:`read_stream` 的便捷包装，内部读取字节流后按指定编码解码为字符串，并自动关闭文件句柄。
    ``search_dirs`` 参数语义同 :func:`read_stream`，原样透传。

    Args:
        file_path: 文件路径，可为绝对路径或相对路径（按 ``search_dirs`` 解析）。
        encoding: 文本编码，默认为 utf-8。
        search_dirs: 相对路径的基准目录解析顺序，详见 :func:`read_stream`。

    Returns:
        文件的字符串内容。

    Raises:
        ValueError: 相对路径但 ``search_dirs`` 显式传入空列表时抛出。
        FileNotFoundError: 所有候选位置均未命中文件时抛出。
        UnicodeDecodeError: 按指定编码解码失败时抛出。

    Examples:
        >>> content = read_text("/etc/hosts")
        >>> content = read_text("config.ini")
        >>> content = read_text("myapp.ini",
        ...                     search_dirs=[ResolveType.CURRENT, ResolveType.USER])
    """
    with read_stream(file_path, search_dirs=search_dirs) as f:
        return f.read().decode(encoding)


# endregion


# region ======== 文件名生成（暂未启用，待有调用方再放开；启用时需恢复顶部 `from datetime import datetime`） ========

# def timestamped_filename(name_parts: list[str], ext: str) -> str:
#     """
#     生成带时间戳的文件名：``{p1}_{p2}_..._{timestamp}.{ext}``。
#
#     名称片段按顺序用下划线拼接，末尾追加形如 ``%Y%m%d_%H%M%S`` 的时间戳，最后加上扩展名。
#     不对 ``name_parts`` 做任何路径剥离或规范化——调用方负责预处理（如对路径型标识
#     先取 basename、去扩展名）。
#
#     Args:
#         name_parts: 名称片段列表（按顺序用 ``_`` 拼接）。
#         ext: 扩展名（不含点，如 ``'sql'`` 或 ``'sql.gz'``）。
#
#     Returns:
#         形如 ``{p1}_{p2}_..._{timestamp}.{ext}`` 的文件名。
#
#     Examples:
#         >>> # 形如 mysql_mydb_20240101_120000.sql.gz
#         >>> timestamped_filename(['mysql', 'mydb'], 'sql.gz')
#     """
#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#     return f"{'_'.join([*name_parts, timestamp])}.{ext}"

# endregion

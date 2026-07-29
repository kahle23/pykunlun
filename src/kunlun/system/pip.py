"""
pip 工具，支持多镜像站点自动切换。

按优先级依次尝试多个 PyPI 镜像站点安装 Python 包，
当首选镜像不可用时自动切换到下一个镜像，
提高在国内网络环境下安装包的可靠性和速度。
"""

import subprocess
from typing import List, Optional, Tuple, Union

from . import env

# 默认镜像站点列表，按优先级从高到低
DEFAULT_MIRRORS: List[str] = [
    'https://pypi.tuna.tsinghua.edu.cn/simple/',     # 清华大学
    'https://mirrors.aliyun.com/pypi/simple/',       # 阿里云
    'https://mirrors.ustc.edu.cn/pypi/web/simple/',  # 中科大
    'https://pypi.org/simple/',                      # 官方
]

# Python 命令，默认使用当前正在运行的 Python 解释器
_PYTHON_COMMAND: str = env.get_python_executable()


def get_python_command() -> str:
    """
    获取当前 Python 命令。
    """
    return _PYTHON_COMMAND


def set_python_command(command: str) -> None:
    """
    设置 Python 命令。

    Args:
        command: Python 命令，如 'python3' 或 'D:\\Python39\\python.exe'。
    """
    global _PYTHON_COMMAND
    _PYTHON_COMMAND = command


def execute(
    command: str,
    args: Optional[List[str]] = None,
    timeout: int = 300,
    mirrors: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """
    执行 pip 命令，支持多镜像站点自动切换。

    通过 subprocess 执行任意 pip 命令。遇到异常时记录错误并尝试下一个镜像。

    Args:
        command: pip 命令，如 'install'、'uninstall'、'list'、'show'、'freeze' 等。
        args: 参数列表，可包含包名（如 ['requests'] 或 ['requests==2.31.0']）和额外选项（如 ['--upgrade'] 或 ['-y']）。
        timeout: 每个镜像站点的超时时间（秒），默认 300 秒。
        mirrors: 镜像站点列表，若为 None 或空列表则不使用镜像。

    Returns:
        元组 (成功标志, 结果信息)。成功时包含所使用的镜像地址；失败时包含错误摘要。
    """
    # 确保 args 是列表，若为 None 则创建空列表
    if args is None:
        args = []
    # mirrors 为空时只执行一次（不使用镜像），否则依次尝试每个镜像
    mirrors_to_try = mirrors if mirrors else [None]
    # 初始化错误列表
    errors: List[str] = []
    # 构建基础命令（包含所有参数，不含镜像参数）
    base_command = [_PYTHON_COMMAND, '-m', 'pip', command] + args
    # 依次尝试每个镜像
    for mirror in mirrors_to_try:
        # 复制基础命令，避免修改原始列表
        cmd_list = base_command.copy()
        if mirror:
            host = mirror.split('//')[1].split('/')[0]
            cmd_list.extend(['-i', mirror, '--trusted-host', host])
        # 构建命令字符串
        cmd_str = ' '.join(cmd_list)
        try:
            # 执行命令
            subprocess.run(
                cmd_list,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            # 命令执行成功，返回成功标志和结果信息
            mirror_info = f" using mirror [{mirror}]" if mirror else ""
            return (True, f"Command succeeded: {cmd_str}{mirror_info}")
        except subprocess.CalledProcessError as e:
            # 命令执行失败，记录错误信息
            errors.append(f"Command failed: {cmd_str}\n{e.stderr.strip()}")
        except subprocess.TimeoutExpired as e:
            # 命令执行超时，记录错误信息
            errors.append(f"Command timeout: {cmd_str}\n{e}")
        except Exception as e:
            # 命令执行其他异常，记录错误信息
            errors.append(f"Command error: {cmd_str}\n{e}")
    # 所有镜像都失败，返回错误摘要
    error_detail = "\n".join(errors) if mirrors else errors[0]
    mirror_info = f" using {len(mirrors)} mirrors" if mirrors else ""
    return (False, f"Command failed: {cmd_str}{mirror_info}\n{error_detail}")


def install(
    packages: Union[str, List[str]],
    timeout: int = 300,
    mirrors: Optional[List[str]] = None,
) -> Union[Tuple[bool, str], Tuple[List[str], List[str]]]:
    """
    安装包，支持单个安装和批量安装，自动尝试多个镜像站点。

    Args:
        packages: 包名（可含版本号）或包名列表。
        timeout: 每个镜像站点的超时时间（秒），默认 300 秒。
        mirrors: 镜像站点列表，若为 None 则使用默认列表。

    Returns:
        - 单个包：元组 (成功标志, 结果信息)
        - 批量包：元组 (成功列表, 失败列表)，失败列表中每项格式为 "包名: 错误信息"
    """
    # 确保 mirrors 是列表，若为 None 则创建空列表
    mirrors = mirrors if mirrors else DEFAULT_MIRRORS
    # 单个包安装
    if isinstance(packages, str):
        return execute('install', [packages], timeout=timeout, mirrors=mirrors)
    # 批量包安装
    success_list: List[str] = []
    fail_list: List[str] = []
    for package in packages:
        # 执行安装命令
        # 命令执行成功，将包名添加到成功列表
        # 命令执行失败，将包名和错误信息添加到失败列表
        success, msg = execute('install', [package], timeout=timeout, mirrors=mirrors)
        if success:
            success_list.append(package)
        else:
            fail_list.append(f"{package}: {msg}")
    # 返回成功列表和失败列表
    return (success_list, fail_list)


def upgrade(
    packages: Union[str, List[str]],
    timeout: int = 300,
    mirrors: Optional[List[str]] = None,
) -> Union[Tuple[bool, str], Tuple[List[str], List[str]]]:
    """
    升级包，支持单个升级和批量升级，自动尝试多个镜像站点。

    Args:
        packages: 包名（可含版本号）或包名列表。
        timeout: 每个镜像站点的超时时间（秒），默认 300 秒。
        mirrors: 镜像站点列表，若为 None 则使用默认列表。

    Returns:
        - 单个包：元组 (成功标志, 结果信息)
        - 批量包：元组 (成功列表, 失败列表)，失败列表中每项格式为 "包名: 错误信息"
    """
    # 确保 mirrors 是列表，若为 None 则创建空列表
    mirrors = mirrors if mirrors else DEFAULT_MIRRORS
    # 单个包升级
    if isinstance(packages, str):
        return execute('install', [packages, '--upgrade'], timeout=timeout, mirrors=mirrors)
    # 批量包升级
    success_list: List[str] = []
    fail_list: List[str] = []
    for package in packages:
        # 执行升级命令
        # 命令执行成功，将包名添加到成功列表
        # 命令执行失败，将包名和错误信息添加到失败列表
        success, msg = execute('install', [package, '--upgrade'], timeout=timeout, mirrors=mirrors)
        if success:
            success_list.append(package)
        else:
            fail_list.append(f"{package}: {msg}")
    # 返回成功列表和失败列表
    return (success_list, fail_list)


def uninstall(
    packages: Union[str, List[str]],
    timeout: int = 300,
) -> Union[Tuple[bool, str], Tuple[List[str], List[str]]]:
    """
    卸载包，支持单个卸载和批量卸载。

    Args:
        packages: 包名或包名列表。
        timeout: 超时时间（秒），默认 300 秒。

    Returns:
        - 单个包：元组 (成功标志, 结果信息)
        - 批量包：元组 (成功列表, 失败列表)，失败列表中每项格式为 "包名: 错误信息"
    """
    # 单个包卸载
    if isinstance(packages, str):
        return execute('uninstall', [packages, '-y'], timeout=timeout)
    # 批量包卸载
    success_list: List[str] = []
    fail_list: List[str] = []
    for package in packages:
        # 执行卸载命令
        # 命令执行成功，将包名添加到成功列表
        # 命令执行失败，将包名和错误信息添加到失败列表
        success, msg = execute('uninstall', [package, '-y'], timeout=timeout)
        if success:
            success_list.append(package)
        else:
            fail_list.append(f"{package}: {msg}")
    # 返回成功列表和失败列表
    return (success_list, fail_list)

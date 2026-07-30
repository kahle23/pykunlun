"""
系统级能力模块。

提供环境变量管理、pip 包安装等与操作系统交互的通用底层能力，按子模块组织。

环境信息的只读探测（平台、Python 解释器、包元数据等）由
:mod:`kunlun.envinfo` 提供，不在本包职责内。
"""

from . import env_var, pip
from .env_var import EnvVarManager, EnvVarService

__all__ = [
    'EnvVarManager',
    'EnvVarService',
    'env_var',
    'pip',
]

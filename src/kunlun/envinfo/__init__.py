"""
环境信息查询模块。

提供当前运行环境的只读信息查询能力，按职责拆分为三个子模块：

  - :mod:`kunlun.envinfo.osenv`：操作系统平台识别与跨平台目录解析；
  - :mod:`kunlun.envinfo.pyinfo`：Python 解释器路径与安装目录查询；
  - :mod:`kunlun.envinfo.pkginfo`：模块/包信息与版本查询。

环境变量的读写与 PATH 管理由 :mod:`kunlun.system.env_var` 提供，不在本包职责内。
"""

from . import osenv, pkginfo, pyinfo

__all__ = [
    'osenv',
    'pkginfo',
    'pyinfo',
]

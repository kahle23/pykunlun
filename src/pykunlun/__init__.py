"""
Kunlun — 与具体业务无关的底层能力库。

承载跨平台、跨业务的通用抽象与基础设施，不依赖上层应用包。上层包按需在此之上扩展具体实现。
"""

from . import envinfo
from .core import (
    Command,
    CommandManager,
    CommandNotFoundError,
    HelpCommand,
    action,
    cli,
)
from .data import Masker, MaskManager
from .db import RdbCfg, RdbClient, RdbManager
from .envinfo import osenv, pkginfo, pyinfo
from .system import EnvVarManager, EnvVarService, env_var, pip
from .util import (
    fileutil,
    loadutil,
    logutil,
    maskutil,
    modutil,
    objutil,
    pathutil,
    timeutil,
    validation,
)
from .util.maskutil import CommandPasswordMasker, EnvMasker

# 不捕获 PackageNotFoundError：能执行到此处说明包已加载，版本缺失应报错而非静默回退
__version__ = pkginfo.get_package_version(pkginfo.get_own_top_package_name())

__all__ = [
    'Command',
    'CommandManager',
    'CommandNotFoundError',
    'CommandPasswordMasker',
    'EnvMasker',
    'EnvVarManager',
    'EnvVarService',
    'HelpCommand',
    'MaskManager',
    'Masker',
    'RdbCfg',
    'RdbClient',
    'RdbManager',
    'action',
    'cli',
    'env_var',
    'envinfo',
    'fileutil',
    'loadutil',
    'logutil',
    'maskutil',
    'modutil',
    'objutil',
    'osenv',
    'pathutil',
    'pip',
    'pkginfo',
    'pyinfo',
    'timeutil',
    'validation',
]

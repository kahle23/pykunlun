"""
对象存储子包。

``Oss`` 系列命名取自 OSS（Object Storage Service，对象存储服务）的首字母缩写。
本子包将其用作对象存储的中性泛称，不特指阿里云同名产品：阿里云 OSS、
AWS S3、腾讯云 COS 等，都只是本抽象之下的一种后端实现。

提供对象存储操作的门面抽象（:class:`OssClient`、:class:`OssManager`、:class:`OssCfg`）、
对象元信息模型（:class:`ObjectStat`），以及基于标准库的默认实现
（:class:`LocalOssClient`，本地目录存储）。各抽象的其他实现（如阿里云 OSS）
由上层包提供。

驱动与实例统一由 :class:`OssManager` 管理：client 实例走 ``name`` 索引，
所有操作均可经管理器按名称（别名）转发。

模块组织：

  - :mod:`pykunlun.oss.cfg`          — 访问配置 :class:`OssCfg`
  - :mod:`pykunlun.oss.stat`         — 对象元信息 :class:`ObjectStat`
  - :mod:`pykunlun.oss.client`       — 客户端策略抽象基类 :class:`OssClient`
  - :mod:`pykunlun.oss.manager`      — 客户端管理器 :class:`OssManager`
  - :mod:`pykunlun.oss.local_client` — 本地目录默认实现 :class:`LocalOssClient`
"""

from .cfg import OssCfg
from .client import OssClient
from .local_client import LocalOssClient
from .manager import OssManager
from .stat import ObjectStat

__all__ = [
    'LocalOssClient',
    'ObjectStat',
    'OssCfg',
    'OssClient',
    'OssManager',
]

"""
关系型数据库驱动与备份子包。

提供驱动策略抽象（:class:`RdbClient`、:class:`RdbManager`、:class:`RdbCfg`）、
只读客户端代理（:class:`RdbReadOnlyClient`），备份/恢复策略抽象（:class:`RdbBackupService`、
:class:`RdbBackupResult`），以及基于标准库的内置实现（:class:`SqliteClient`、
:class:`SqliteBackupService`）。各抽象的具体实现由上层包提供。

驱动与备份统一由 :class:`RdbManager` 管理：client 实例走 ``name`` 索引（query/execute），
备份服务走 ``db_type`` 索引、``cfg`` 作参数（dump/restore）。

模块组织：

  - :mod:`pykunlun.db.rdb.cfg`            — 连接配置 :class:`RdbCfg`
  - :mod:`pykunlun.db.rdb.client`         — 驱动策略抽象基类 :class:`RdbClient`
  - :mod:`pykunlun.db.rdb.readonly`       — 只读客户端代理 :class:`RdbReadOnlyClient`
  - :mod:`pykunlun.db.rdb.manager`        — 驱动客户端/备份管理器 :class:`RdbManager`
  - :mod:`pykunlun.db.rdb.backup`         — 备份策略抽象 :class:`RdbBackupService` 与结果 :class:`RdbBackupResult`
  - :mod:`pykunlun.db.rdb.sqlite_client`  — SQLite 内置驱动 :class:`SqliteClient`
  - :mod:`pykunlun.db.rdb.sqlite_backup`  — SQLite 内置备份 :class:`SqliteBackupService`
"""

from .backup import RdbBackupResult, RdbBackupService
from .cfg import RdbCfg
from .client import RdbClient
from .manager import RdbManager
from .readonly import RdbReadOnlyClient
from .sqlite_backup import SqliteBackupService
from .sqlite_client import SqliteClient

__all__ = [
    'RdbBackupResult',
    'RdbBackupService',
    'RdbCfg',
    'RdbClient',
    'RdbManager',
    'RdbReadOnlyClient',
    'SqliteBackupService',
    'SqliteClient',
]

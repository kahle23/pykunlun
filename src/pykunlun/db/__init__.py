"""
数据库相关抽象模块。

提供关系型数据库的驱动策略抽象、备份（转储）策略抽象与注册机制。
各抽象的具体实现由上层包提供；基于标准库的实现（如 SQLite）内置于此。

驱动与备份统一收敛在 :mod:`pykunlun.db.rdb` 子包：:class:`RdbManager` 既管 client 实例
（query/execute 走 ``name`` 索引），也管备份服务（dump/restore 走 ``cfg`` 参数）。

后续可扩展 KV、向量库等。
"""

from .rdb import (
    RdbBackupResult,
    RdbBackupService,
    RdbCfg,
    RdbClient,
    RdbManager,
    RdbReadOnlyClient,
    SqliteBackupService,
    SqliteClient,
)

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

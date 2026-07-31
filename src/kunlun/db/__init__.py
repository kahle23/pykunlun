"""
数据库相关抽象模块。

提供关系型数据库的驱动策略抽象、备份（转储）策略抽象与注册机制，按子模块组织。
各抽象的具体实现由上层包提供；基于标准库的实现（如 SQLite）内置于此。

当前包含：
  - 关系型数据库驱动（:mod:`kunlun.db.rdb`）
  - 基于标准库的内置实现（:mod:`kunlun.db.rdb_builtin`，如 SQLite）
后续可扩展 KV、向量库等。
"""

from .rdb import RdbCfg, RdbClient, RdbManager
from .rdb_builtin import SqliteClient

__all__ = [
    'RdbCfg',
    'RdbClient',
    'RdbManager',
    'SqliteClient',
]

"""
关系型数据库内置实现（基于 Python 标准库，无第三方依赖）。

随 :class:`~kunlun.db.rdb.RdbManager`
实例化自动注册，开箱即用。当前包含：

  - :class:`SqliteRdbClient`：SQLite 驱动客户端（基于 ``sqlite3``）
"""

import gzip
import os
import sqlite3
from typing import List, Optional

from kunlun import logutil

from .rdb import RdbCfg, RdbClient

log = logutil.getLogger(__name__)


# region ======== SQLite 驱动客户端 ========

class SqliteRdbClient(RdbClient):
    """
    SQLite 驱动客户端。

    SQLite 为文件型数据库，仅需 :attr:`RdbCfg.database` 指定文件路径
    （``':memory:'`` 表示内存库），无需 host/port/username/password。
    """

    def get_driver(self):
        """
        返回标准库 sqlite3 模块。
        """
        return sqlite3

    def build_connect_kwargs(self) -> dict:
        """
        构建 sqlite3 连接参数（基于绑定的 :attr:`cfg`）。

        Returns:
            包含 ``database`` 的连接参数字典。
        """
        return {'database': self.cfg.database}

    def validate_cfg(self) -> None:
        """
        SQLite 仅需 database 字段（文件路径或 ``':memory:'``）。

        覆盖基类默认实现（网络库字段校验），仅校验 database 与 db_type。

        Raises:
            ValueError: database 为空时抛出。
        """
        from kunlun import validation
        validation.check_required_fields_not_empty(self.cfg, ['database', 'db_type'], '数据库配置')

    def is_connection_open(self, connection) -> bool:
        """
        判断 sqlite3 连接是否可用。

        sqlite3 连接对象不暴露 ``open``/``closed`` 属性，且关闭后访问会抛出
        ``ProgrammingError``。此处乐观返回 True，由实际执行时的错误驱动重连
        （连接池模式下连接生命周期由连接池统一管理，本方法通常不参与判定）。

        Args:
            connection: sqlite3 连接对象。

        Returns:
            始终返回 True。
        """
        return True

# endregion


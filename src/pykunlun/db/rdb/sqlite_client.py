"""
SQLite 驱动客户端（基于 Python 标准库 ``sqlite3``，无第三方依赖）。

随 :class:`pykunlun.db.rdb.RdbManager` 实例化可注册使用，开箱即用。
"""

import os
import sqlite3
import urllib.request

from pykunlun.util import logutil

from .client import RdbClient

log = logutil.getLogger(__name__)


class SqliteClient(RdbClient):
    """
    SQLite 驱动客户端。

    SQLite 为文件型数据库，仅需 :attr:`RdbCfg.database` 指定文件路径
    （``':memory:'`` 表示内存库），无需 host/port/username/password。
    """

    db_type = 'sqlite'

    def get_driver(self):
        """
        返回标准库 sqlite3 模块。
        """
        return sqlite3

    def build_connect_kwargs(self) -> dict:
        """
        构建 sqlite3 连接参数（基于绑定的 :attr:`cfg`）。

        - 默认（``read_only=False``）：直接传 ``database`` 文件路径。
        - ``read_only=True``：用 URI ``file:<abs path>?mode=ro`` 打开，
          适用于查询其他进程正在写入的 SQLite 库（如 opencode 的会话库）——
          只读连接绝不参与写事务、不会锁库；连接不存在的库会失败而非创建。
          ``:memory:`` 内存库忽略此选项（内存库不支持只读，仍用普通连接）。

        路径处理：先转绝对路径，再用 :func:`urllib.request.pathname2url`
        转成合法的 ``file:`` URI 段（处理空格、中文、Windows 反斜杠与盘符）。

        Returns:
            连接参数字典：普通连接为 ``{'database': path}``；
            只读连接为 ``{'database': 'file:<path>?mode=ro', 'uri': True}``。
        """
        db = self.cfg.database
        if db is None:
            raise ValueError("SQLite database 路径未配置")
        if self.cfg.read_only and db != ':memory:':
            abs_path = os.path.abspath(db)
            uri = 'file:' + urllib.request.pathname2url(abs_path) + '?mode=ro'
            return {'database': uri, 'uri': True}
        return {'database': db}

    def _validate_and_prepare_cfg(self) -> None:
        """
        SQLite 仅需 database 字段（文件路径或 ``':memory:'``）。

        覆盖基类默认实现（网络库字段校验），仅校验 database；
        db_type 的一致性已由 :meth:`RdbClient.__init__` 保证。

        Raises:
            ValueError: database 为空时抛出。
        """
        from pykunlun.util import validation
        validation.check_required_fields_not_empty(self.cfg, ['database'], '数据库配置')

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

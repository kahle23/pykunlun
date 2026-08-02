"""
关系型数据库内置实现（基于 Python 标准库，无第三方依赖）。

随 :class:`~pykunlun.db.rdb.RdbManager` / :class:`~pykunlun.db.rdb_backup.RdbBackupManager`
实例化自动注册，开箱即用。当前包含：

  - :class:`SqliteClient`：SQLite 驱动客户端（基于 ``sqlite3``）
  - :class:`SqliteBackup`：SQLite 备份/恢复服务（基于 ``sqlite3.iterdump()``）
"""

import gzip
import os
import sqlite3
import urllib.request

from pykunlun.util import logutil

from .rdb import RdbCfg, RdbClient
from .rdb_backup import RdbBackup, RdbBackupResult

log = logutil.getLogger(__name__)


# region ======== SQLite 驱动客户端 ========

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

# endregion


# region ======== SQLite 备份服务 ========

class SqliteBackup(RdbBackup):
    """
    SQLite 备份/恢复服务（基于 Python 内置 sqlite3 模块）。

    使用内置 sqlite3 的 ``iterdump()`` 导出 SQL，无需安装额外的命令行工具，
    因此整体覆盖 :meth:`~pykunlun.db.rdb_backup.RdbBackup.dump` /
    :meth:`~pykunlun.db.rdb_backup.RdbBackup.restore`。
    """

    db_type = 'sqlite'
    tool_name = 'sqlite3'
    install_hint = 'Python 内置模块，无需安装'

    def is_available(self) -> bool:
        """
        Python 内置 sqlite3 模块，始终可用。
        """
        return True

    def _build_dump_command(self, cfg: RdbCfg, tables: list[str] | None = None,
                            no_data: bool = False) -> list[str]:
        # 不使用命令行，此方法仅供接口兼容
        return []

    def _build_restore_command(self, cfg: RdbCfg) -> list[str]:
        # 不使用命令行，此方法仅供接口兼容
        return []

    def dump(self, cfg: RdbCfg, output_path: str, tables: list[str] | None = None,
             no_data: bool = False, compress: bool = True, verbose: bool = False,
             timeout: int | None = None) -> RdbBackupResult:
        """
        执行 SQLite 数据库备份。

        使用 Python 内置 sqlite3 模块的 ``iterdump()`` 方法导出 SQL。
        """
        db_path = cfg.database
        if not db_path:
            return RdbBackupResult(False, error_message="SQLite 数据库文件路径未配置")

        if not os.path.exists(db_path):
            return RdbBackupResult(False, error_message=f"SQLite 数据库文件不存在: {db_path}")

        if verbose:
            log.info(f"正在备份 SQLite 数据库: {db_path}")

        try:
            conn = sqlite3.connect(db_path)

            if compress:
                temp_path = output_path + '.tmp'
                self._dump_to_file(conn, temp_path, tables, no_data)

                with open(temp_path, 'rb') as f_in, gzip.open(output_path, 'wb') as f_out:
                    f_out.writelines(f_in)
                os.remove(temp_path)
            else:
                self._dump_to_file(conn, output_path, tables, no_data)

            conn.close()

            file_size = os.path.getsize(output_path)
            return RdbBackupResult(True, output_path, file_size)

        except Exception as e:
            log.warning(f"SQLite 备份失败: {e}")
            return RdbBackupResult(False, error_message=f"SQLite 备份失败: {e}")

    def restore(self, cfg: RdbCfg, input_path: str, verbose: bool = False,
                timeout: int | None = None) -> RdbBackupResult:
        """
        从 SQL 备份文件恢复 SQLite 数据库。

        自动识别 ``.gz`` 后缀并解压，按 ``executescript`` 整体执行 SQL 文本。
        """
        db_path = cfg.database
        if not db_path:
            return RdbBackupResult(False, error_message="SQLite 数据库文件路径未配置")

        if not os.path.exists(input_path):
            return RdbBackupResult(False, error_message=f"备份文件不存在: {input_path}")

        if verbose:
            log.info(f"正在恢复 SQLite 数据库: {db_path} <- {input_path}")

        opener = gzip.open if input_path.endswith('.gz') else open
        try:
            with opener(input_path, 'rt', encoding='utf-8') as f:
                sql = f.read()

            conn = sqlite3.connect(db_path)
            conn.executescript(sql)
            conn.commit()
            conn.close()

            log.info(f"恢复成功: {input_path}")
            return RdbBackupResult(True)
        except Exception as e:
            log.warning(f"SQLite 恢复失败: {e}")
            return RdbBackupResult(False, error_message=f"SQLite 恢复失败: {e}")

    def _dump_to_file(self, conn, output_path: str,
                      tables: list[str] | None = None, no_data: bool = False) -> None:
        """
        将数据库内容导出到 SQL 文件。
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in conn.iterdump():
                # 如果指定了表，只导出指定表
                if tables and not self._is_table_line(line, tables):
                    continue

                # 如果只导出结构，跳过 INSERT 语句
                if no_data and line.strip().startswith('INSERT'):
                    continue

                f.write(line + '\n')

    @staticmethod
    def _is_table_line(line: str, tables: list[str]) -> bool:
        """
        检查 SQL 行是否属于指定的表。
        """
        for table in tables:
            # CREATE TABLE 语句
            if f'CREATE TABLE "{table}"' in line or f'CREATE TABLE {table}' in line:
                return True
            # INSERT 语句
            if f'INSERT INTO "{table}"' in line or f'INSERT INTO {table}' in line:
                return True
        return False

# endregion

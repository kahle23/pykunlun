"""
SQLite 备份/恢复服务（基于 Python 标准库 ``sqlite3``，无第三方依赖）。

使用内置 sqlite3 的 ``iterdump()`` 导出 SQL，无需安装额外的命令行工具，
随 :class:`RdbManager` 注册后可用。
"""

import gzip
import os
import sqlite3

from pykunlun.util import logutil

from .backup import RdbBackupResult, RdbBackupService
from .cfg import RdbCfg

log = logutil.getLogger(__name__)


class SqliteBackupService(RdbBackupService):
    """
    SQLite 备份/恢复服务（基于 Python 内置 sqlite3 模块）。

    使用内置 sqlite3 的 ``iterdump()`` 导出 SQL，无需安装额外的命令行工具，
    因此整体覆盖 :meth:`RdbBackupService.dump` /
    :meth:`RdbBackupService.restore`。
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
                            schema_only: bool = False) -> list[str]:
        # 不使用命令行，此方法仅供接口兼容
        return []

    def _build_restore_command(self, cfg: RdbCfg) -> list[str]:
        # 不使用命令行，此方法仅供接口兼容
        return []

    def dump(self, cfg: RdbCfg, output_path: str, tables: list[str] | None = None,
             schema_only: bool = False, compress: bool = True, verbose: bool = False,
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
                self._dump_to_file(conn, temp_path, tables, schema_only)

                with open(temp_path, 'rb') as f_in, gzip.open(output_path, 'wb') as f_out:
                    f_out.writelines(f_in)
                os.remove(temp_path)
            else:
                self._dump_to_file(conn, output_path, tables, schema_only)

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
                      tables: list[str] | None = None, schema_only: bool = False) -> None:
        """
        将数据库内容导出到 SQL 文件。
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            for line in conn.iterdump():
                # 如果指定了表，只导出指定表
                if tables and not self._is_table_line(line, tables):
                    continue

                # 如果只导出结构，跳过 INSERT 语句
                if schema_only and line.strip().startswith('INSERT'):
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

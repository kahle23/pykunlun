"""
关系型数据库备份/恢复（转储）的底层抽象模块，定义策略接口与注册表。

采用策略模式：
  - :class:`RdbBackup` 为抽象基类，定义统一的备份/恢复接口（:meth:`~RdbBackup.dump`
    / :meth:`~RdbBackup.restore`），把"因数据库而异"的命令构建收敛为可覆盖的钩子，
    把通用的执行/压缩/格式化/脱敏逻辑统一写在本类；
  - :class:`RdbBackupManager` 维护 ``db_type -> 备份服务`` 注册表，按数据库类型
    工厂化获取服务实例，并与 :mod:`pykunlun.db.rdb` 的 :class:`~pykunlun.db.rdb.RdbManager`
    风格保持一致。

本模块仅提供抽象与注册表，**不绑定任何具体实现**。各数据库的具体备份服务
（依赖外部命令行工具如 mysqldump、pg_dump，或驱动如 sqlite3）由上层包在导入时
通过 :meth:`RdbBackupManager.register` 注册。

:class:`RdbBackup` 基于 :class:`~pykunlun.db.rdb.RdbCfg` 配置执行备份/恢复，
与 :mod:`pykunlun.db.rdb` 的驱动服务共享同一份连接配置。

仅依赖 Python 标准库与 pykunlun 自身工具模块。
"""

import gzip
import os
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from typing import IO, Any, cast

from pykunlun.util import logutil, maskutil

from .rdb import RdbCfg

log = logutil.getLogger(__name__)


# region ======== 备份/恢复结果 ========

class RdbBackupResult:
    """
    备份/恢复结果。

    由 :meth:`RdbBackup.dump` / :meth:`RdbBackup.restore` 返回，封装操作是否成功、
    输出文件路径、文件大小及失败时的错误信息。恢复操作成功时 ``output_path``
    与 ``file_size`` 为空（无产出文件）。

    Attributes:
        success: 是否成功。
        output_path: 备份文件路径（成功时；恢复时为空）。
        file_size: 文件大小（字节；恢复时为 0）。
        error_message: 错误信息（失败时）。
    """

    def __init__(self, success: bool, output_path: str = '', file_size: int = 0,
                 error_message: str = ''):
        self.success = success
        self.output_path = output_path
        self.file_size = file_size
        self.error_message = error_message

    def __str__(self) -> str:
        if self.success:
            if self.output_path:
                size_str = RdbBackup.format_size(self.file_size)
                return f"备份成功: {self.output_path} ({size_str})"
            return "恢复成功"
        return f"操作失败: {self.error_message}"

# endregion


# region ======== 备份服务抽象基类 ========

class RdbBackup(ABC):
    """
    关系型数据库备份/恢复服务策略抽象基类。

    每个实例代表一种数据库的备份/恢复能力，把"因数据库而异"的差异收敛为可覆盖的
    钩子方法，把"放之四海皆准"的执行/压缩逻辑统一写在本类。新增一种数据库的
    备份 = 继承本类并覆盖少数钩子，基类的执行逻辑无需改动。

    方法分两层：

    【数据库差异钩子】—— 子类必须实现/按需覆盖
      - ``db_type``                : 数据库类型标识（如 mysql、postgresql、sqlite）。
      - ``tool_name``              : 备份工具名称（如 mysqldump、pg_dump）。
      - ``install_hint``           : 工具未安装时的提示信息。
      - ``_build_dump_command``         : 构建备份命令参数列表。
      - ``_build_restore_command`` : 构建恢复命令参数列表（通过 stdin 喂入 SQL）。
      - ``get_env``                : 执行命令所需的额外环境变量（如 PGPASSWORD），默认无。
      - ``is_available``           : 检查备份工具是否可用，默认按 ``tool_name`` 查 PATH。

      上述 ``db_type`` / ``tool_name`` / ``install_hint`` 虽在本类声明为抽象只读
      property（强制子类提供），但子类应以**类级常量**形式覆盖（如
      ``db_type = 'mysql'``），与 :class:`~pykunlun.db.rdb.RdbClient` 风格保持一致，
      避免逐个写 ``@property`` 的样板代码。

    【通用执行接口】—— 基类实现，调用方直接使用
      - ``dump``           : 备份模板方法，串联可用性检查 → 构建命令 → 执行 → 压缩。
      - ``restore``        : 恢复模板方法，串联可用性检查 → 构建命令 → 解压读入 → 执行。
      - ``generate_filename`` / ``format_size`` : 静态工具方法。

    对于不依赖命令行工具的实现（如基于 Python 内置 sqlite3 的备份），子类可
    整体覆盖 :meth:`dump` / :meth:`restore`，此时 :meth:`_build_dump_command` /
    :meth:`_build_restore_command` 可返回空列表仅供接口兼容。

    通常由上层包构造后注册到 :class:`RdbBackupManager` 按类型管理，也可直接使用::

        backup = SomeBackup()
        result = backup.dump(cfg, './backups/db.sql.gz')
        print(result)
    """

    #: 命令执行默认超时秒数（dump/restore 共用），子类可覆盖或调用时透传 timeout
    DEFAULT_TIMEOUT = 3600

    @property
    @abstractmethod
    def db_type(self) -> str:
        """
        数据库类型标识（如 mysql、postgresql、sqlite）。

        子类以类级常量形式提供，如 ``db_type = 'mysql'``。
        """
        pass

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """
        备份工具名称（如 mysqldump、pg_dump）。

        子类以类级常量形式提供。
        """
        pass

    @property
    @abstractmethod
    def install_hint(self) -> str:
        """
        工具未安装时的提示信息。

        子类以类级常量形式提供。
        """
        pass

    @abstractmethod
    def _build_dump_command(self, cfg: RdbCfg, tables: list[str] | None = None,
                       no_data: bool = False) -> list[str]:
        """
        构建备份命令参数列表。

        Args:
            cfg: 数据库配置。
            tables: 只备份指定的表。
            no_data: 只备份结构，不备份数据。

        Returns:
            命令参数列表。
        """
        pass

    @abstractmethod
    def _build_restore_command(self, cfg: RdbCfg) -> list[str]:
        """
        构建恢复命令参数列表（SQL 经 stdin 喂入）。

        恢复命令通常是另一个客户端可执行文件（如 mysqldump 对应 mysql、
        pg_dump 对应 psql），从标准输入读取 SQL 文本执行。

        Args:
            cfg: 数据库配置。

        Returns:
            命令参数列表；整体覆盖 :meth:`restore` 的实现可返回空列表仅供接口兼容。
        """
        pass

    def get_env(self, cfg: RdbCfg) -> dict[str, str] | None:
        """
        获取执行命令时需要的额外环境变量。

        用于封装各数据库传递敏感信息的方式差异（如 PostgreSQL 通过
        ``PGPASSWORD`` 环境变量传递密码，而非命令行参数）。

        Args:
            cfg: 数据库配置。

        Returns:
            环境变量字典，None 表示使用默认环境。
        """
        return None

    def is_available(self) -> bool:
        """
        检查备份工具是否可用（按 :attr:`tool_name` 在 PATH 中查找）。
        """
        return shutil.which(self.tool_name) is not None

    # region ======== 备份 ========

    def dump(self, cfg: RdbCfg, output_path: str, tables: list[str] | None = None,
             no_data: bool = False, compress: bool = True, verbose: bool = False,
             timeout: int | None = None) -> RdbBackupResult:
        """
        执行数据库备份（模板方法）。

        串联：可用性检查 → 构建命令 → （可选）打印脱敏命令/环境 → 执行并落盘。

        Args:
            cfg: 数据库配置。
            output_path: 输出文件路径。
            tables: 只备份指定的表。
            no_data: 只备份结构，不备份数据。
            compress: 是否 gzip 压缩。
            verbose: 是否显示详细输出（命令与环境变量自动脱敏）。
            timeout: 命令执行超时秒数，``None`` 用 :attr:`DEFAULT_TIMEOUT`。

        Returns:
            :class:`RdbBackupResult` 备份结果。
        """
        if not self.is_available():
            return RdbBackupResult(False, error_message=f"{self.tool_name} 未安装，{self.install_hint}")

        cmd = self._build_dump_command(cfg, tables, no_data)
        env = self.get_env(cfg)

        if verbose:
            self._log_command(cmd, env)

        return self._execute(cmd, output_path, compress, env, timeout)

    def _execute(self, cmd: list[str], output_path: str, compress: bool,
                 env: dict[str, str] | None = None,
                 timeout: int | None = None) -> RdbBackupResult:
        """
        执行备份命令并落盘（按 ``compress`` 决定是否 gzip 压缩）。

        不压缩时直接写到 ``output_path``；压缩时先写临时文件再 gzip 到 ``output_path``。

        临时文件名刻意与 ``output_path`` 区分：当 ``output_path`` 不以 ``.gz`` 结尾时，
        不能直接拿它去掉后缀当临时名（去不掉会与目标路径相同，导致边读边写同一文件、
        输出损坏）。

        清理规则（统一在 ``finally`` 中处理半成品/临时文件）：

          - 压缩模式：``raw_path`` 始终是临时文件，无论成败都删；
          - 非压缩失败：``raw_path`` 即 ``output_path`` 半成品，删除；
          - 非压缩成功：``raw_path`` 即 ``output_path`` 成果，保留。
        """
        actual_timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

        if compress:
            raw_path = output_path[:-len('.gz')] if output_path.endswith('.gz') \
                else output_path + '.tmp'
        else:
            raw_path = output_path

        success = False
        try:
            with open(raw_path, 'w', encoding='utf-8') as f:
                result = subprocess.run(
                    cmd, stdout=f, stderr=subprocess.PIPE,
                    text=True, timeout=actual_timeout, env=env, check=False,
                )

            if result.returncode != 0:
                err = result.stderr.strip()
                log.warning(f"备份命令执行失败: {err}")
                return RdbBackupResult(False, error_message=f"备份命令执行失败: {err}")

            if compress:
                with open(raw_path, 'rb') as f_in, gzip.open(output_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            success = True
        except subprocess.TimeoutExpired:
            return RdbBackupResult(False, error_message=f"备份命令执行超时（超过 {actual_timeout} 秒）")
        except FileNotFoundError:
            return RdbBackupResult(False, error_message=f"备份工具未找到: {cmd[0]}")
        except Exception as e:
            return RdbBackupResult(False, error_message=f"备份执行异常: {e}")
        finally:
            if (compress or not success) and os.path.exists(raw_path):
                os.remove(raw_path)

        file_size = os.path.getsize(output_path)
        return RdbBackupResult(True, output_path, file_size)

    # endregion

    # region ======== 恢复 ========

    def restore(self, cfg: RdbCfg, input_path: str, verbose: bool = False,
                timeout: int | None = None) -> RdbBackupResult:
        """
        从备份文件恢复数据库（模板方法）。

        串联：文件存在性检查 → 构建恢复命令 → （可选）打印脱敏命令/环境 →
        按扩展名自动解压并通过 stdin 喂入 SQL 执行。

        自动识别 ``.gz`` 后缀并以 gzip 解压读取，其余按普通文本处理。

        Args:
            cfg: 数据库配置。
            input_path: 备份文件路径。
            verbose: 是否显示详细输出（命令与环境变量自动脱敏）。
            timeout: 命令执行超时秒数，``None`` 用 :attr:`DEFAULT_TIMEOUT`。

        Returns:
            :class:`RdbBackupResult` 恢复结果（成功时 ``output_path`` 为空）。
        """
        if not os.path.exists(input_path):
            return RdbBackupResult(False, error_message=f"备份文件不存在: {input_path}")

        cmd = self._build_restore_command(cfg)
        restore_tool = cmd[0] if cmd else self.tool_name
        if not shutil.which(restore_tool):
            return RdbBackupResult(False, error_message=f"{restore_tool} 未安装，{self.install_hint}")

        env = self.get_env(cfg)
        if verbose:
            self._log_command(cmd, env)

        return self._execute_restore(cmd, input_path, env, timeout)

    def _execute_restore(self, cmd: list[str], input_path: str,
                         env: dict[str, str] | None = None,
                         timeout: int | None = None) -> RdbBackupResult:
        """
        执行恢复命令：按 ``.gz`` 后缀决定是否解压，将 SQL 流经 stdin 喂入恢复客户端。
        """
        actual_timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        opener = gzip.open if input_path.endswith('.gz') else open
        try:
            with opener(input_path, 'rb') as f_in:
                result = subprocess.run(
                    cmd, stdin=cast(IO[Any], f_in), stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, timeout=actual_timeout,
                    env=env, check=False,
                )
            if result.returncode != 0:
                err = result.stderr.decode('utf-8', errors='replace').strip()
                log.warning(f"恢复命令执行失败: {err}")
                return RdbBackupResult(False, error_message=f"恢复命令执行失败: {err}")
        except subprocess.TimeoutExpired:
            return RdbBackupResult(False, error_message=f"恢复命令执行超时（超过 {actual_timeout} 秒）")
        except FileNotFoundError:
            return RdbBackupResult(False, error_message=f"恢复工具未找到: {cmd[0]}")
        except Exception as e:
            return RdbBackupResult(False, error_message=f"恢复执行异常: {e}")

        log.info(f"恢复成功: {input_path}")
        return RdbBackupResult(True)

    # endregion

    # region ======== 日志脱敏 ========

    def _log_command(self, cmd: list[str], env: dict[str, str] | None) -> None:
        """
        打印脱敏后的命令与环境变量（用于 verbose 模式）。

        实际脱敏逻辑委托 :mod:`pykunlun.util.maskutil`：统一走 :func:`~pykunlun.util.maskutil.mask`
        ——命令（``List[str]``）由命令脱敏策略屏蔽 ``--password=`` / ``-pXXX`` 等密码参数，
        环境变量（``Dict[str, str]``）由环境变量脱敏策略屏蔽敏感键的值。
        """
        log.info(f"执行命令: {' '.join(maskutil.mask(cmd))}")
        masked_env = maskutil.mask(env)
        if masked_env:
            log.info("环境变量: " + ', '.join(f'{k}={v}' for k, v in masked_env.items()))

    # endregion

    @staticmethod
    def generate_filename(db_type: str, database: str, compress: bool) -> str:
        """
        生成备份文件名。

        对于文件路径型的数据库（如 SQLite），只取文件主名作为标识。

        Args:
            db_type: 数据库类型标识。
            database: 数据库名称或文件路径。
            compress: 是否 gzip 压缩（影响扩展名）。

        Returns:
            形如 ``{db_type}_{db_name}_{timestamp}.sql[.gz]`` 的文件名。
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        db_name = os.path.basename(database) if (os.path.sep in database or '/' in database) else database
        db_name = os.path.splitext(db_name)[0]
        filename = f"{db_type}_{db_name}_{timestamp}.sql"
        if compress:
            filename += '.gz'
        return filename

    @staticmethod
    def format_size(size: float) -> str:
        """
        格式化文件大小为带单位的可读字符串。
        """
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

# endregion


# region ======== 备份服务管理器（注册表） ========

class RdbBackupManager:
    """
    数据库备份服务管理器（按 ``db_type`` 注册 :class:`RdbBackup` 实例）。

    维护 ``db_type -> 备份服务`` 的注册表，与 :class:`~pykunlun.db.rdb.RdbManager`
    风格一致：每个数据库类型注册一个备份服务实例，通过类型标识工厂化获取。
    注册键取自服务自身的 :attr:`~RdbBackup.db_type`（自动小写归一化），
    无需调用方显式提供。

    本类额外提供 :meth:`dump` / :meth:`restore` 便捷方法，
    等价于 ``get_service(db_type).dump(...)`` / ``get_service(db_type).restore(...)``。

    用法示例::

        manager = RdbBackupManager()
        manager.register(MysqlBackup())
        manager.register(SqliteBackup())

        result = manager.dump('sqlite', cfg, './backup.sql.gz')
        types = manager.get_supported_types()
    """

    def __init__(self) -> None:
        # 类型注册表：db_type -> RdbBackup 实例（本实例独有）
        self._registry: dict[str, RdbBackup] = {}
        self._lock = threading.RLock()

    def register(self, service: RdbBackup) -> None:
        """
        注册或替换一个备份服务（按服务自身的 :attr:`~RdbBackup.db_type` 归档）。

        Args:
            service: :class:`RdbBackup` 实例。
        """
        key = service.db_type.lower()
        with self._lock:
            self._registry[key] = service

    def unregister(self, db_type: str) -> bool:
        """
        取消注册指定类型的备份服务。

        Args:
            db_type: 数据库类型标识。

        Returns:
            是否成功移除。
        """
        key = db_type.lower()
        with self._lock:
            if key in self._registry:
                del self._registry[key]
                return True
            return False

    def get_service(self, db_type: str) -> RdbBackup:
        """
        获取指定类型的备份服务。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            :class:`RdbBackup` 实例。

        Raises:
            ValueError: 不支持的数据库类型时抛出。
        """
        key = db_type.lower()
        with self._lock:
            service = self._registry.get(key)
            if service is None:
                supported = ', '.join(self._registry.keys()) or '（无）'
                raise ValueError(f"不支持的数据库类型: {db_type}，支持的类型: {supported}")
            return service

    def dump(self, db_type: str, cfg: RdbCfg, output_path: str,
             tables: list[str] | None = None, no_data: bool = False,
             compress: bool = True, verbose: bool = False,
             timeout: int | None = None) -> RdbBackupResult:
        """
        执行指定类型的备份（便捷方法，透传 :meth:`RdbBackup.dump`）。

        Args:
            db_type: 数据库类型标识。
            cfg: 数据库配置。
            output_path: 输出文件路径。
            tables: 只备份指定的表。
            no_data: 只备份结构，不备份数据。
            compress: 是否 gzip 压缩。
            verbose: 是否显示详细输出。
            timeout: 命令执行超时秒数，``None`` 用默认值。

        Returns:
            :class:`RdbBackupResult` 备份结果。
        """
        return self.get_service(db_type).dump(
            cfg, output_path, tables, no_data, compress, verbose, timeout)

    def restore(self, db_type: str, cfg: RdbCfg, input_path: str,
                verbose: bool = False, timeout: int | None = None) -> RdbBackupResult:
        """
        从备份文件恢复指定类型的数据库（便捷方法，透传 :meth:`RdbBackup.restore`）。

        Args:
            db_type: 数据库类型标识。
            cfg: 数据库配置。
            input_path: 备份文件路径。
            verbose: 是否显示详细输出。
            timeout: 命令执行超时秒数，``None`` 用默认值。

        Returns:
            :class:`RdbBackupResult` 恢复结果。
        """
        return self.get_service(db_type).restore(cfg, input_path, verbose, timeout)

    def get_supported_types(self) -> list[str]:
        """
        获取所有已注册（即支持）的数据库类型列表。

        Returns:
            数据库类型标识列表。
        """
        with self._lock:
            return list(self._registry.keys())

# endregion

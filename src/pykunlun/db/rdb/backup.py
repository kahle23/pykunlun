"""
关系型数据库备份/恢复：结果对象与策略抽象基类。

本模块把"因数据库而异"的备份/恢复差异收敛为可覆盖的钩子方法，把"放之四海皆准"的
执行/压缩逻辑统一写在 :class:`RdbBackupService` 基类。新增一种数据库的备份 = 继承本类并覆盖
少数钩子，基类的执行逻辑无需改动。

:class:`RdbBackupService` 为无状态策略服务，``cfg`` 作为 :meth:`dump` / :meth:`restore` 的
入参传入（与 :class:`RdbClient` 的 cfg-bound 风格区分），一个实例可服务任意多份
同 db_type 的配置。通常注册到 :class:`RdbManager` 按 db_type 管理。

- :class:`RdbBackupResult` — 备份/恢复结果
- :class:`RdbBackupService`    — 备份/恢复策略抽象基类
"""

import gzip
import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from typing import IO, Any, cast

from pykunlun.util import cmdutil, fmtutil, logutil

from .cfg import RdbCfg

log = logutil.getLogger(__name__)


class RdbBackupResult:
    """
    备份/恢复结果。

    由 :meth:`RdbBackupService.dump` / :meth:`RdbBackupService.restore` 返回，封装操作是否成功、
    输出文件路径、文件大小及失败时的错误信息。恢复操作成功时 ``output_path``
    与 ``file_size`` 为空（无产出文件）。

    Attributes:
        success: 是否成功。
        output_path: 备份文件路径（成功时；恢复时为空）。
        file_size: 文件大小（字节；恢复时为 0）。
        error_message: 错误信息（失败时）。
    """

    def __init__(self, success: bool, output_path: str = "", file_size: int = 0, error_message: str = ""):
        self.success = success
        self.output_path = output_path
        self.file_size = file_size
        self.error_message = error_message

    def __str__(self) -> str:
        if self.success:
            if self.output_path:
                size_str = fmtutil.format_bytes(self.file_size)
                return f"备份成功: {self.output_path} ({size_str})"
            return "恢复成功"
        return f"操作失败: {self.error_message}"


class RdbBackupService(ABC):
    """
    关系型数据库备份/恢复服务策略抽象基类。

    每个实例代表一种数据库的备份/恢复能力，把"因数据库而异"的差异收敛为可覆盖的
    钩子方法，把"放之四海皆准"的执行/压缩逻辑统一写在本类。新增一种数据库的
    备份 = 继承本类并覆盖少数钩子，基类的执行逻辑无需改动。

    本类为无状态策略服务：``cfg`` 作为 :meth:`dump` / :meth:`restore` 的**入参**传入，
    一个实例可服务任意多份同 db_type 的配置（区别于 :class:`RdbClient` 构造时绑定一份 cfg）。

    方法分两层：

    【数据库差异钩子】—— 子类必须实现/按需覆盖
      - ``db_type``                : 数据库类型标识（如 mysql、postgresql、sqlite）。
      - ``tool_name``              : 备份工具名称（如 mysqldump、pg_dump）。
      - ``install_hint``           : 工具未安装时的提示信息。
      - ``_get_env``               : 执行命令所需的额外环境变量（如 PGPASSWORD），默认无。
      - ``is_available``           : 检查备份工具是否可用，默认按 ``tool_name`` 查 PATH。
      - ``_build_dump_command``         : 构建备份命令参数列表。
      - ``_build_restore_command`` : 构建恢复命令参数列表（通过 stdin 喂入 SQL）。

      上述 ``db_type`` / ``tool_name`` / ``install_hint`` 虽在本类声明为抽象只读
      property（强制子类提供），但子类应以**类级常量**形式覆盖（如
      ``db_type = 'mysql'``），与 :class:`RdbClient` 风格保持一致，
      避免逐个写 ``@property`` 的样板代码。其运行时不可修改性由 :meth:`__setattr__`
      显式拦截保证（与 :class:`RdbClient` 同一思路）。

    【通用执行接口】—— 基类实现，调用方直接使用
      - ``dump``           : 备份模板方法，串联可用性检查 → 构建命令 → 执行 → 压缩。
      - ``restore``        : 恢复模板方法，串联可用性检查 → 构建命令 → 解压读入 → 执行。

    对于不依赖命令行工具的实现（如基于 Python 内置 sqlite3 的备份），子类可
    整体覆盖 :meth:`dump` / :meth:`restore`，此时 :meth:`_build_dump_command` /
    :meth:`_build_restore_command` 可返回空列表仅供接口兼容。

    通常由上层包构造后注册到 :class:`RdbManager` 按 db_type 管理，也可直接使用::

        backup = SomeBackup()
        result = backup.dump(cfg, './backups/db.sql.gz')
        print(result)
    """

    #: 命令执行默认超时秒数（dump/restore 共用），子类可覆盖或调用时透传 timeout
    DEFAULT_TIMEOUT = 3600

    def __setattr__(self, name: str, value: Any) -> None:
        """
        拦截实例属性赋值，保护类级常量不被运行时篡改。

        - ``db_type`` / ``tool_name`` / ``install_hint``：基类虽声明为抽象只读 property，
          但子类为满足抽象约束会用类级常量 ``db_type = 'mysql'`` 覆盖——该常量是普通字符串
          （非 data descriptor），会遮蔽基类 property，使 property 的只读保护失效，
          ``instance.xxx = y`` 将悄悄创建实例级遮蔽。本方法显式抛 :class:`AttributeError`
          堵住此缺口（与 :class:`RdbClient` 同一思路）。
        - ``DEFAULT_TIMEOUT``：虽非抽象 property（有通用默认值，子类按需在**类级**覆盖），
          但同样不应在**实例级**被遮蔽——``instance.DEFAULT_TIMEOUT = 0`` 会让所有命令立即超时。
        - 其余属性（无状态策略服务通常无额外实例属性）照常赋值。

        Raises:
            AttributeError: 尝试给实例的 ``db_type`` / ``tool_name`` / ``install_hint`` /
                ``DEFAULT_TIMEOUT`` 赋值时抛出。
        """
        if name in ('db_type', 'tool_name', 'install_hint', 'DEFAULT_TIMEOUT'):
            raise AttributeError(
                f"{type(self).__name__}.{name} 是实现类硬编码的类级常量，"
                f"禁止运行时修改。"
            )
        super().__setattr__(name, value)

    def _get_env(self, cfg: RdbCfg) -> dict[str, str] | None:
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

    def is_available(self) -> bool:
        """
        检查备份工具是否可用（按 :attr:`tool_name` 在 PATH 中查找）。
        """
        return shutil.which(self.tool_name) is not None

    # region ======== 备份 ========

    @abstractmethod
    def _build_dump_command(self, cfg: RdbCfg, tables: list[str] | None = None, schema_only: bool = False) -> list[str]:
        """
        构建备份命令参数列表。

        Args:
            cfg: 数据库配置。
            tables: 只备份指定的表。
            schema_only: 只备份结构，不备份数据。

        Returns:
            命令参数列表。
        """
        pass

    def _execute_dump(
        self,
        cmd: list[str],
        output_path: str,
        compress: bool,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> RdbBackupResult:
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
            raw_path = output_path[: -len(".gz")] if output_path.endswith(".gz") else output_path + ".tmp"
        else:
            raw_path = output_path

        success = False
        try:
            with open(raw_path, "w", encoding="utf-8") as f:
                result = subprocess.run(
                    cmd,
                    stdout=f,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=actual_timeout,
                    env=env,
                    check=False,
                )

            if result.returncode != 0:
                err = result.stderr.strip()
                log.warning(f"备份命令执行失败: {err}")
                return RdbBackupResult(False, error_message=f"备份命令执行失败: {err}")

            if compress:
                with open(raw_path, "rb") as f_in, gzip.open(output_path, "wb") as f_out:
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

    def dump(
        self,
        cfg: RdbCfg,
        output_path: str,
        tables: list[str] | None = None,
        schema_only: bool = False,
        compress: bool = True,
        verbose: bool = False,
        timeout: int | None = None,
    ) -> RdbBackupResult:
        """
        执行数据库备份（模板方法）。

        串联：可用性检查 → 构建命令 → （可选）打印脱敏命令/环境 → 执行并落盘。

        Args:
            cfg: 数据库配置。
            output_path: 输出文件路径。
            tables: 只备份指定的表。
            schema_only: 只备份结构，不备份数据。
            compress: 是否 gzip 压缩。
            verbose: 是否显示详细输出（命令与环境变量自动脱敏）。
            timeout: 命令执行超时秒数，``None`` 用 :attr:`DEFAULT_TIMEOUT`。

        Returns:
            :class:`RdbBackupResult` 备份结果。
        """
        if not self.is_available():
            return RdbBackupResult(False, error_message=f"{self.tool_name} 未安装，{self.install_hint}")

        cmd = self._build_dump_command(cfg, tables, schema_only)
        env = self._get_env(cfg)

        if verbose:
            cmdutil.log_command(cmd, env, log)

        return self._execute_dump(cmd, output_path, compress, env, timeout)

    # endregion

    # region ======== 恢复 ========

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

    def _execute_restore(
        self, cmd: list[str], input_path: str, env: dict[str, str] | None = None, timeout: int | None = None
    ) -> RdbBackupResult:
        """
        执行恢复命令：按 ``.gz`` 后缀决定是否解压，将 SQL 流经 stdin 喂入恢复客户端。
        """
        actual_timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT
        opener = gzip.open if input_path.endswith(".gz") else open
        try:
            with opener(input_path, "rb") as f_in:
                result = subprocess.run(
                    cmd,
                    stdin=cast(IO[Any], f_in),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=actual_timeout,
                    env=env,
                    check=False,
                )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace").strip()
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

    def restore(
        self, cfg: RdbCfg, input_path: str, verbose: bool = False, timeout: int | None = None
    ) -> RdbBackupResult:
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

        env = self._get_env(cfg)
        if verbose:
            cmdutil.log_command(cmd, env, log)

        return self._execute_restore(cmd, input_path, env, timeout)

    # endregion

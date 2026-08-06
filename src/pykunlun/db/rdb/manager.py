"""
关系型数据库驱动客户端管理器（三层注册表：client 类 + client 实例 + 备份服务）。

:class:`RdbManager` 维护三张注册表：``db_type -> RdbClient 子类`` 的类注册表、
``name -> RdbClient 实例`` 的实例注册表，以及 ``db_type -> RdbBackupService 实例`` 的备份服务注册表。
前两者用于按数据库类型/别名工厂化创建与获取客户端实例，后者用于 dump/restore 转发。
"""

import threading
from collections.abc import Callable
from typing import Any

from pykunlun.util import logutil

from .backup import RdbBackupResult, RdbBackupService
from .cfg import RdbCfg
from .client import RdbClient
from .readonly import RdbReadOnlyClient

log = logutil.getLogger(__name__)


class RdbManager:
    """
    关系型数据库驱动客户端管理器（双层注册表：类 + 实例）。

    维护两张注册表：

      - **类注册表** ``db_type -> RdbClient 子类(class)``：管理各数据库类型对应的实现类。
        注册键取自类自身的 :attr:`~RdbClient.db_type`（自动小写归一化），无需调用方显式提供。
        通过 :meth:`register_client_class` 注册后，即可用 :meth:`register` 直接传入 :class:`RdbCfg`，
        由本管理器按 ``cfg.db_type`` 工厂化创建实例——调用方无需手动 ``new``。
      - **实例注册表** ``name -> RdbClient 实例``：管理绑定具体配置的客户端实例，每个实例绑定一份 :class:`RdbCfg`。
        同一管理器可注册多份不同配置的实例，通过名称（别名）区分。

    关于 ``name`` 的用途：name 是注册实例的**别名**，用于区分同一数据库类型的不同连接配置，而非区分数据库类型本身。
        典型场景是按环境隔离——例如为开发环境与测试环境各注册一个 :class:`MysqlRdbClient` 实例
        （实现类相同、连接配置不同），通过 ``name="dev"`` / ``name="test"`` 分别访问；
    也可按业务模块命名（如 ``"order_db"``、``"user_db"``）。

    :attr:`DEFAULT_NAME` 为默认实例名称。
    除 :meth:`register_client_class`（按类自身 db_type 归档）与 :meth:`register`（须显式提供 name）外，
    其余方法（:meth:`unregister`、:meth:`get_client`、:meth:`get_connection`、:meth:`query`、:meth:`execute`）
    的 ``name`` 参数均可省略，省略时使用默认名称。

    本类额外提供 :meth:`get_connection`、:meth:`query`、:meth:`execute` 便捷方法，
    比直接调用 :class:`RdbClient` 同名方法多一个 ``name`` 参数（用于选择已注册的实例），其余参数语义一致。

    此外，本管理器还维护一张**备份服务注册表** ``db_type -> RdbBackupService``：通过 :meth:`register_backup_service`
    注册各数据库类型的 :class:`RdbBackupService` 实例后，即可用 :meth:`dump` / :meth:`restore` 便捷方法
    直接备份/恢复。与 query/execute 不同，dump/restore **直接接收 :class:`RdbCfg` 作参数**（而非
    ``name``）：用 ``cfg.db_type`` 查备份服务后把 cfg 透传执行。这是因为 query/execute 依赖一条已建立的
    连接（client 实例绑定 cfg，用 name 索引），而 dump/restore 是一次性命令行操作，cfg 仅作输入参数、
    无需预先建 client。对 ``cfg.read_only=True`` 的配置，:meth:`restore` 会被拒绝（只读库不可写），
    :meth:`dump` 不受限（导出数据不修改源库）。

    用法示例::

        manager = RdbManager()

        # 1) 注册实现类（db_type 取自类自身，一次性）
        manager.register_client_class(SqliteClient)

        # 2) 注册实例：直接传 RdbCfg，按 cfg.db_type 自动 new
        cfg = RdbCfg(db_type='sqlite', database='/tmp/test.db')
        manager.register("default", cfg)

        # 也仍可显式传入已构造的实例（不依赖类注册表）
        # manager.register("default", SqliteClient(cfg))

        # 3) 通过管理器直接执行（name 可省略，默认 "default"）
        manager.execute("INSERT INTO t VALUES (1)")
        rows = manager.query("SELECT * FROM t")

        # 指定 name 操作非默认实例
        manager.execute("...", name="other")
    """

    # region ======== 构造 ========

    #: 默认实例名称
    DEFAULT_NAME = "default"

    def __init__(self, config_loader: Callable[['RdbManager', str], None] | None = None) -> None:
        """
        Args:
            config_loader: 配置加载器，当 :meth:`get_client` 按名称查找失败时调用。
                签名 ``(manager: RdbManager, name: str) -> None``，
                由 loader 自行决定加载策略（如一次性加载、按需加载等）。
                为 ``None`` 时不启用 fallback。
        """
        # 类注册表：db_type -> RdbClient 子类（用于按 cfg.db_type 工厂化创建实例）
        self._class_registry: dict[str, type[RdbClient]] = {}
        # 实例注册表：name -> RdbClient 实例（本实例独有）
        self._client_registry: dict[str, RdbClient] = {}
        # 备份服务注册表：db_type -> RdbBackupService 实例（按 db_type 工厂化获取备份服务）
        self._backup_registry: dict[str, RdbBackupService] = {}
        self._lock = threading.RLock()
        self._config_loader = config_loader

    # endregion

    # region ======== getter ========

    def get_config_loader(self) -> Callable[['RdbManager', str], None] | None:
        """
        获取配置加载器。

        Returns:
            配置加载器 callable，未设置时返回 None。
        """
        return self._config_loader

    # endregion

    # region ======== 类注册表（db_type -> RdbClient 子类） ========

    def _maybe_wrap_read_only(self, client: RdbClient) -> RdbClient:
        """
        若客户端配置为只读（``cfg.read_only=True``）且尚未被只读代理包裹，则套一层
        :class:`RdbReadOnlyClient`；否则原样返回。

        使本管理器注册的只读实例统一具备客户端层写拦截能力，无论实例是按配置工厂化创建、
        还是由调用方预先构造后传入 :meth:`register`。已是 :class:`RdbReadOnlyClient` 的不再重复包裹。

        Args:
            client: 待处理的客户端实例。

        Returns:
            原客户端，或其只读代理。
        """
        if client.cfg.read_only and not isinstance(client, RdbReadOnlyClient):
            return RdbReadOnlyClient(client)
        return client

    def _create_client_from_cfg(self, cfg: RdbCfg) -> RdbClient:
        """
        按 ``cfg.db_type`` 从类注册表取出实现类并实例化（内部工具）。

        创建后若 ``cfg.read_only`` 为 True，自动用 :class:`RdbReadOnlyClient` 代理包裹，
        使只读配置在经 :meth:`query` / :meth:`execute` 调度时拒绝写操作。

        Args:
            cfg: 数据库配置；必须显式提供 db_type 以便查表。

        Returns:
            绑定该 cfg 的 :class:`RdbClient` 实例（可能被只读代理包裹）。

        Raises:
            ValueError: ``cfg.db_type`` 为空、或该类型未注册时抛出。
        """
        db_type = cfg.db_type
        if not db_type:
            raise ValueError(
                "通过 RdbCfg 创建实例时必须显式提供 cfg.db_type，"
                "以便从类注册表查找对应的 RdbClient 实现类"
            )
        key = db_type.lower()
        with self._lock:
            client_cls = self._class_registry.get(key)
            if client_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 db_type={db_type!r} 对应的 RdbClient 实现类，"
                    f"已注册的类型: {registered}；请先通过 register_client_class() 注册"
                )
        return self._maybe_wrap_read_only(client_cls(cfg))

    def register_client_class(self, client_cls: type[RdbClient]) -> None:
        """
        注册或替换一个 :class:`RdbClient` 实现类（按类自身的 :attr:`~RdbClient.db_type` 归档）。

        注册后即可通过 :meth:`register` 传入 :class:`RdbCfg`，
        由本管理器根据 ``cfg.db_type`` 工厂化创建实例，调用方无需手动 ``new``。

        Args:
            client_cls: :class:`RdbClient` 的具体子类（类对象，非实例）。

        Raises:
            TypeError: 传入的不是 :class:`RdbClient` 子类时抛出。
            ValueError: 类的 :attr:`~RdbClient.db_type` 为空时抛出。
        """
        if not (isinstance(client_cls, type) and issubclass(client_cls, RdbClient)):
            raise TypeError(
                f"register_client_class 仅接受 RdbClient 的子类，"
                f"收到: {client_cls!r}"
            )
        db_type = getattr(client_cls, 'db_type', None)
        if not isinstance(db_type, str) or not db_type:
            raise ValueError(
                f"{client_cls.__name__}.db_type 必须是非空字符串，"
                f"当前值: {db_type!r}"
            )
        key = db_type.lower()
        with self._lock:
            self._class_registry[key] = client_cls

    def unregister_client_class(self, db_type: str) -> bool:
        """
        取消注册指定类型的 :class:`RdbClient` 实现类。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            是否成功移除。
        """
        if not isinstance(db_type, str) or not db_type:
            return False
        key = db_type.lower()
        with self._lock:
            if key in self._class_registry:
                del self._class_registry[key]
                return True
            return False

    def get_client_class(self, db_type: str) -> type[RdbClient]:
        """
        获取指定数据库类型的 :class:`RdbClient` 实现类。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            :class:`RdbClient` 子类。

        Raises:
            ValueError: 该类型未注册时抛出。
        """
        if not isinstance(db_type, str) or not db_type:
            raise ValueError("db_type 不能为空")
        key = db_type.lower()
        with self._lock:
            client_cls = self._class_registry.get(key)
            if client_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 db_type={db_type!r} 对应的 RdbClient 实现类，"
                    f"已注册的类型: {registered}；请先通过 register_client_class() 注册"
                )
            return client_cls

    def get_registered_client_types(self) -> list[str]:
        """
        获取所有已注册（即支持工厂化创建）的数据库类型列表。

        Returns:
            数据库类型标识列表。
        """
        with self._lock:
            return list(self._class_registry.keys())

    # endregion

    # region ======== 实例注册表（name -> RdbClient 实例） ========

    def _resolve_name(self, name: str | None) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, rdb_client: RdbClient | RdbCfg) -> None:
        """
        注册或替换指定名称的客户端实例。

        第二个参数支持两种形式：

          - :class:`RdbClient` 实例：直接按名称归档（不依赖类注册表）；
          - :class:`RdbCfg` 配置：按 ``cfg.db_type`` 从类注册表取出实现类，自动 ``cls(cfg)`` 工厂化创建实例后归档。
            此时要求对应实现类已通过 :meth:`register_client_class` 注册，且 ``cfg.db_type`` 不能为空。

        无论哪种形式，若配置 ``read_only=True``，均自动用 :class:`RdbReadOnlyClient` 代理包裹
        （见 :meth:`_maybe_wrap_read_only`），使注册的只读实例具备客户端层写拦截。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            rdb_client: :class:`RdbClient` 实例，或 :class:`RdbCfg` 配置。

        Raises:
            ValueError: 传入 :class:`RdbCfg` 但 ``db_type`` 为空、或对应类型
                未注册时抛出。
        """
        key = self._resolve_name(name)
        if isinstance(rdb_client, RdbCfg):
            client = self._create_client_from_cfg(rdb_client)   # 已含只读包裹
        else:
            client = self._maybe_wrap_read_only(rdb_client)     # 预构造实例同样包裹
        with self._lock:
            self._client_registry[key] = client

    def unregister(self, name: str | None = None) -> bool:
        """
        取消注册指定名称的客户端实例。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            是否成功移除。
        """
        key = self._resolve_name(name)
        with self._lock:
            if key in self._client_registry:
                del self._client_registry[key]
                return True
            return False

    def get_client(self, name: str | None = None) -> RdbClient:
        """
        获取指定名称的客户端实例。

        若按名称未找到且已设置 :attr:`_config_loader`，会先调用配置加载器
        （传入 manager 自身与请求的 name），再重新查找；仍未找到则抛出异常。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`RdbClient` 实例。

        Raises:
            ValueError: 该名称尚未注册且配置加载器未能成功加载时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            client = self._client_registry.get(key)
            if client is None and self._config_loader is not None:
                self._config_loader(self, key)
                client = self._client_registry.get(key)
            if client is None:
                registered = ", ".join(self._client_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到实例 '{key}'，已注册的实例: {registered}；"
                    f"请先通过 register() 注册"
                )
            return client

    def get_registered_names(self) -> list[str]:
        """
        获取所有已注册的实例名称列表。

        Returns:
            实例名称列表。
        """
        with self._lock:
            return list(self._client_registry.keys())

    # endregion

    # region ======== 备份服务注册表（db_type -> RdbBackupService 实例） ========

    def register_backup_service(self, service: RdbBackupService) -> None:
        """
        注册或替换一个备份服务（按服务自身的 :attr:`~RdbBackupService.db_type` 归档）。

        注册后即可通过 :meth:`dump` / :meth:`restore` 直接备份/恢复该类型数据库，
        无需手动持有备份服务实例。

        Args:
            service: :class:`RdbBackupService` 实例。

        Raises:
            ValueError: 服务的 :attr:`~RdbBackupService.db_type` 为空时抛出。
        """
        db_type = getattr(service, 'db_type', None)
        if not isinstance(db_type, str) or not db_type:
            raise ValueError(
                f"{type(service).__name__}.db_type 必须是非空字符串，"
                f"当前值: {db_type!r}"
            )
        key = db_type.lower()
        with self._lock:
            self._backup_registry[key] = service

    def unregister_backup_service(self, db_type: str) -> bool:
        """
        取消注册指定类型的备份服务。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            是否成功移除。
        """
        if not isinstance(db_type, str) or not db_type:
            return False
        key = db_type.lower()
        with self._lock:
            if key in self._backup_registry:
                del self._backup_registry[key]
                return True
            return False

    def get_backup_service(self, db_type: str) -> RdbBackupService:
        """
        获取指定类型的备份服务。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            :class:`RdbBackupService` 实例。

        Raises:
            ValueError: 不支持的数据库类型时抛出。
        """
        if not isinstance(db_type, str) or not db_type:
            raise ValueError("db_type 不能为空")
        key = db_type.lower()
        with self._lock:
            service = self._backup_registry.get(key)
            if service is None:
                supported = ", ".join(self._backup_registry.keys()) or "（无）"
                raise ValueError(
                    f"不支持的数据库类型: {db_type}，已注册的备份类型: {supported}；"
                    f"请先通过 register_backup_service() 注册"
                )
            return service

    def get_registered_backup_types(self) -> list[str]:
        """
        获取所有已注册备份服务的数据库类型列表。

        Returns:
            数据库类型标识列表。
        """
        with self._lock:
            return list(self._backup_registry.keys())

    # endregion

    # region ======== 执行便捷方法（透传 RdbClient） ========

    def get_connection(self, name: str | None = None):
        """
        打开并返回指定实例的数据库连接（透传 :meth:`RdbClient.get_connection`）。

        .. warning::
            若该实例为只读客户端（:class:`RdbReadOnlyClient`），返回的裸连接**绕过客户端层写拦截**
            （写拦截仅在 :meth:`query` / :meth:`execute` 调度链内生效）。本方法对此仅打一条
            warning 日志提醒，不阻止调用；确权请依赖数据库账号权限。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            数据库连接对象。
        """
        client = self.get_client(name)
        if isinstance(client, RdbReadOnlyClient):
            log.warning(
                "实例 '%s' 为只读客户端，get_connection 返回的裸连接绕过写拦截，"
                "请确认后续操作确实只读", self._resolve_name(name)
            )
        return client.get_connection()

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None,
              name: str | None = None) -> list[dict]:
        """
        执行查询（透传 :meth:`RdbClient.query`）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射，``{原始类型: 转换函数}``，由调用方按需传入；
                ``None`` 表示不做任何转换，保持驱动返回的原始类型。
                转换先按值的**精确类型**（:func:`type`）匹配，未命中则沿其 MRO 回退查父类，
                兼容驱动返回的子类型；``None`` 值不参与转换。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        return self.get_client(name).query(sql, params, converters)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None,
                name: str | None = None) -> int:
        """
        执行 SQL 语句（透传 :meth:`RdbClient.execute`）。

        Args:
            sql: SQL 语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            受影响的行数。
        """
        return self.get_client(name).execute(sql, params)

    # endregion

    # region ======== 备份/恢复便捷方法（透传 RdbBackupService，cfg 作参数） ========

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
        执行数据库备份（透传 :meth:`RdbBackupService.dump`）。

        与 :meth:`query` / :meth:`execute` 不同，本方法**直接接收 :class:`RdbCfg` 作参数**
        （而非 ``name``）：用 ``cfg.db_type`` 从备份服务注册表取出 :class:`RdbBackupService` 实例，
        再把 cfg 透传给 :meth:`RdbBackupService.dump` 执行。这是因为备份是一次性命令行操作，
        cfg 仅作输入参数，无需预先建立 client 连接。

        不受 ``cfg.read_only`` 限制（导出数据不修改源库，只读库也可备份）。

        Args:
            cfg: 数据库配置（``cfg.db_type`` 决定用哪个备份服务）。
            output_path: 输出文件路径。
            tables: 只备份指定的表。
            schema_only: 只备份结构，不备份数据。
            compress: 是否 gzip 压缩。
            verbose: 是否显示详细输出（命令与环境变量自动脱敏）。
            timeout: 命令执行超时秒数，``None`` 用备份服务默认值。

        Returns:
            :class:`RdbBackupResult` 备份结果。
        """
        db_type = cfg.db_type
        if not db_type:
            raise ValueError(
                f"备份操作要求 cfg.db_type 不能为空：{cfg.database!r}"
            )
        return self.get_backup_service(db_type).dump(
            cfg, output_path, tables, schema_only, compress, verbose, timeout
        )

    def restore(
        self, cfg: RdbCfg, input_path: str, verbose: bool = False, timeout: int | None = None
    ) -> RdbBackupResult:
        """
        从备份文件恢复数据库（透传 :meth:`RdbBackupService.restore`）。

        与 :meth:`dump` 同样直接接收 :class:`RdbCfg` 作参数。**对 ``cfg.read_only=True``
        的配置拒绝执行**（恢复是写操作，只读库不可写）；dump 则不受只读限制（导出数据不修改源库）。

        Args:
            cfg: 数据库配置（``cfg.db_type`` 决定用哪个备份服务）。
            input_path: 备份文件路径。
            verbose: 是否显示详细输出（命令与环境变量自动脱敏）。
            timeout: 命令执行超时秒数，``None`` 用备份服务默认值。

        Returns:
            :class:`RdbBackupResult` 恢复结果（成功时 ``output_path`` 为空）。

        Raises:
            ValueError: ``cfg.read_only`` 为 True 时抛出（只读配置禁止恢复）。
        """
        if cfg.read_only:
            raise ValueError(
                f"配置为只读（read_only=True），禁止恢复操作：{cfg.database!r}"
            )
        db_type = cfg.db_type
        if not db_type:
            raise ValueError(
                f"恢复操作要求 cfg.db_type 不能为空：{cfg.database!r}"
            )
        return self.get_backup_service(db_type).restore(cfg, input_path, verbose, timeout)

    # endregion

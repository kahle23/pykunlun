"""
双层注册表管理器基类模块。

提供 :class:`RegistryManager`：组合"类注册表 + 实例注册表"两张
:class:`~pykunlun.registry.atom.Registry` 原子的管理器基类——类型注册/注销/
查找/列举、按配置工厂化创建、按名查找失败时的配置加载器回退全部就绪，
领域差异通过**词汇常量**与**钩子**注入。

派生一个领域管理器只需声明词汇常量 + （如需）以别名保持既有公共 API 名::

    class OssManager(RegistryManager[OssClient, OssCfg]):
        TYPE_ATTR = 'oss_type'                # 键在实现类与 cfg 上的属性名
        ITEM_LABEL = 'OssClient 实现类'        # 未注册报错里的物品称谓
        REGISTERED_LABEL = '类型'              # "已注册的XX" 措辞
        CLASS_HINT = 'register_client_class'  # 类未注册提示的注册方法名
        INSTANCE_HINT = 'register'            # 实例未注册提示的注册方法名
        CFG_LABEL = 'OssCfg'                  # 空键报错里的配置类名
        ITEM_BASE = OssClient                 # 类注册的 issubclass 基准
        CFG_CLS = OssCfg                      # register 实例/配置分支判定

        # 既有公共 API 名以别名保持不变（取方法的基类须带类型参数，原因见下"两个语法要点"）
        register_client_class = RegistryManager[OssClient, OssCfg].register_class
        register = RegistryManager[OssClient, OssCfg].register_instance
        get_client = RegistryManager[OssClient, OssCfg].get_instance
        ...

词汇常量缺失或为空在**类定义时**即报 :class:`TypeError`
（见 :meth:`RegistryManager.__init_subclass__`），不必等到运行期。

两个语法要点（初见易惑，以 oss 取值为例）：

  - ``RegistryManager[OssClient, OssCfg]`` **不是实例化**（实例化是加括号调用
    ``__init__``），而是 typing 的参数化别名：给同一个类贴上"TItem=OssClient、
    TCfg=OssCfg"的类型标签，运行时指向的仍是同一个类对象，零开销。
    类定义处 ``class OssManager(RegistryManager[OssClient, OssCfg])`` 同理——
    就是普通继承，下标只为让类型检查器按具体类型校验继承来的方法；
  - 别名行 ``.register_class`` 取出的是**函数对象**，挂进子类类体后与普通方法
    无异：调用 ``manager.get_client('x')`` 时 ``self`` 绑定为 manager 自己，
    操作的是自己的注册表（与写一个 ``def get_client(self, ...)`` 转发等价，
    但保持方法同一性、零间接层）。取方法时基类带类型参数，是为了让检查器按
    已绑定的类型推导完整签名——裸 ``RegistryManager.register_class`` 在
    pyright strict 下报 partially unknown。

领域差异钩子（默认恒等，按需覆盖）：

  - :meth:`RegistryManager._wrap_item`: 工厂化创建与实例注册共用的后处理，
    如 rdb 的只读代理包裹。

线程模型：两张表共享管理器构造时创建的一把 :class:`threading.RLock`
（与既有各 manager 的单锁口径一致），原子的所有读写均在锁内完成。
"""

import threading
from collections.abc import Callable
from typing import Any, ClassVar, Generic, TypeVar, cast

from .atom import Registry, TItem

TCfg = TypeVar('TCfg')

#: 配置加载器签名：实例按名未命中时被调用 ``(manager, name) -> None``，
#: 由 loader 自行决定加载策略（一次性加载、按需加载等）；加载后管理器重试查找一次。
#:
#: 调用契约（与 :class:`pykunlun.data.cache.Cache` 的锁外 loader 同款，有意设计）：
#: loader 在管理器锁**外**被调用，避免 loader 回调访问注册表死锁、长耗时加载
#: 阻塞其他读写；代价是可能被**并发地、重复地**调用——同名并发未命中各自触发、
#: 未注册成功下次再调、注销后再取重调（"卸载后重载新配置"的更新通路依赖于此）。
#: 故幂等与线程安全由 loader 自行保证；需防重复创建昂贵资源（如连接池）时，
#: loader 内自行实现并发去重（single-flight）。
ConfigLoader = Callable[['RegistryManager[Any, Any]', str], None]

#: 子类必须声明的词汇常量名（缺失或为空在类定义时即报 TypeError）
_REQUIRED_VOCAB: tuple[str, ...] = (
    'TYPE_ATTR',
    'ITEM_LABEL',
    'REGISTERED_LABEL',
    'CLASS_HINT',
    'INSTANCE_HINT',
    'CFG_LABEL',
    'ITEM_BASE',
    'CFG_CLS',
)


class RegistryManager(Generic[TItem, TCfg]):
    """
    双层注册表管理器基类（类注册表 + 实例注册表）。

    收敛各领域 manager 的公共骨架，领域差异经两类扩展点注入：

      - **词汇常量**（:class:`ClassVar`，子类必须声明，类定义时校验）：
        ``TYPE_ATTR`` / ``ITEM_LABEL`` / ``REGISTERED_LABEL`` / ``CLASS_HINT`` /
        ``INSTANCE_HINT`` / ``CFG_LABEL`` / ``ITEM_BASE`` / ``CFG_CLS``，
        语义见模块 docstring 的派生示例；
      - **钩子**（默认恒等）：:meth:`_wrap_item` 为工厂化创建与实例注册
        共用的后处理（如只读代理包裹）。

    本类方法名是**通用名**（``register_class`` / ``get_instance`` 等）；
    领域子类以类属性别名（``get_client = RegistryManager.get_instance``）保持
    既有公共 API 名不变。便捷透传方法（领域 API + ``name`` 参数）不属于本骨架，
    由子类自行实现。
    """

    #: 默认实例名称（实例方法省略 ``name`` 参数时回落于此）
    DEFAULT_NAME: ClassVar[str] = 'default'

    # ---- 词汇常量（子类必须声明，缺失/为空在类定义时即报 TypeError；逐条语义见模块 docstring 派生示例）----
    # 类型只能标到 type[Any]：typing 规范禁止 ClassVar 引用类自身类型变量（TItem/TCfg），
    # 精确类型由子类声明时绑定（如 CFG_CLS: ClassVar[type[OssCfg]]），使用点经 cast 恢复
    TYPE_ATTR: ClassVar[str]
    ITEM_LABEL: ClassVar[str]
    REGISTERED_LABEL: ClassVar[str]
    CLASS_HINT: ClassVar[str]
    INSTANCE_HINT: ClassVar[str]
    CFG_LABEL: ClassVar[str]
    ITEM_BASE: ClassVar[type[Any]]
    CFG_CLS: ClassVar[type[Any]]

    # region ======== 构造与派生校验 ========
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """
        类定义即校验词汇常量，缺失/为空立刻失败，不等运行期。
        """
        super().__init_subclass__(**kwargs)
        missing = [name for name in _REQUIRED_VOCAB if not getattr(cls, name, None)]
        if missing:
            raise TypeError(
                f"{cls.__name__} 缺少注册表词汇常量声明: {', '.join(missing)}"
                f"（语义见 {RegistryManager.__name__} 模块 docstring 的派生示例）"
            )

    def __init__(self, config_loader: Callable[[Any, str], None] | None = None) -> None:
        """
        Args:
            config_loader: 配置加载器，:meth:`get_instance` 按名查找失败时调用，
                签名 ``(manager, name) -> None``；加载后重试查找一次，
                仍未命中才抛错。``None`` 表示不启用回退。
                loader 在管理器锁外调用、可能被并发/重复调用（失败重试、
                卸载后重载、并发未命中），幂等与线程安全由 loader 自负——
                完整契约见模块级 :data:`ConfigLoader` 注释。
                loader 的 ``manager`` 参数建议标注具体子类类型（如 ``OssManager``），
                本基类经 ``Any`` 收口以兼容各子类的具体化签名（逆变限制）。
        """
        self._lock = threading.RLock()
        self._config_loader: ConfigLoader | None = config_loader
        # 类注册表：type_attr（小写折叠）-> 实现类
        self._class_registry: Registry[type[TItem]] = Registry(fold_case=True, lock=self._lock)
        # 实例注册表：name（保留大小写）-> 实例
        self._instance_registry: Registry[TItem] = Registry(fold_case=False, lock=self._lock)
    # endregion

    # region ======== getter ========
    def get_config_loader(self) -> ConfigLoader | None:
        """
        获取配置加载器。

        Returns:
            配置加载器 callable，未设置时返回 ``None``。
        """
        return self._config_loader
    # endregion

    # region ======== 类注册表（type_attr -> 实现类） ========
    def register_class(self, item_cls: type[TItem], *, replace: bool = True) -> None:
        """
        注册或替换一个实现类（按类自身 :attr:`TYPE_ATTR` 属性归档，自动小写折叠）。

        Args:
            item_cls: :attr:`ITEM_BASE` 的具体子类（类对象，非实例）。
            replace: 同类型键已存在时是否覆盖；``False`` 且已存在时抛
                :class:`ValueError`。

        Raises:
            TypeError: 传入的不是 :attr:`ITEM_BASE` 子类时抛出。
            ValueError: 类的 :attr:`TYPE_ATTR` 属性缺失或为空、或
                ``replace=False`` 且类型键已注册时抛出。
        """
        if not (isinstance(item_cls, type) and issubclass(item_cls, self.ITEM_BASE)):
            raise TypeError(
                f"{self.CLASS_HINT} 仅接受 {self.ITEM_BASE.__name__} 的子类，"
                f"收到: {item_cls!r}"
            )
        type_key = getattr(item_cls, self.TYPE_ATTR, None)
        if not isinstance(type_key, str) or not type_key:
            raise ValueError(
                f"{item_cls.__name__}.{self.TYPE_ATTR} 必须是非空字符串，"
                f"当前值: {type_key!r}"
            )
        self._class_registry.register(type_key, item_cls, replace=replace)

    def unregister_class(self, type_key: str) -> bool:
        """
        取消注册指定类型的实现类（大小写不敏感）。

        Returns:
            是否成功移除；入参非字符串或为空时为 ``False``。
        """
        if not isinstance(type_key, str) or not type_key:
            return False
        return self._class_registry.unregister(type_key)

    def get_class(self, type_key: str) -> type[TItem]:
        """
        获取指定类型的实现类（大小写不敏感）。

        Args:
            type_key: 类型标识。

        Returns:
            :attr:`ITEM_BASE` 子类。

        Raises:
            ValueError: 入参非字符串或为空、或该类型未注册时抛出。
        """
        if not isinstance(type_key, str) or not type_key:
            raise ValueError(f"{self.TYPE_ATTR} 不能为空")
        item_cls = self._class_registry.get(type_key)
        if item_cls is None:
            registered = ', '.join(self._class_registry.keys()) or '（无）'
            raise ValueError(
                f"未找到 {self.TYPE_ATTR}={type_key!r} 对应的 {self.ITEM_LABEL}，"
                f"已注册的{self.REGISTERED_LABEL}: {registered}；"
                f"请先通过 {self.CLASS_HINT}() 注册"
            )
        return item_cls

    def get_registered_types(self) -> list[str]:
        """
        所有已注册（即支持工厂化创建）的类型标识列表。
        """
        return self._class_registry.keys()

    def _create_from_cfg(self, cfg: TCfg) -> TItem:
        """
        按 ``cfg.{TYPE_ATTR}`` 从类注册表取出实现类并实例化（内部模板）。

        末尾经 :meth:`_wrap_item` 后处理（默认恒等，领域子类按需覆盖）。

        Args:
            cfg: 领域配置对象；须显式提供 :attr:`TYPE_ATTR` 属性以便查表。

        Returns:
            绑定该 cfg 的实例（可能经 :meth:`_wrap_item` 包裹）。

        Raises:
            ValueError: ``cfg`` 的类型属性为空、或该类型未注册时抛出。
        """
        type_key = getattr(cfg, self.TYPE_ATTR, None)
        if not type_key:
            raise ValueError(
                f"通过 {self.CFG_LABEL} 创建实例时必须显式提供 cfg.{self.TYPE_ATTR}，"
                f"以便从类注册表查找对应的 {self.ITEM_BASE.__name__} 实现类"
            )
        item_cls = self.get_class(type_key)
        # type[TItem] 经未绑定类型变量间接调用：构造签名对检查器不可见；
        # 此处是工厂化创建的唯一出口，定向豁免
        return self._wrap_item(item_cls(cfg))  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]
    # endregion

    # region ======== 实例注册表（name -> 实例） ========
    def _resolve_name(self, name: str | None) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    def register_instance(self, name: str, item: TItem | TCfg, *, replace: bool = True) -> None:
        """
        注册或替换指定名称的实例。

        第二个参数支持两种形式（以 ``isinstance(item, :attr:`CFG_CLS`)`` 先判，
        同时属于两类的对象走配置分支）：

          - 实例（:attr:`ITEM_BASE` 子类对象）：直接按名称归档（不依赖类注册表），
            经 ``isinstance`` 运行时校验，类型错误在注册现场即抛
            :class:`TypeError`，不延迟到使用点；
          - 配置（:attr:`CFG_CLS` 实例）：按 ``cfg.{TYPE_ATTR}`` 从类注册表取出
            实现类，自动 ``cls(cfg)`` 工厂化创建后归档。

        两种形式最终均经 :meth:`_wrap_item` 后处理（默认恒等）。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            item: 实例，或配置对象。
            replace: 同名已注册时是否覆盖；``False`` 且已存在时抛
                :class:`ValueError`。

        Raises:
            TypeError: 实例形式传入的不是 :attr:`ITEM_BASE` 的实例时抛出。
            ValueError: 传入配置但类型属性为空、对应类型未注册、
                ``replace=False`` 且名称已注册时抛出。
        """
        key = self._resolve_name(name)
        # CFG_CLS 基类层面只能声明为 type[Any]（ClassVar 禁引类自身类型变量），
        # cast 恢复精确类型以让 isinstance 正向收窄出 cfg 分支
        cfg_cls: type[TCfg] = cast(type[TCfg], self.CFG_CLS)
        if isinstance(item, cfg_cls):
            resolved = self._create_from_cfg(item)
        else:
            if not isinstance(item, self.ITEM_BASE):
                raise TypeError(
                    f"{self.INSTANCE_HINT}() 的 item 须为 {self.CFG_CLS.__name__} 配置"
                    f"或 {self.ITEM_BASE.__name__} 实例，收到: {item!r}"
                )
            # 负向分支检查器无法从联合中剔除类型变量成员，按调用方注解契约 cast 回实例侧
            resolved = self._wrap_item(cast(TItem, item))
        self._instance_registry.register(key, resolved, replace=replace)

    def unregister_instance(self, name: str | None = None) -> bool:
        """
        取消注册指定名称的实例（``name`` 省略时使用 :attr:`DEFAULT_NAME`）。

        Returns:
            是否成功移除。
        """
        return self._instance_registry.unregister(self._resolve_name(name))

    def get_instance(self, name: str | None = None) -> TItem:
        """
        获取指定名称的实例（``name`` 省略时使用 :attr:`DEFAULT_NAME`）。

        按名未找到且构造时提供了 ``config_loader`` 时，先调用加载器
        （传入管理器自身与请求的 name）再重试查找；仍未找到才抛错。
        加载器在管理器锁外调用、可能被并发/重复调用（失败重试、卸载后重载、
        并发未命中），幂等与线程安全由 loader 自负——完整契约见
        :data:`ConfigLoader`。

        Returns:
            注册的实例。

        Raises:
            ValueError: 该名称尚未注册且配置加载器未能成功加载时抛出。
        """
        key = self._resolve_name(name)
        item = self._instance_registry.get(key)
        if item is None and self._config_loader is not None:
            self._config_loader(self, key)
            item = self._instance_registry.get(key)
        if item is None:
            registered = ', '.join(self._instance_registry.keys()) or '（无）'
            raise ValueError(
                f"未找到实例 '{key}'，已注册的实例: {registered}；"
                f"请先通过 {self.INSTANCE_HINT}() 注册"
            )
        return item

    def get_registered_names(self) -> list[str]:
        """
        所有已注册的实例名称列表（保留注册时的原名，区分大小写）。
        """
        return self._instance_registry.keys()
    # endregion

    # region ======== 领域钩子 ========
    def _wrap_item(self, item: TItem) -> TItem:
        """
        工厂化创建与实例注册共用的后处理钩子（默认恒等返回）。

        领域子类按需覆盖，如 rdb 对 ``cfg.read_only=True`` 的实例
        套只读代理（无论实例来自工厂化创建还是调用方预构造）。
        """
        return item
    # endregion

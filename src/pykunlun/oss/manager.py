"""
对象存储客户端管理器（双层注册表：client 类 + client 实例）。

:class:`OssManager` 维护两张注册表：``oss_type -> OssClient 子类`` 的类注册表、
``name -> OssClient 实例`` 的实例注册表。前者用于按存储类型工厂化创建实例，
后者用于按别名（名称）管理多个不同配置的客户端；并在两者之上提供带 ``name``
参数的便捷转发方法，使调用方不必持有具体 client 引用。
"""

import threading
from collections.abc import Callable
from typing import ClassVar

from pykunlun.util import logutil

from .cfg import OssCfg
from .client import OssClient
from .stat import ObjectStat

log = logutil.getLogger(__name__)


class OssManager:
    """
    对象存储客户端管理器（类注册表 + 实例注册表）。

    ``Oss`` 取自 OSS（Object Storage Service，对象存储服务）的首字母缩写，
    本库将其用作中性泛称，不特指阿里云同名产品。

    维护两张注册表：

      - **类注册表** ``oss_type -> OssClient 子类(class)``：管理各存储类型对应的实现类。
        注册键取自类自身的 :attr:`~OssClient.oss_type`（自动小写归一化），无需调用方显式提供。
        通过 :meth:`register_client_class` 注册后，即可用 :meth:`register` 直接传入 :class:`OssCfg`，
        由本管理器按 ``cfg.oss_type`` 工厂化创建实例——调用方无需手动 ``new``。
      - **实例注册表** ``name -> OssClient 实例``：管理绑定具体配置的客户端实例，
        每个实例绑定一份 :class:`OssCfg`。同一管理器可注册多份不同配置的实例，
        通过名称（别名）区分——典型场景是按环境隔离（``name="dev"`` / ``name="prod"``）
        或按业务模块命名（如 ``"report_files"``、``"user_avatars"``）。

    :attr:`DEFAULT_NAME` 为默认实例名称。除 :meth:`register_client_class`
    （按类自身 oss_type 归档）与 :meth:`register`（须显式提供 name）外，
    其余方法的 ``name`` 参数均可省略，省略时使用默认名称。

    用法示例::

        manager = OssManager()

        # 1) 注册实现类（oss_type 取自类自身，一次性）
        manager.register_client_class(LocalOssClient)

        # 2) 注册实例：直接传 OssCfg，按 cfg.oss_type 自动 new
        cfg = OssCfg(oss_type='local', bucket='demo', storage_options={'base_dir': '/data/oss'})
        manager.register("default", cfg)

        # 也仍可显式传入已构造的实例（不依赖类注册表）
        # manager.register("default", LocalOssClient(cfg))

        # 3) 通过管理器直接操作（name 可省略，默认 "default"）
        manager.put_object('a/b.txt', 'hello')
        text = manager.get_object_text('a/b.txt')

        # 指定 name 操作非默认实例
        manager.put_object('a.txt', 'x', name="other")
    """

    # region ======== 构造 ========
    #: 默认实例名称
    DEFAULT_NAME: ClassVar[str] = "default"

    def __init__(self, config_loader: Callable[['OssManager', str], None] | None = None) -> None:
        """
        Args:
            config_loader: 配置加载器，当 :meth:`get_client` 按名称查找失败时调用。
                签名 ``(manager: OssManager, name: str) -> None``，
                由 loader 自行决定加载策略（如一次性加载、按需加载等）。
                为 ``None`` 时不启用 fallback。
        """
        # 类注册表：oss_type -> OssClient 子类（用于按 cfg.oss_type 工厂化创建实例）
        self._class_registry: dict[str, type[OssClient]] = {}
        # 实例注册表：name -> OssClient 实例（本管理器实例独有）
        self._client_registry: dict[str, OssClient] = {}
        self._lock = threading.RLock()
        self._config_loader = config_loader
    # endregion

    # region ======== getter ========
    def get_config_loader(self) -> Callable[['OssManager', str], None] | None:
        """
        获取配置加载器。

        Returns:
            配置加载器 callable，未设置时返回 None。
        """
        return self._config_loader
    # endregion

    # region ======== 类注册表（oss_type -> OssClient 子类） ========
    def _create_client_from_cfg(self, cfg: OssCfg) -> OssClient:
        """
        按 ``cfg.oss_type`` 从类注册表取出实现类并实例化（内部工具）。

        Args:
            cfg: 对象存储配置；必须显式提供 oss_type 以便查表。

        Returns:
            绑定该 cfg 的 :class:`OssClient` 实例。

        Raises:
            ValueError: ``cfg.oss_type`` 为空、或该类型未注册时抛出。
        """
        oss_type = cfg.oss_type
        if not oss_type:
            raise ValueError(
                "通过 OssCfg 创建实例时必须显式提供 cfg.oss_type，"
                "以便从类注册表查找对应的 OssClient 实现类"
            )
        # 查表与未注册报错复用 get_client_class（锁内查找 + 统一错误文案的唯一出处）
        return self.get_client_class(oss_type)(cfg)

    def register_client_class(self, client_cls: type[OssClient]) -> None:
        """
        注册或替换一个 :class:`OssClient` 实现类（按类自身的 :attr:`~OssClient.oss_type` 归档）。

        注册后即可通过 :meth:`register` 传入 :class:`OssCfg`，
        由本管理器根据 ``cfg.oss_type`` 工厂化创建实例，调用方无需手动 ``new``。

        Args:
            client_cls: :class:`OssClient` 的具体子类（类对象，非实例）。

        Raises:
            TypeError: 传入的不是 :class:`OssClient` 子类时抛出。
            ValueError: 类的 :attr:`~OssClient.oss_type` 为空时抛出。
        """
        if not (isinstance(client_cls, type) and issubclass(client_cls, OssClient)):
            raise TypeError(
                f"register_client_class 仅接受 OssClient 的子类，"
                f"收到: {client_cls!r}"
            )
        oss_type = getattr(client_cls, 'oss_type', None)
        if not isinstance(oss_type, str) or not oss_type:
            raise ValueError(
                f"{client_cls.__name__}.oss_type 必须是非空字符串，"
                f"当前值: {oss_type!r}"
            )
        key = oss_type.lower()
        with self._lock:
            self._class_registry[key] = client_cls

    def unregister_client_class(self, oss_type: str) -> bool:
        """
        取消注册指定类型的 :class:`OssClient` 实现类。

        Args:
            oss_type: 存储类型标识（大小写不敏感）。

        Returns:
            是否成功移除。
        """
        if not isinstance(oss_type, str) or not oss_type:
            return False
        key = oss_type.lower()
        with self._lock:
            return self._class_registry.pop(key, None) is not None

    def get_client_class(self, oss_type: str) -> type[OssClient]:
        """
        获取指定存储类型的 :class:`OssClient` 实现类。

        Args:
            oss_type: 存储类型标识（大小写不敏感）。

        Returns:
            :class:`OssClient` 子类。

        Raises:
            ValueError: 该类型未注册时抛出。
        """
        if not isinstance(oss_type, str) or not oss_type:
            raise ValueError("oss_type 不能为空")
        key = oss_type.lower()
        with self._lock:
            client_cls = self._class_registry.get(key)
            if client_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 oss_type={oss_type!r} 对应的 OssClient 实现类，"
                    f"已注册的类型: {registered}；请先通过 register_client_class() 注册"
                )
            return client_cls

    def get_registered_client_types(self) -> list[str]:
        """
        获取所有已注册（即支持工厂化创建）的存储类型列表。

        Returns:
            存储类型标识列表。
        """
        with self._lock:
            return list(self._class_registry.keys())
    # endregion

    # region ======== 实例注册表（name -> OssClient 实例） ========
    def _resolve_name(self, name: str | None) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, oss_client: OssClient | OssCfg) -> None:
        """
        注册或替换指定名称的客户端实例。

        第二个参数支持两种形式：

          - :class:`OssClient` 实例：直接按名称归档（不依赖类注册表）；
          - :class:`OssCfg` 配置：按 ``cfg.oss_type`` 从类注册表取出实现类，
            自动 ``cls(cfg)`` 工厂化创建实例后归档。
            此时要求对应实现类已通过 :meth:`register_client_class` 注册，
            且 ``cfg.oss_type`` 不能为空。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            oss_client: :class:`OssClient` 实例，或 :class:`OssCfg` 配置。

        Raises:
            ValueError: 传入 :class:`OssCfg` 但 ``oss_type`` 为空、或对应类型
                未注册时抛出。
        """
        key = self._resolve_name(name)
        if isinstance(oss_client, OssCfg):
            client = self._create_client_from_cfg(oss_client)
        else:
            client = oss_client
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
            return self._client_registry.pop(key, None) is not None

    def get_client(self, name: str | None = None) -> OssClient:
        """
        获取指定名称的客户端实例。

        若按名称未找到且已设置配置加载器（构造参数 ``config_loader``，
        可经 :meth:`get_config_loader` 读取），会先调用配置加载器
        （传入 manager 自身与请求的 name），再重新查找；仍未找到则抛出异常。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`OssClient` 实例。

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

    # region ======== 操作便捷方法（写）（透传 OssClient，附加 name 参数） ========
    def put_object(self, key: str, data: bytes | str, content_type: str | None = None,
                   metadata: dict[str, str] | None = None,
                   bucket: str | None = None, name: str | None = None) -> None:
        """
        写入对象（透传 :meth:`OssClient.put_object`）。

        Args:
            key: 逻辑键。
            data: 对象内容；``str`` 按 UTF-8 编码。
            content_type: 对象 MIME 类型；None 表示不指定。
            metadata: 用户自定义元数据（裸键名），语义同 :meth:`OssClient.put_object`。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).put_object(key, data, content_type, metadata, bucket)

    def delete_object(self, key: str, bucket: str | None = None,
                      name: str | None = None) -> None:
        """
        删除对象，幂等（透传 :meth:`OssClient.delete_object`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).delete_object(key, bucket)

    def copy_object(self, src_key: str, dst_key: str, bucket: str | None = None,
                    dst_bucket: str | None = None, name: str | None = None) -> None:
        """
        复制对象（透传 :meth:`OssClient.copy_object`，支持跨桶）。

        Args:
            src_key: 源逻辑键。
            dst_key: 目标逻辑键。
            bucket: 源桶名；省略时使用客户端配置的默认桶。
            dst_bucket: 目标桶名；省略时回退 ``bucket``（同桶复制）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).copy_object(src_key, dst_key, bucket, dst_bucket)

    def move_object(self, src_key: str, dst_key: str, bucket: str | None = None,
                    dst_bucket: str | None = None, name: str | None = None) -> None:
        """
        移动对象（透传 :meth:`OssClient.move_object`，支持跨桶）。

        Args:
            src_key: 源逻辑键。
            dst_key: 目标逻辑键。
            bucket: 源桶名；省略时使用客户端配置的默认桶。
            dst_bucket: 目标桶名；省略时回退 ``bucket``（同桶移动）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).move_object(src_key, dst_key, bucket, dst_bucket)

    def upload_file(self, key: str, local_path: str, content_type: str | None = None,
                    metadata: dict[str, str] | None = None,
                    bucket: str | None = None, name: str | None = None) -> None:
        """
        上传本地文件为对象（透传 :meth:`OssClient.upload_file`）。

        Args:
            key: 逻辑键。
            local_path: 本地源文件路径。
            content_type: 对象 MIME 类型；省略时按扩展名自动推导。
            metadata: 用户自定义元数据（裸键名），语义同 :meth:`OssClient.put_object`。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).upload_file(key, local_path, content_type, metadata, bucket)
    # endregion

    # region ======== 操作便捷方法（读）（透传 OssClient，附加 name 参数） ========
    def get_object(self, key: str, bucket: str | None = None,
                   name: str | None = None) -> bytes:
        """
        读取对象内容字节（透传 :meth:`OssClient.get_object`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Raises:
            FileNotFoundError: 键不存在时抛出。
        """
        return self.get_client(name).get_object(key, bucket)

    def get_object_text(self, key: str, encoding: str = 'utf-8',
                        bucket: str | None = None,
                        name: str | None = None) -> str:
        """
        读取对象文本（透传 :meth:`OssClient.get_object_text`）。

        Args:
            key: 逻辑键。
            encoding: 文本编码，默认 UTF-8。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Raises:
            FileNotFoundError: 键不存在时抛出。
            UnicodeDecodeError: 内容与指定编码不符时抛出。
        """
        return self.get_client(name).get_object_text(key, encoding, bucket)

    def exists(self, key: str, bucket: str | None = None,
               name: str | None = None) -> bool:
        """
        判断对象是否存在（透传 :meth:`OssClient.exists`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        return self.get_client(name).exists(key, bucket)

    def stat(self, key: str, bucket: str | None = None,
             name: str | None = None) -> ObjectStat | None:
        """
        读取对象元信息（透传 :meth:`OssClient.stat`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            对象元信息；键不存在时返回 ``None`` 而非抛错
            （区别于 :meth:`get_object` 的 FileNotFoundError 语义）。
        """
        return self.get_client(name).stat(key, bucket)

    def list_objects(self, prefix: str = '', delimiter: str | None = None,
                     bucket: str | None = None,
                     name: str | None = None) -> list[ObjectStat]:
        """
        列举对象元信息（透传 :meth:`OssClient.list_objects`）。

        Args:
            prefix: 逻辑键前缀，空串表示全部。
            delimiter: 分隔符，语义同 :meth:`OssClient.list_objects`。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        return self.get_client(name).list_objects(prefix, delimiter, bucket)

    def list_keys(self, prefix: str = '', delimiter: str | None = None,
                  bucket: str | None = None, name: str | None = None) -> list[str]:
        """
        列举对象键（透传 :meth:`OssClient.list_keys`）。

        Args:
            prefix: 逻辑键前缀，空串表示全部。
            delimiter: 分隔符，语义同 :meth:`OssClient.list_objects`。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        return self.get_client(name).list_keys(prefix, delimiter, bucket)

    def download_file(self, key: str, local_path: str, bucket: str | None = None,
                      name: str | None = None) -> None:
        """
        下载对象到本地文件（透传 :meth:`OssClient.download_file`）。

        Args:
            key: 逻辑键。
            local_path: 本地目标文件路径。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Raises:
            FileNotFoundError: 键不存在时抛出。
        """
        self.get_client(name).download_file(key, local_path, bucket)

    def presigned_url(self, key: str, expires: int = 3600,
                      bucket: str | None = None,
                      name: str | None = None) -> str:
        """
        生成对象临时访问 URL（透传 :meth:`OssClient.presigned_url`）。

        Args:
            key: 逻辑键。
            expires: 有效期秒数，默认 1 小时。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        return self.get_client(name).presigned_url(key, expires, bucket)
    # endregion

    # region ======== 生命周期（透传 OssClient.close） ========
    def close(self, name: str | None = None) -> None:
        """
        释放指定实例的底层资源（透传 :meth:`OssClient.close`）。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        self.get_client(name).close()
    # endregion

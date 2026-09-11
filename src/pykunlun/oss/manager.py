"""
对象存储客户端管理器（双层注册表：client 类 + client 实例）。

:class:`OssManager` 派生自通用基座
:class:`~pykunlun.registry.base.RegistryManager`——注册/注销/查找/工厂化创建/
配置加载器回退的骨架与线程模型全部由基座提供，领域差异收敛为**词汇常量**
（驱动报错文案与校验基准）与**API 别名**（保持既有公共方法名不变）；
本模块只额外提供带 ``name`` 参数的便捷转发方法，使调用方不必持有具体
client 引用。
"""

from typing import Any, ClassVar

from pykunlun.registry import RegistryManager

from .cfg import OssCfg
from .client import OssClient
from .stat import ObjectStat


class OssManager(RegistryManager[OssClient, OssCfg]):
    """
    对象存储客户端管理器（类注册表 + 实例注册表）。

    ``Oss`` 取自 OSS（Object Storage Service，对象存储服务）的首字母缩写，
    本库将其用作中性泛称，不特指阿里云同名产品。

    维护两张注册表：

      - **类注册表** ``oss_type -> OssClient 子类(class)``：管理各存储类型对应的实现类。
        注册键取自类自身的 :attr:`~OssClient.oss_type`（自动小写归一化），无需调用方显式提供。
        通过 :meth:`register_client_class` 注册后，即可用 :meth:`register_client` 直接传入 :class:`OssCfg`，
        由本管理器按 ``cfg.oss_type`` 工厂化创建实例——调用方无需手动 ``new``。
      - **实例注册表** ``name -> OssClient 实例``：管理绑定具体配置的客户端实例，
        每个实例绑定一份 :class:`OssCfg`。同一管理器可注册多份不同配置的实例，
        通过名称（别名）区分——典型场景是按环境隔离（``name="dev"`` / ``name="prod"``）
        或按业务模块命名（如 ``"report_files"``、``"user_avatars"``）。

    :attr:`DEFAULT_INSTANCE_NAME` 为默认实例名称。除 :meth:`register_client_class`
    （按类自身 oss_type 归档）与 :meth:`register_client`（须显式提供 name）外，
    其余方法的 ``name`` 参数均可省略，省略时使用默认名称。

    注册动词的完整契约（``replace`` 覆盖开关、实例分支运行时类型校验、
    ``config_loader`` 在锁外调用且可能被并发/重复调用的语义）见
    :class:`~pykunlun.registry.base.RegistryManager`。

    用法示例::

        manager = OssManager()

        # 1) 注册实现类（oss_type 取自类自身，一次性）
        manager.register_client_class(LocalOssClient)

        # 2) 注册实例：直接传 OssCfg，按 cfg.oss_type 自动 new
        cfg = OssCfg(oss_type='local', bucket='demo', storage_options={'base_dir': '/data/oss'})
        manager.register_client("default", cfg)

        # 也仍可显式传入已构造的实例（不依赖类注册表）
        # manager.register_client("default", LocalOssClient(cfg))

        # 3) 通过管理器直接操作（name 可省略，默认 "default"）
        manager.put_object('a/b.txt', 'hello')
        text = manager.get_object_text('a/b.txt')

        # 指定 name 操作非默认实例
        manager.put_object('a.txt', 'x', name="other")
    """

    # region ======== 注册表词汇常量与 API 别名（骨架见 RegistryManager） ========
    # 词汇常量驱动基座报错文案与校验基准（逐条语义见 RegistryManager 模块 docstring）
    TYPE_ATTR = 'oss_type'
    # OssClient 是抽象基类，mypy type-abstract 规则禁止抽象类赋给 type[OssClient]，
    # 故按基座原样声明 type[Any]（仅用于 issubclass 校验与报错文案，无精度损失）
    ITEM_BASE: ClassVar[type[Any]] = OssClient
    CFG_CLS: ClassVar[type[OssCfg]] = OssCfg
    REGISTER_CLASS_METHOD_NAME = 'register_client_class'
    REGISTER_INSTANCE_METHOD_NAME = 'register_client'

    # 既有公共 API 名以别名保持不变（从特化后的基类取方法，保签名完整特化）；
    register_client_class = RegistryManager[OssClient, OssCfg].register_class
    unregister_client_class = RegistryManager[OssClient, OssCfg].unregister_class
    get_client_class = RegistryManager[OssClient, OssCfg].get_class
    get_registered_client_types = RegistryManager[OssClient, OssCfg].get_registered_types
    register_client = RegistryManager[OssClient, OssCfg].register_instance
    unregister_client = RegistryManager[OssClient, OssCfg].unregister_instance
    get_client = RegistryManager[OssClient, OssCfg].get_instance
    # get_registered_names / get_config_loader 与基座同名同义，直接继承
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
        """
        self.get_client(name).put_object(key, data, content_type, metadata, bucket)

    def delete_object(self, key: str, bucket: str | None = None,
                      name: str | None = None) -> None:
        """
        删除对象，幂等（透传 :meth:`OssClient.delete_object`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。

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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。

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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
        """
        return self.get_client(name).exists(key, bucket)

    def stat(self, key: str, bucket: str | None = None,
             name: str | None = None) -> ObjectStat | None:
        """
        读取对象元信息（透传 :meth:`OssClient.stat`）。

        Args:
            key: 逻辑键。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。

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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
        """
        return self.get_client(name).list_objects(prefix, delimiter, bucket)

    def list_keys(self, prefix: str = '', delimiter: str | None = None,
                  bucket: str | None = None,
                  name: str | None = None) -> list[str]:
        """
        列举对象键（透传 :meth:`OssClient.list_keys`）。

        Args:
            prefix: 逻辑键前缀，空串表示全部。
            delimiter: 分隔符，语义同 :meth:`OssClient.list_objects`。
            bucket: 桶名；省略时使用客户端配置的默认桶（``cfg.bucket``）。
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。

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
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
        """
        return self.get_client(name).presigned_url(key, expires, bucket)
    # endregion

    # region ======== 生命周期（透传 OssClient.close） ========
    def close(self, name: str | None = None) -> None:
        """
        释放指定实例的底层资源（透传 :meth:`OssClient.close`）。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_INSTANCE_NAME`。
        """
        self.get_client(name).close()
    # endregion

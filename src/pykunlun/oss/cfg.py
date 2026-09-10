"""
对象存储连接配置。

封装对象存储访问所需的所有参数，覆盖本地目录与云上对象存储（阿里云 OSS 等）。
``OssCfg`` 为纯数据容器，自身不做任何校验；各实现的校验与默认值补全交由
:meth:`pykunlun.oss.client.OssClient._validate_and_prepare_cfg`
在构造客户端实例时自动完成。

字段按通用性分两层（均为可选，实现按需校验）：

  - 通用强类型字段：``oss_type`` / ``bucket`` / ``region`` / ``endpoint`` /
    ``access_key_id`` / ``access_key_secret`` / ``security_token`` / ``prefix`` /
    ``read_only``，跨实现语义一致——云端五项即 S3 协议的连接身份
    （region / endpoint / AccessKeyId / SecretAccessKey / STS token），
    为 AWS S3、阿里云 OSS、腾讯云 COS、MinIO 等 S3 兼容家族所共有；
  - :attr:`storage_options`：实现类特异化参数（dict），只被某一个实现消费，
    键由各实现类以类常量声明并在构造时校验，如
    :class:`~pykunlun.oss.local_client.LocalOssClient` 的 :attr:`~pykunlun.oss.local_client.LocalOssClient.EXT_BASE_DIR`
    （``'base_dir'``，本地存储根目录）。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class OssCfg:
    """
    对象存储访问配置。

    ``Oss`` 取自 OSS（Object Storage Service，对象存储服务）的首字母缩写，
    本库将其用作中性泛称，不特指阿里云同名产品。

    封装对象存储访问所需的所有参数。顶层字段除 ``read_only`` 外均可选
    （默认 ``None`` 表示未设置），必填性与默认值交由各
    :class:`~pykunlun.oss.client.OssClient` 实现的
    :meth:`~pykunlun.oss.client.OssClient._validate_and_prepare_cfg`
    在构造客户端时校验与补全，便于仅需少量参数的实现（如本地目录存储，
    仅需 :attr:`storage_options` 中的 ``base_dir``）使用。

    Attributes:
        oss_type: 存储类型标识，如 ``local``（本地目录）、``aliyun``（阿里云 OSS）；
            省略（``None``）时由所构造的实现类 :attr:`~pykunlun.oss.client.OssClient.oss_type`
            推导，显式传入则校验一致性。
        bucket: 默认存储桶名。一切操作都发生在某个桶上：调用显式传 ``bucket`` 优先，
            缺省回退本默认值，两者皆缺时该次调用抛 :class:`ValueError`。
            本地实现把桶映射为 ``base_dir`` 下的同名子目录（一个根目录可容纳多个桶）。
        region: 云端地域（如 ``cn-hangzhou``）；缺 :attr:`endpoint` 时由本字段推导
            （阿里云为 ``https://oss-{region}.aliyuncs.com``）。
        endpoint: 云端服务地址（如 ``https://oss-cn-hangzhou.aliyuncs.com``）；
            与 :attr:`region` 至少提供一个，同时提供时本字段优先。
        access_key_id: 云端访问密钥 ID。
        access_key_secret: 云端访问密钥 Secret。
        security_token: STS 临时访问凭证；提供时以临时鉴权方式访问（可选）。
        prefix: 全局键前缀（虚拟目录隔离，可选）。规范化后自动以 ``/`` 结尾；
            所有对象键在底层读写时都会自动拼上该前缀，调用方视角的键不含前缀，
            列举结果同样剥除前缀——如同在"子目录"里操作，对调用方透明。
        read_only: 是否只读（默认 ``False``）。
            为 ``True`` 时所有写操作（put/delete/copy/move/upload 等）在客户端层被拒绝
            （抛 :class:`PermissionError`），适用于挂载只读账号或保护重要桶；
            与数据库的启发式 SQL 解析不同，对象存储的 API 天然分读写，拦截是精确的。
        storage_options: 实现类特异化参数（默认空 dict）。顶层强类型字段装"放之各实现
            皆准"的通用连接身份，本 dict 装只被某一个实现消费的参数：键名由各实现类
            以类常量声明（如 :attr:`~pykunlun.oss.local_client.LocalOssClient.EXT_BASE_DIR`，
            本地存储根目录），必填性与取值由该实现类在构造时校验与补全。
            命名沿用 fsspec 系生态的同名惯例（pandas / dask / polars 的
            ``storage_options`` 参数，语义同为"透传给具体存储后端的选项"）。

    校验策略：OssCfg 为纯数据容器，自身不做任何校验；
    各实现的校验与默认值补全（必填字段差异）
    交由 :meth:`OssClient._validate_and_prepare_cfg` 在构造客户端实例时自动完成。
    """

    # ======== 定位：存储类型与桶 ========
    oss_type: str | None = None
    bucket: str | None = None

    # ======== 云端连接身份（S3 协议通用；region/endpoint 至少给一个，同给时 endpoint 优先） ========
    region: str | None = None
    endpoint: str | None = None
    access_key_id: str | None = None
    access_key_secret: str | None = None
    security_token: str | None = None

    # ======== 门面行为 ========
    prefix: str | None = None
    read_only: bool = False

    # ======== 实现类特异化参数（键由各实现类以类常量声明并在构造时校验） ========
    # default_factory 须参数化（dict[str, Any]）：pyright 1.1.411 对裸 dict 作工厂
    # 会把字段类型推断为 dict[Unknown, Unknown] 而误报 partially unknown
    storage_options: dict[str, Any] = field(default_factory=dict[str, Any])

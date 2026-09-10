"""
对象元信息模型。

定义对象存储统一的元信息载体 :class:`ObjectStat`：各实现把底层返回的
元数据（本地 stat / 云端 head）整形为该结构，供 :meth:`OssClient.stat` 与
:meth:`OssClient.list_objects` 返回，屏蔽不同存储之间的字段差异。

字段与 :class:`~pykunlun.oss.cfg.OssCfg` 同构地分两层：通用字段强类型收敛
（key / size / last_modified / etag / content_type / metadata），
实现特有信息收敛到 :attr:`ObjectStat.extra` 扩展 dict。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class ObjectStat:
    """
    对象元信息。

    Attributes:
        key: 对象键（调用方视角的逻辑键，不含 :attr:`~pykunlun.oss.cfg.OssCfg.prefix`）。
        size: 对象字节数；无法获取时为 -1（如部分云存储列举接口不返回 size 的场景）。
        last_modified: 最后修改时间（naive datetime，本地时区）；无法获取时为 None。
        etag: 内容标识（如云端 ETag）；本地目录实现无此概念，为 None。
        content_type: 对象的 MIME 类型（如 ``text/plain``）；写入时未指定且无法推导
            （或列举接口不返回）时为 None。
        metadata: 用户自定义元数据（写入 :meth:`put_object` 时 ``metadata`` 参数的原样读回，
            键不带云厂商前缀）；未指定或列举接口不返回时为 None。
        extra: 实现特异化字段，承载只被某一个实现认知的信息（如 S3 家族的 ``version_id`` /
            ``storage_class``、归档对象的解冻状态）；键由各实现类以 ``EXT_*`` 类常量声明
            （约定同 :attr:`~pykunlun.oss.cfg.OssCfg.storage_options`）。仅承载信息，
            调用方核心逻辑不得依赖其键做分支（跨实现语义一致的约定）；无则为空 dict。

    注意：云端列举接口（ListObjectsV2）不返回 content_type 与用户元数据，
    故 :meth:`~pykunlun.oss.client.OssClient.list_objects` 结果中二者恒为 None；
    需要元数据请对单个对象调用 :meth:`~pykunlun.oss.client.OssClient.stat`
    （本地目录实现列举时同样不读旁车，保持跨实现一致）。
    实现特有字段同理：列举接口一般不返回，列举结果中 :attr:`extra` 通常为空 dict，
    实现可按底层接口能力填充（如 S3 列举返回的 StorageClass）。
    """

    key: str
    size: int = -1
    last_modified: datetime | None = None
    etag: str | None = None
    content_type: str | None = None
    metadata: dict[str, str] | None = None
    # default_factory 参数化对齐 OssCfg.storage_options：裸 dict 作工厂会被 pyright 判 partially unknown
    extra: dict[str, Any] = field(default_factory=dict[str, Any])

"""
对象存储客户端策略抽象基类。

:class:`OssClient` 绑定一份 :class:`OssCfg` 配置，是对象存储操作的门面入口：
把"因存储而异"的差异收敛为少量底层钩子（读写删探测列举拷贝），
把"放之四海皆准"的公共逻辑（键规范化、前缀拼接、只读拦截、文本编解码、
文件级便捷方法）统一写在本类。新增一种存储 = 继承本类并覆盖少数钩子，
基类的公共逻辑无需改动。

键（key）约定：

  - 调用方统一使用**逻辑键**：``/`` 分隔的 POSIX 风格**相对**路径
    （如 ``a/b/c.txt``），不含 :attr:`~pykunlun.oss.cfg.OssCfg.prefix` 前缀、
    不以 ``/`` 开头；
  - 键的处理直接复用 :mod:`pykunlun.util.pathutil` 的通用字符串路径工具：
    键先经键预处理钩子 :meth:`OssClient._prepare_key` 规范化（默认实现为
    :func:`pathutil.normpath`：统一分隔符、消解 ``..``、拒绝空键；normpath
    同时支持相对与绝对路径，**不剥开头 ``/``**，故以 ``/`` 开头的入参会原样
    保留在键中——相对性由调用方遵守约定保证），与 ``prefix``（``normpath``
    后补 ``/`` 的目录形态）经 :func:`pathutil.join_path` 合成**物理键**交给
    底层钩子；列举结果经 :func:`pathutil.sub_path` 剥除前缀返回。调用方全程
    只见逻辑键，如同在"子目录"里操作；
  - 本地目录等把键映射到真实文件系统的实现，须自行在文件系统边界做
    包含性校验（见 :class:`~pykunlun.oss.local_client.LocalOssClient` 的 realpath 复核），防止键经
    未消解的 ``..`` 或符号链接逃逸出存储根目录。

桶（bucket）约定：

  - 桶是键的命名空间，一切操作都发生在某个桶上：调用显式传 ``bucket`` 优先，
    缺省回退配置默认桶（``cfg.bucket``），两者皆缺时抛 :class:`ValueError`；
  - 桶不进入物理键，与 prefix、键是三个正交维度，由各实现自行映射到底层寻址
    （本地目录实现映射为 ``base_dir/<bucket>/`` 子目录，云端实现作为 API 的桶参数）；
  - 复制/移动支持跨桶：``dst_bucket`` 省略时回退 ``bucket``（同桶），
    源与目标的桶寻址相互独立（语义对齐 S3 CopyObject）。

异常约定（各实现必须遵守，保证跨实现语义一致）：

  - :meth:`get_object` / :meth:`download_file` / :meth:`get_object_text`：
    键不存在时抛内置 :class:`FileNotFoundError`；
  - :meth:`stat`：键不存在时返回 ``None``（与 :meth:`exists` 配合使用）；
  - :meth:`delete_object`：幂等，键不存在时静默成功；
  - 只读配置（``cfg.read_only=True``）下所有写操作抛 :class:`PermissionError`。

方法分层：

  【配置层】—— 构造时即校验
    - ``oss_type``             : 存储类型标识（实现类硬编码的类级常量）；
                                 cfg.oss_type 省略时由本类推导，显式传入则校验一致性。
    - ``_validate_and_prepare_cfg``: 校验 cfg 必填字段并补全可推导默认值，
                                 由 ``__init__`` 自动调用，**构造即校验并补全**。

  【底层钩子】—— 子类必须实现（首参均为桶名 bucket，物理键语义；
                 与 S3 的 PUT/GET/DELETE/HEAD/LIST/COPY 一一对应）
    - ``_put`` / ``_get`` / ``_delete`` / ``_head`` / ``_list`` / ``_copy``
    - 流契约：``_put`` 入参与 ``_get`` 返回值均为二进制流（详见各钩子 docstring）；
      存在性探测由 ``_head`` 独立承担（``exists`` 即 ``_head(...) is not None``）

  【可选钩子】—— 子类按需覆盖以获得更优实现
    - ``_upload_file`` / ``_download_file``: 大文件直传/直下（如云端分片断点续传），
                                 默认实现为流式转发到 ``_put`` / ``_get``（不整读进内存）；
    - ``_presigned_url``       : 生成临时签名 URL；不支持签名语义的实现（本地目录）
                                 可返回等价可打开的地址或抛 :class:`NotImplementedError`。

  【公共接口】—— 基类实现，组合上述钩子，调用方直接使用
    - ``put_object`` / ``get_object`` / ``get_object_text``: 字节/文本级读写；
    - ``delete_object`` / ``copy_object`` / ``move_object``: 删除与复制/移动；
    - ``exists`` / ``stat`` / ``list_objects`` / ``list_keys``: 探测与列举；
    - ``upload_file`` / ``download_file``  : 本地文件与对象互转（独立于对象键操作族
      的文件桥接便捷方法；上传按扩展名猜 content_type）；
    - ``presigned_url`` / ``close``。

  【元数据约定】—— 跨实现语义一致
    - ``put_object`` / ``upload_file`` 支持 ``content_type`` 与 ``metadata``（用户自定义
      元数据，键为裸键名，不带 ``x-oss-meta-`` / ``x-amz-meta-`` 前缀，前缀由云端实现内部翻译）；
    - ``stat`` 经 :class:`ObjectStat` 读回 ``content_type`` / ``metadata``；
    - 复制/移动保留元数据（云端走 COPY 语义，本地搬运旁车），不暴露覆盖式（REPLACE）选项。

调用链示意::

    client.put_object('a/b.txt', 'hello')
      └─ bucket = 入参 bucket or cfg.bucket  # 皆缺抛 ValueError
      └─ join_path(prefix, normpath('a/b.txt'))   # pathutil：规范化 + 拼前缀
      └─ data.encode('utf-8')               # 文本统一按 UTF-8 编码
      └─ _put('<bucket>', '<prefix>/a/b.txt', BytesIO(b'hello')) # 钩子（二进制流），各实现落盘/上传
"""

import mimetypes
import shutil
from abc import ABC, abstractmethod
from contextlib import closing
from io import BytesIO
from typing import Any, BinaryIO, ClassVar

from pykunlun.util import fileutil, logutil
from pykunlun.util.pathutil import join_path, normpath, sub_path

from .cfg import OssCfg
from .stat import ObjectStat

log = logutil.getLogger(__name__)


class OssClient(ABC):
    """
    对象存储客户端策略抽象基类（绑定一份 :class:`OssCfg` 配置）。

    ``Oss`` 取自 OSS（Object Storage Service，对象存储服务）的首字母缩写，
    本库将其用作中性泛称，不特指阿里云同名产品。

    门面 + 模板方法：公共方法（键规范化、前缀、只读拦截、编解码、文件便捷方法）
    在基类实现；与具体存储相关的读写删列举拷贝收敛为 ``_`` 起头的底层钩子，
    由各实现提供。通常直接构造使用（构造时自动校验配置），也可注册到
    :class:`OssManager` 按名称管理::

        client = LocalOssClient(OssCfg(bucket='demo', storage_options={'base_dir': '/data/oss'}))
        client.put_object('reports/2026.txt', 'hello')        # 走默认桶 demo
        client.put_object('tmp/a.txt', 'x', bucket='scratch')  # 显式指定桶
        text = client.get_object_text('reports/2026.txt')
        keys = client.list_keys('reports/')
    """

    # region ======== 构造与配置校验 ========

    def __init__(self, cfg: OssCfg) -> None:
        """
        Args:
            cfg: 绑定的对象存储配置对象。

        Raises:
            ValueError: 显式声明的 ``oss_type`` 与本实现类 :attr:`oss_type` 不一致、
                或 :meth:`_validate_and_prepare_cfg` 校验不通过时抛出。
        """
        self.cfg = cfg
        # oss_type：cfg 未声明（None）时由本实现类的 oss_type 推导；显式声明则校验
        # 一致性，不符即说明配置用错了实现类。
        if cfg.oss_type is None:
            cfg.oss_type = self.oss_type
        elif cfg.oss_type != self.oss_type:
            raise ValueError(
                f"存储类型不匹配：配置 oss_type={cfg.oss_type!r}，"
                f"实现类 {type(self).__name__} 仅支持 {self.oss_type!r}"
            )
        # 全局键前缀：归一为 '' 或以 '/' 结尾的目录形态，供键合成/剥除使用
        # cfg.prefix 空白时归一为 ''；否则 normpath + '/' 组合为目录形态
        self._prefix = (normpath(cfg.prefix) + '/'
                        if cfg.prefix and cfg.prefix.strip() else '')
        # 构造即校验+补全：不同存储的必填字段与默认值不同，交由各实现判定
        self._validate_and_prepare_cfg()

    def __setattr__(self, name: str, value: Any) -> None:
        """
        拦截实例属性赋值，保护 :attr:`oss_type` 与 :attr:`cfg` 不被运行时篡改。

        - ``oss_type``：基类以 ClassVar 声明（无默认值），但子类为满足约束会用类级常量
          ``oss_type = 'local'`` 覆盖——该常量是普通字符串（非 data descriptor），
          ``instance.oss_type = x`` 将悄悄创建实例级遮蔽。
          本方法显式抛 :class:`AttributeError` 堵住此缺口。
        - ``cfg``：允许构造时首次赋值（由 :meth:`__init__` 触发），构造完成后禁止替换。
          绑定的 cfg 已经过 :meth:`_validate_and_prepare_cfg` 校验与默认值补全，
          运行期整体替换会绕过校验、破坏不变量；如需变更配置请重新构造实例。
        其余属性（前缀缓存、云端客户端句柄等）照常赋值。

        Raises:
            AttributeError: 尝试给实例的 ``oss_type`` 赋值，或构造完成后再次给 ``cfg``
                赋值时抛出。
        """
        if name == 'oss_type':
            raise AttributeError(
                f"{type(self).__name__}.oss_type 是实现类硬编码的类级常量，"
                f"代表本类所属的存储类型，禁止运行时修改。"
            )
        if name == 'cfg' and 'cfg' in self.__dict__:
            raise AttributeError(
                f"{type(self).__name__}.cfg 在构造完成后不可替换（绑定配置已经校验），"
                f"如需变更配置请重新构造实例。"
            )
        super().__setattr__(name, value)

    def _validate_and_prepare_cfg(self) -> None:
        """
        校验并补全绑定的 :attr:`cfg`：必填字段缺失报错，可推导字段填默认。

        不同存储所需字段与默认值不同，故本方法为**实例方法**，由各实现按自身规则覆盖。
        本默认实现为空（无通用必填项），如本地目录实现仅校验 ``storage_options``
        中的 ``base_dir``、云端实现校验 region/endpoint 与密钥等。

        本方法由 :meth:`__init__` 自动调用，确保构造出的实例配置一定有效且完整。

        Raises:
            ValueError: 必填字段为空或取值非法时抛出。
        """
        pass

    # endregion

    # region ======== 键预处理钩子 ========

    def _prepare_key(self, key: str) -> str:
        """
        键预处理钩子：公共方法在合成物理键前对逻辑键的统一预处理（模板方法）。

        默认实现按 OSS 键要求规范化（:func:`pathutil.normpath`：反斜杠统一为
        ``/``、消解 ``..``、剥尾部 ``/``、拒绝空键）。子类可按需覆写以施加额外
        策略（如统一大小写、按业务加固定前缀、拒绝特定字符等）；覆写时必须保持
        键的恒等性——同一逻辑键在所有操作中须得到同一处理结果，否则读写将错位。

        Args:
            key: 调用方传入的逻辑键。

        Returns:
            预处理后的逻辑键。

        Raises:
            ValueError: 默认实现下键为空时抛出。
        """
        return normpath(key)

    # endregion

    # region ======== 只读守卫 ========

    def _ensure_writable(self, op: str) -> None:
        """
        写操作前置守卫：只读配置下拒绝一切写操作。

        对象存储的 API 天然分读写（不像数据库需解析 SQL），故本守卫是精确拦截，
        无需类似数据库只读代理的包装层。

        Args:
            op: 操作名，用于报错提示（如 ``put_object``）。

        Raises:
            PermissionError: ``cfg.read_only=True`` 时抛出。
        """
        if self.cfg.read_only:
            raise PermissionError(
                f"配置为只读（read_only=True），拒绝写操作: {op}"
            )

    # endregion

    # region ======== 桶解析 ========

    def _effective_bucket(self, bucket: str | None) -> str:
        """
        解析本次调用生效的桶：显式入参优先，缺省回退配置默认桶（模板方法）。

        Args:
            bucket: 调用方传入的桶名；None 或空白视为未传。

        Returns:
            生效的桶名（非空），直接交给底层钩子的 ``bucket`` 参数。

        Raises:
            ValueError: 入参与配置默认桶（``cfg.bucket``）均为空时抛出。
        """
        effective = bucket if (bucket and bucket.strip()) else self.cfg.bucket
        if not effective or not effective.strip():
            raise ValueError(
                "未指定 bucket：调用入参与配置默认桶（cfg.bucket）均为空；"
                "请在调用时显式传 bucket，或在 OssCfg 中配置默认桶"
            )
        return effective

    # endregion

    # region ======== 底层钩子（子类必须实现，首参桶名 + 物理键语义） ========

    #: 本实现类代表的存储类型标识（如 ``local``、``aliyun``）。
    #:
    #: 由各实现类以**类级常量**形式硬编码提供，标识"本类是哪种存储的实现"。
    #: 基类以 ClassVar 声明（无默认值）强制子类在类级覆盖；
    #: 其运行时不可修改性由 :meth:`__setattr__` 显式拦截保证（详见该方法的说明）。
    oss_type: ClassVar[str]

    @abstractmethod
    def _put(self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None,
             metadata: dict[str, str] | None = None) -> None:
        """
        写入对象（覆盖式）。子类必须实现。

        Args:
            bucket: 桶名（非空，由基类经 :meth:`_effective_bucket` 解析后传入）。
            key: 物理键（已规范化、已拼前缀）。
            data: 内容**二进制流**，约定可 seek（云端 SDK 依此确定 ContentLength，
                基类传入的 BytesIO 与文件句柄均满足）。实现方只读取、**不关闭**，
                流的生命周期归调用方；建议经流式转写落盘/上传（如
                ``shutil.copyfileobj``），勿 ``read()`` 全量进内存。
            content_type: 对象 MIME 类型；None 表示不指定（交由存储端默认）。
            metadata: 用户自定义元数据（裸键名，无云厂商前缀）；None 表示不指定。
                值约定为字符串（云端元数据仅支持字符串，非字符串由调用方先行序列化）。
        """
        pass

    @abstractmethod
    def _get(self, bucket: str, key: str) -> BinaryIO:
        """
        读取对象内容。子类必须实现。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。

        Returns:
            已打开、定位在起始位置的**二进制流**，由调用方负责关闭
            （基类公共方法经 :func:`contextlib.closing` 确保释放）。

        Raises:
            FileNotFoundError: 键不存在时抛出。约定在**钩子调用时同步抛出**
                （本地为 open 失败、云端为请求 404），而非首次读取流时——
                调用方依赖此时序在失败时不产生目标文件/目录。
        """
        pass

    @abstractmethod
    def _delete(self, bucket: str, key: str) -> None:
        """
        删除单个对象。子类必须实现。

        约定幂等：键不存在时静默成功，不抛异常。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。
        """
        pass

    @abstractmethod
    def _head(self, bucket: str, key: str) -> ObjectStat | None:
        """
        读取单个对象的元信息快照（HEAD 语义：取元数据、不取内容）。子类必须实现。

        钩子族的唯一存在性原语：:meth:`exists` 即由 ``_head(...) is not None``
        推导，子类无需（也不应）再提供单独的存在性钩子。返回类型沿用 POSIX
        "stat" 词汇，与公共方法 :meth:`stat` 及 :meth:`pathlib.Path.stat` 一脉相承。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。

        Returns:
            对象元信息；不存在时返回 ``None``。返回的 ``key`` 为物理键，
            由基类统一剥除前缀后再回给调用方。本类型同时是 :meth:`list_objects`
            的列举项类型，列举路径下 ``content_type`` / ``metadata`` 恒为 None
            （列举接口不返回元数据）。
        """
        pass

    @abstractmethod
    def _list(self, bucket: str, prefix: str, delimiter: str | None) -> list[ObjectStat]:
        """
        列举指定桶内物理键前缀下的对象。子类必须实现。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            prefix: 物理键前缀（已拼全局前缀；空串表示全部）。
            delimiter: 分隔符（如 ``/``，用于"目录"层列举）；
                不支持该语义的实现可忽略，仅按前缀扁平列举。

        Returns:
            对象元信息列表，``key`` 为物理键，由基类统一剥除前缀后再回给调用方。
        """
        pass

    @abstractmethod
    def _copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        """
        服务端复制对象（不经过调用方内存）。子类必须实现。

        源与目标的桶相互独立，支持跨桶复制（语义对齐 S3 CopyObject 的
        ``x-amz-copy-source: bucket/key``——源/目标桶本就是两个独立寻址参数）；
        不支持跨桶的实现可校验两桶相同、否则抛 :class:`ValueError`。

        Args:
            src_bucket: 源桶名（非空，由基类解析后传入）。
            src_key: 源物理键。
            dst_bucket: 目标桶名（非空，由基类解析后传入）。
            dst_key: 目标物理键（覆盖式）。

        Raises:
            FileNotFoundError: 源对象不存在时抛出。
        """
        pass

    # endregion

    # region ======== 可选钩子（子类按需覆盖） ========

    def _upload_file(self, bucket: str, key: str, local_path: str,
                     content_type: str | None = None,
                     metadata: dict[str, str] | None = None) -> None:
        """
        从本地文件上传对象。子类可覆盖以获得更优实现（如分片断点续传）。

        默认实现为把文件句柄流式转发给 :meth:`_put`（不整读进内存）。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。
            local_path: 本地源文件路径。
            content_type: 对象 MIME 类型；None 表示不指定。
            metadata: 用户自定义元数据（裸键名）；None 表示不指定。

        Raises:
            FileNotFoundError: 本地文件不存在时抛出（由文件打开触发）。
        """
        with open(local_path, 'rb') as f:
            self._put(bucket, key, f, content_type, metadata)

    def _download_file(self, bucket: str, key: str, local_path: str) -> None:
        """
        下载对象到本地文件。子类可覆盖以获得更优实现（如分片断点续传）。

        默认实现为经 :meth:`_get` 取流后分块转写（不整读进内存）。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。
            local_path: 本地目标文件路径（父目录由本方法自动创建）。

        Raises:
            FileNotFoundError: 键不存在时抛出（由 :meth:`_get` 触发；此时不产生
                目标目录与文件）。
        """
        # 先开流：键不存在时此处即抛，目标目录/文件不受影响
        with closing(self._get(bucket, key)) as src:
            fileutil.make_parent_dirs(local_path)
            with open(local_path, 'wb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)

    def _presigned_url(self, bucket: str, key: str, expires: int) -> str:
        """
        生成对象的临时访问 URL。子类可覆盖。

        默认实现抛 :class:`NotImplementedError`（无签名语义的实现可不覆盖但须遵守
        本约定）；具备等价"可打开地址"语义的实现（如本地目录返回 ``file://`` URI）
        也可覆盖为返回该地址（并注明无过期语义）。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。
            expires: 有效期秒数。

        Returns:
            可直接访问对象的 URL。

        Raises:
            NotImplementedError: 当前实现不支持生成访问 URL 时抛出。
        """
        raise NotImplementedError(
            f"{type(self).__name__} 不支持生成临时访问 URL；"
            f"请改用 download_file 先落地到本地，或为该实现覆盖 _presigned_url"
        )

    def close(self) -> None:
        """
        释放底层资源。

        默认实现为空操作（无长连接的客户端无需集中释放）；
        持有连接池等长期资源的实现应覆盖本方法。
        """
        pass

    # endregion

    # region ======== 公共接口（写） ========

    def put_object(self, key: str, data: bytes | str, content_type: str | None = None,
                   metadata: dict[str, str] | None = None,
                   bucket: str | None = None) -> None:
        """
        写入对象（覆盖式）。

        Args:
            key: 逻辑键。
            data: 对象内容；``str`` 统一按 UTF-8 编码后写入。
            content_type: 对象 MIME 类型（如 ``image/png``）；None 表示不指定。
            metadata: 用户自定义元数据，键为裸键名（不带 ``x-oss-meta-`` /
                ``x-amz-meta-`` 前缀，云端实现内部翻译），值约定为字符串；
                None 表示不指定。再次覆盖写同一键且未指定元数据时，
                旧元数据随之清除（覆盖式语义）。
            bucket: 桶名；省略时使用配置默认桶（``cfg.bucket``），两者皆缺时抛
                :class:`ValueError`（下同，各方法语义一致）。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            PermissionError: 只读配置下抛出。
        """
        effective_bucket = self._effective_bucket(bucket)
        self._ensure_writable('put_object')
        physical = join_path(self._prefix, self._prepare_key(key))
        payload = data.encode('utf-8') if isinstance(data, str) else data
        self._put(effective_bucket, physical, BytesIO(payload), content_type, metadata)
        log.debug("put_object: %s (%d bytes)", physical, len(payload))

    def delete_object(self, key: str, bucket: str | None = None) -> None:
        """
        删除对象（幂等，键不存在时静默成功）。

        Args:
            key: 逻辑键。
            bucket: 桶名，语义同 :meth:`put_object`。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            PermissionError: 只读配置下抛出。
        """
        self._ensure_writable('delete_object')
        self._delete(self._effective_bucket(bucket),
                     join_path(self._prefix, self._prepare_key(key)))

    def copy_object(self, src_key: str, dst_key: str, bucket: str | None = None,
                    dst_bucket: str | None = None) -> None:
        """
        复制对象（服务端完成，不经过调用方内存；目标覆盖式）。

        支持跨桶复制：省略 ``dst_bucket`` 时与 ``bucket`` 同桶。

        Args:
            src_key: 源逻辑键。
            dst_key: 目标逻辑键。
            bucket: 源桶名，语义同 :meth:`put_object`。
            dst_bucket: 目标桶名；省略（None 或空白）时回退 ``bucket``（同桶复制）。

        Raises:
            ValueError: 任一键非法或未指定桶时抛出。
            PermissionError: 只读配置下抛出（复制产生新写入）。
            FileNotFoundError: 源对象不存在时抛出。
        """
        src_b = self._effective_bucket(bucket)
        dst_b = self._effective_bucket(dst_bucket) if (dst_bucket and dst_bucket.strip()) else src_b
        self._ensure_writable('copy_object')
        self._copy(src_b,
                   join_path(self._prefix, self._prepare_key(src_key)),
                   dst_b,
                   join_path(self._prefix, self._prepare_key(dst_key)))
        log.debug("copy_object: %s -> %s", src_key, dst_key)

    def move_object(self, src_key: str, dst_key: str, bucket: str | None = None,
                    dst_bucket: str | None = None) -> None:
        """
        移动对象（默认为复制 + 删除源；同存储内无原子性保证，子类可覆盖优化）。

        支持跨桶移动：省略 ``dst_bucket`` 时与 ``bucket`` 同桶。

        Args:
            src_key: 源逻辑键。
            dst_key: 目标逻辑键。
            bucket: 源桶名，语义同 :meth:`copy_object`。
            dst_bucket: 目标桶名；省略（None 或空白）时回退 ``bucket``（同桶移动）。

        Raises:
            ValueError: 任一键非法或未指定桶时抛出。
            PermissionError: 只读配置下抛出。
            FileNotFoundError: 源对象不存在时抛出。
        """
        src_b = self._effective_bucket(bucket)
        dst_b = self._effective_bucket(dst_bucket) if (dst_bucket and dst_bucket.strip()) else src_b
        self._ensure_writable('move_object')
        physical_src = join_path(self._prefix, self._prepare_key(src_key))
        physical_dst = join_path(self._prefix, self._prepare_key(dst_key))
        self._copy(src_b, physical_src, dst_b, physical_dst)
        self._delete(src_b, physical_src)
        log.debug("move_object: %s -> %s", src_key, dst_key)

    def upload_file(self, key: str, local_path: str, content_type: str | None = None,
                    metadata: dict[str, str] | None = None,
                    bucket: str | None = None) -> None:
        """
        上传本地文件为对象（覆盖式）。

        Args:
            key: 逻辑键。
            local_path: 本地源文件路径。
            content_type: 对象 MIME 类型；省略（None）时按源文件扩展名经标准库
                :func:`mimetypes.guess_type` 推导（如 ``.txt`` → ``text/plain``，
                未知扩展名仍为 None）。
            metadata: 用户自定义元数据，语义同 :meth:`put_object`。
            bucket: 桶名，语义同 :meth:`put_object`。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            PermissionError: 只读配置下抛出。
            FileNotFoundError: 本地文件不存在时抛出。
        """
        effective_bucket = self._effective_bucket(bucket)
        self._ensure_writable('upload_file')
        if content_type is None:
            content_type = mimetypes.guess_type(local_path)[0]
        self._upload_file(effective_bucket,
                          join_path(self._prefix, self._prepare_key(key)), local_path,
                          content_type, metadata)
        log.debug("upload_file: %s <- %s", key, local_path)

    # endregion

    # region ======== 公共接口（读） ========

    def get_object(self, key: str, bucket: str | None = None) -> bytes:
        """
        读取对象内容字节。

        Args:
            key: 逻辑键。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            对象内容字节。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            FileNotFoundError: 键不存在时抛出。
        """
        with closing(self._get(self._effective_bucket(bucket),
                               join_path(self._prefix, self._prepare_key(key)))) as stream:
            data = stream.read()
        log.debug("get_object: %s (%d bytes)", key, len(data))
        return data

    def get_object_text(self, key: str, encoding: str = 'utf-8',
                        bucket: str | None = None) -> str:
        """
        读取对象内容并按指定编码解码为文本。

        Args:
            key: 逻辑键。
            encoding: 文本编码，默认 UTF-8。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            解码后的文本。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            FileNotFoundError: 键不存在时抛出。
            UnicodeDecodeError: 内容与指定编码不符时抛出。
        """
        return self.get_object(key, bucket).decode(encoding)

    def exists(self, key: str, bucket: str | None = None) -> bool:
        """
        判断对象是否存在（由 :meth:`_head` 推导，``_head(...) is not None``）。

        Args:
            key: 逻辑键。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            存在返回 True。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
        """
        return self._head(self._effective_bucket(bucket),
                          join_path(self._prefix, self._prepare_key(key))) is not None

    def stat(self, key: str, bucket: str | None = None) -> ObjectStat | None:
        """
        读取对象元信息。

        Args:
            key: 逻辑键。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            对象元信息（``key`` 已剥除前缀为逻辑键）；不存在时返回 ``None``。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
        """
        prefix = self._prefix
        st = self._head(self._effective_bucket(bucket),
                        join_path(prefix, self._prepare_key(key)))
        if st is None:
            return None
        return ObjectStat(
            key=sub_path(st.key, self._prefix),
            size=st.size,
            last_modified=st.last_modified,
            etag=st.etag,
            content_type=st.content_type,
            metadata=st.metadata,
            extra=st.extra,
        )

    def list_objects(self, prefix: str = '', delimiter: str | None = None,
                     bucket: str | None = None) -> list[ObjectStat]:
        """
        列举指定前缀下的对象元信息。

        Args:
            prefix: 逻辑键前缀，空串表示全部。
            delimiter: 分隔符（如 ``/``，用于只列举"目录"层）；
                不支持该语义的实现按前缀扁平列举（本地目录实现即如此）。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            对象元信息列表（``key`` 已剥除前缀为逻辑键），顺序由实现决定。

        Raises:
            ValueError: 前缀非法或未指定桶时抛出。
        """
        effective_bucket = self._effective_bucket(bucket)
        # 物理前缀：配置前缀（已是目录形态）+ 目录形态的列举前缀；
        # 空前缀时 join 仅为配置前缀本身（列举全部）
        physical_prefix = (self._prefix + normpath(prefix) + '/'
                           if prefix.strip() else self._prefix)
        stats = self._list(effective_bucket, physical_prefix, delimiter)
        return [
            ObjectStat(
                key=sub_path(st.key, self._prefix),
                size=st.size,
                last_modified=st.last_modified,
                etag=st.etag,
                # 云端列举接口不返回元数据（本地列举亦不读旁车），保持恒 None
                content_type=st.content_type,
                metadata=st.metadata,
                # 实现特有字段按底层接口能力透传（如 S3 列举返回的 StorageClass）
                extra=st.extra,
            )
            for st in stats
        ]

    def list_keys(self, prefix: str = '', delimiter: str | None = None,
                  bucket: str | None = None) -> list[str]:
        """
        列举指定前缀下的对象键（:meth:`list_objects` 的轻量便捷版）。

        Args:
            prefix: 逻辑键前缀，空串表示全部。
            delimiter: 分隔符，语义同 :meth:`list_objects`。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            逻辑键列表。

        Raises:
            ValueError: 前缀非法或未指定桶时抛出。
        """
        return [st.key for st in self.list_objects(prefix, delimiter, bucket)]

    def download_file(self, key: str, local_path: str, bucket: str | None = None) -> None:
        """
        下载对象到本地文件（目标父目录自动创建）。

        Args:
            key: 逻辑键。
            local_path: 本地目标文件路径。
            bucket: 桶名，语义同 :meth:`put_object`。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            FileNotFoundError: 键不存在时抛出。
        """
        self._download_file(self._effective_bucket(bucket),
                            join_path(self._prefix, self._prepare_key(key)), local_path)
        log.debug("download_file: %s -> %s", key, local_path)

    def presigned_url(self, key: str, expires: int = 3600,
                      bucket: str | None = None) -> str:
        """
        生成对象的临时访问 URL（下载语义）。

        Args:
            key: 逻辑键。
            expires: 有效期秒数，默认 1 小时。
            bucket: 桶名，语义同 :meth:`put_object`。

        Returns:
            可直接访问对象的 URL。

        Raises:
            ValueError: 键非法或未指定桶时抛出。
            NotImplementedError: 当前实现不支持生成访问 URL 时抛出。
        """
        return self._presigned_url(self._effective_bucket(bucket),
                                   join_path(self._prefix, self._prepare_key(key)), expires)

    # endregion

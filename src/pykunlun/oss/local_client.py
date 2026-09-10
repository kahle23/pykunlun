"""
本地目录对象存储客户端（基于标准库 :mod:`os` / :mod:`pathlib` / :mod:`shutil`，零第三方依赖）。

把一个本地目录视作对象存储的根：桶映射为根目录下的同名子目录，
对象键映射为桶内的相对路径（``/`` 分隔），
读写删列举拷贝全部落为普通文件操作，随 :class:`~pykunlun.oss.OssManager`
开箱即用，也是本地开发/单测场景替代云端存储的默认实现。
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, ClassVar, cast

from pykunlun.util import fileutil, logutil

from .client import OssClient
from .stat import ObjectStat

log = logutil.getLogger(__name__)


class LocalOssClient(OssClient):
    """
    本地目录对象存储客户端。

    以 ``storage_options`` 的 :attr:`EXT_BASE_DIR` 键（``'base_dir'``）为存储根目录；
    桶映射为根目录下的同名子目录（一个根目录可容纳多个"桶"）：调用显式传 ``bucket``
    优先，缺省回退 :attr:`~pykunlun.oss.cfg.OssCfg.bucket` 默认桶，两者皆缺时该次
    调用抛 :class:`ValueError`（桶由基类统一解析，与云端语义一致）。

    与云端的语义差异（有意为之，保持零依赖与可预期）：

      - ``list_objects`` 的 ``delimiter`` 参数被忽略，始终按前缀扁平列举
        （本地文件系统没有"目录汇总"的存储层概念）；
      - :meth:`presigned_url` 返回对象的 ``file://`` URI，无过期与签名语义；
      - "目录"随对象创建、不随对象清空而删除（空目录会残留，不影响读写）。

    元数据经**旁车文件**承载：写入对象时若指定了 ``content_type`` 或 ``metadata``，
    在对象旁落一个 ``<对象文件名>.meta.json``（JSON：``{"content_type": ...,
    "metadata": {...}}``），:meth:`stat` 读回；列举时旁车按保留键过滤、不读内容
    （与云端列举不返回元数据保持一致），复制/移动/删除随之搬运/清理；
    覆盖写未指定元数据时旧旁车清除（覆盖式语义）。
    由此 ``*.meta.json`` 结尾的键成为**保留键**（以该键存对象会被旁车过滤逻辑遮蔽），
    正常使用不会触碰。

    路径安全：本实现把键映射到真实文件系统路径，是所有实现中唯一存在
    "键逃逸"风险的（云端实现里键是不透明字符串，``..`` 无特殊含义）。
    防线在文件系统边界：键先经 :func:`pathutil.normpath` 词汇消解
    （``..`` 弹出上一段），本实现落盘前再用 realpath 复核解析后的绝对路径
    仍位于根目录之内——消解后仍外指的键（如以 ``..`` 开头）与经符号链接
    外指的键都会在此被拒绝（抛 :class:`ValueError`）。
    """

    oss_type: ClassVar[str] = 'local'

    #: 元数据旁车文件后缀（保留键规则，见类 docstring）
    META_SUFFIX: ClassVar[str] = '.meta.json'

    #: :attr:`~pykunlun.oss.cfg.OssCfg.storage_options` 中"本地存储根目录"的键名
    #: （本地实现的必填项；调用方引用本常量，避免散落裸字符串键）
    EXT_BASE_DIR: ClassVar[str] = 'base_dir'

    # region ======== 配置校验 ========

    def _validate_and_prepare_cfg(self) -> None:
        """
        本地实现仅需 :attr:`EXT_BASE_DIR` 键（``storage_options['base_dir']``，本地根目录）。

        键缺失报错；提供则转绝对路径写回 ``storage_options``（相对路径与 ``..`` 段按当前
        工作目录由 :meth:`pathlib.Path.resolve` 解析），后续所有定位都基于该
        绝对路径，客户端构造后工作目录变化不影响行为。
        ``bucket``（默认桶）可选，仅作调用期回退值，构造期不做校验。

        Raises:
            ValueError: base_dir 缺失或为空时抛出。
        """
        options = self.cfg.storage_options
        base_dir = options.get(self.EXT_BASE_DIR)
        if base_dir is None or (isinstance(base_dir, str) and not base_dir.strip()):
            raise ValueError(
                f"本地对象存储配置 storage_options['{self.EXT_BASE_DIR}']（本地根目录）不能为空"
            )
        options[self.EXT_BASE_DIR] = str(Path(base_dir).resolve())

    # endregion

    # region ======== 内部定位 ========

    def _base_dir(self) -> Path:
        """
        解析存储根目录（不含桶）。

        Returns:
            绝对化后的根目录路径。
        """
        # base_dir 已在构造校验时转绝对路径写回 storage_options
        base_dir = self.cfg.storage_options.get(self.EXT_BASE_DIR)
        return Path(base_dir) if base_dir else Path('.')

    def _bucket_dir(self, bucket: str) -> Path:
        """
        解析指定桶的存储目录（根目录下的桶子目录）。

        Args:
            bucket: 桶名（非空，由基类经 :meth:`~pykunlun.oss.client.OssClient._effective_bucket`
                解析后传入）。

        Returns:
            绝对化后的桶目录路径。
        """
        return self._base_dir() / bucket

    def _resolve(self, bucket: str, key: str) -> Path:
        """
        把物理键解析为桶目录内的绝对路径，并复核未逃逸出桶目录。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。

        Returns:
            对应的绝对文件路径。

        Raises:
            ValueError: 解析后的路径逃逸出桶目录时抛出——键消解后仍外指
                （以 ``..`` 开头，如 ``../x``），或键中含指向外部的符号链接。
        """
        base = self._bucket_dir(bucket)
        # 过滤空段：normpath 保留绝对形态，键可能以 '/' 开头（如 '/a/b'），
        # 按 '/' 切分会产生空首段；过滤后绝对风格键仍解析到桶目录之内
        target = base.joinpath(*(seg for seg in key.split('/') if seg))
        # realpath 复核：键若含未消解的 .. 或指向外部的符号链接，此处会解析到桶目录之外
        real_base = os.path.realpath(base)
        real_target = os.path.realpath(target)
        if real_target != real_base and not real_target.startswith(real_base + os.sep):
            raise ValueError(f"对象键解析后逃逸出存储根目录: {key!r}")
        return target

    # endregion

    # region ======== 元数据旁车（.meta.json） ========

    def _sidecar_path(self, bucket: str, key: str) -> Path:
        """
        解析物理键对应的元数据旁车文件路径。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键（已规范化、已拼前缀）。

        Returns:
            旁车文件绝对路径（``<对象路径>.meta.json``）。
        """
        return self._resolve(bucket, key + self.META_SUFFIX)

    def _write_sidecar(self, bucket: str, key: str, content_type: str | None,
                       metadata: dict[str, str] | None) -> None:
        """
        写入或清除物理键的元数据旁车文件。

        有任一元数据时落 JSON 旁车；两者皆缺时清除旧旁车（覆盖写未带元数据 =
        元数据一并覆盖清除），保证旁车与对象内容一致。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键。
            content_type: MIME 类型；None 表示无。
            metadata: 用户元数据；None 或空 dict 表示无。
        """
        sidecar = self._sidecar_path(bucket, key)
        if not content_type and not metadata:
            if sidecar.is_file():
                sidecar.unlink()
            return
        payload = json.dumps(
            {'content_type': content_type, 'metadata': metadata or {}},
            ensure_ascii=False,
        )
        fileutil.make_parent_dirs(sidecar)
        sidecar.write_text(payload, encoding='utf-8')

    def _read_sidecar(self, bucket: str,
                      key: str) -> tuple[str | None, dict[str, str] | None]:
        """
        读取物理键的元数据旁车文件。

        Args:
            bucket: 桶名（非空，由基类解析后传入）。
            key: 物理键。

        Returns:
            ``(content_type, metadata)`` 二元组；无旁车或内容损坏时为 ``(None, None)``
            （损坏仅告警不抛错：元数据丢失不应阻断对象本身的读取。损坏包括
            非法 JSON 与结构不符——顶层不是 JSON 对象、``content_type`` 非字符串、
            ``metadata`` 不是 JSON 对象）。
        """
        sidecar = self._sidecar_path(bucket, key)
        if not sidecar.is_file():
            return None, None
        try:
            data: Any = json.loads(sidecar.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('旁车内容不是 JSON 对象')
            content = cast('dict[str, Any]', data)
            # or None 空值归一：content_type 空串、metadata 空 dict 均视为无
            raw_content_type = content.get('content_type') or None
            raw_metadata = content.get('metadata') or None
            # 结构校验：类型不符视同损坏（上方拦顶层形态，此处拦字段类型）
            if raw_content_type is not None and not isinstance(raw_content_type, str):
                raise ValueError('旁车 content_type 不是字符串')
            if raw_metadata is not None and not isinstance(raw_metadata, dict):
                raise ValueError('旁车 metadata 不是 JSON 对象')
            content_type: str | None = raw_content_type
            metadata: dict[str, str] | None = (
                cast('dict[str, str]', raw_metadata) if raw_metadata else None
            )
        except (OSError, ValueError):
            log.warning("元数据旁车文件损坏，忽略: %s", sidecar)
            return None, None
        return content_type, metadata

    # endregion

    # region ======== 底层钩子实现（首参桶名 + 物理键语义，与基类一一对应） ========

    def _put(self, bucket: str, key: str, data: BinaryIO, content_type: str | None = None,
             metadata: dict[str, str] | None = None) -> None:
        target = self._resolve(bucket, key)
        fileutil.make_parent_dirs(target)
        with target.open('wb') as dst:
            shutil.copyfileobj(data, dst, 1024 * 1024)
        self._write_sidecar(bucket, key, content_type, metadata)

    def _get(self, bucket: str, key: str) -> BinaryIO:
        target = self._resolve(bucket, key)
        if not target.is_file():
            raise FileNotFoundError(f"对象不存在: {key}")
        return target.open('rb')

    def _delete(self, bucket: str, key: str) -> None:
        target = self._resolve(bucket, key)
        if target.is_file():
            target.unlink()
        # 旁车随之清理（幂等：对象与旁车任一不存在均静默）
        sidecar = self._sidecar_path(bucket, key)
        if sidecar.is_file():
            sidecar.unlink()

    def _head(self, bucket: str, key: str) -> ObjectStat | None:
        target = self._resolve(bucket, key)
        if not target.is_file():
            return None
        file_stat = target.stat()
        content_type, metadata = self._read_sidecar(bucket, key)
        return ObjectStat(
            key=key,
            size=file_stat.st_size,
            last_modified=datetime.fromtimestamp(file_stat.st_mtime),
            etag=None,
            content_type=content_type,
            metadata=metadata,
        )

    def _list(self, bucket: str, prefix: str, delimiter: str | None) -> list[ObjectStat]:
        # 目录层列举是本地文件系统没有的存储层概念：delimiter 仅记日志即忽略
        #（提示放在本实现而非基类，避免支持 delimiter 的云端实现被误报）
        if delimiter:
            log.debug("_list 带 delimiter=%r：本地实现不支持目录层列举，按前缀扁平列举", delimiter)
        base = self._bucket_dir(bucket)
        if not base.is_dir():
            return []
        stats: list[ObjectStat] = []
        for dirpath, _dirnames, filenames in os.walk(base):
            for name in filenames:
                if name.endswith(self.META_SUFFIX):
                    continue  # 旁车不作为对象出现（列举过滤，也不读其内容）
                full = Path(dirpath) / name
                rel = full.relative_to(base).as_posix()
                if not rel.startswith(prefix):
                    continue
                file_stat = full.stat()
                stats.append(ObjectStat(
                    key=rel,
                    size=file_stat.st_size,
                    last_modified=datetime.fromtimestamp(file_stat.st_mtime),
                    etag=None,
                ))
        return stats

    def _copy(self, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str) -> None:
        src = self._resolve(src_bucket, src_key)
        if not src.is_file():
            raise FileNotFoundError(f"源对象不存在: {src_key}")
        dst = self._resolve(dst_bucket, dst_key)
        fileutil.make_parent_dirs(dst)
        shutil.copyfile(src, dst)
        # 旁车随之搬运：源有则拷、源无则清（复制为覆盖语义，不留目标的旧元数据）
        content_type, metadata = self._read_sidecar(src_bucket, src_key)
        self._write_sidecar(dst_bucket, dst_key, content_type, metadata)

    # endregion

    # region ======== 可选钩子覆盖（对应基类"可选钩子"区域） ========

    def _presigned_url(self, bucket: str, key: str, expires: int) -> str:
        # 本地目录无签名/过期语义：返回 file:// URI（可被本地程序直接打开），
        # expires 仅接受以保持基类签名一致，实际不生效。
        del expires
        return self._resolve(bucket, key).resolve().as_uri()

    # endregion

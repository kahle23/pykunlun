"""
pykunlun.oss 对象存储能力的单元测试。

覆盖：
  - 键规范化：分隔符统一、空段/``.`` 段剔除、``..`` 与空键拒绝
  - :class:`LocalOssClient` 全流程：put/get/text/upload/download、
    exists/stat/list（含前缀过滤）、copy/move、delete 幂等、覆盖写
  - 前缀（cfg.prefix）隔离：写入带前缀、列举剥前缀，调用方视角一致
  - 桶（bucket）入参语义：调用传桶优先、缺省回退 cfg.bucket 默认桶、
    两者皆缺报错（ValueError）、桶间隔离
  - 只读配置：各写操作拒绝（PermissionError），读操作不受限
  - 流式钩子契约：``_get`` 出流由调用方关闭、缺失键同步抛、``_put`` 不关入流、
    大文件分块转写
  - 目录安全：``..`` 逃逸拒绝、bucket 子目录映射、file:// URL
  - :class:`OssManager`：类注册/工厂化创建/别名隔离/未注册报错
"""

import io
import os
from collections.abc import Callable, Iterator
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from pykunlun.oss import (
    LocalOssClient,
    ObjectStat,
    OssCfg,
    OssManager,
)

# region ======== fixture ========

@pytest.fixture
def root_dir(tmp_path: Path) -> Iterator[str]:
    """提供一个临时存储根目录路径，测试结束自动清理。"""
    path = str(tmp_path / 'oss-root')
    os.makedirs(path, exist_ok=True)
    yield path


@pytest.fixture
def client(root_dir: str) -> LocalOssClient:
    """默认客户端：默认桶 oss、无前缀。"""
    return LocalOssClient(OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))

# endregion


# region ======== 键处理（规范化经 pathutil，规则详见 tests/util/test_pathutil.py） ========

class TestKeyHandling:
    """测试键经 pathutil 规范化后在本门面的端到端行为。"""

    def test_backslash_key_equivalent(self, client: LocalOssClient) -> None:
        """Windows 风格反斜杠键与正斜杠键等价（写读互通）。"""
        client.put_object('x\\y.txt', 'ok')
        assert client.exists('x/y.txt')

    def test_empty_key_rejected(self, client: LocalOssClient) -> None:
        """空键与空白键拒绝（经 pathutil.normalize_path）。"""
        for bad in ('', '   '):
            with pytest.raises(ValueError):
                client.put_object(bad, 'x')

    def test_parent_key_resolves_lexically(self, client: LocalOssClient) -> None:
        """.. 按标准语义词汇消解：dir/../b.txt 与 b.txt 是同一个键。"""
        client.put_object('dir/../b.txt', 'v')
        assert client.exists('b.txt')
        assert client.exists('dir/../b.txt')  # 入口即消解，两键恒等
        assert client.get_object_text('b.txt') == 'v'

    def test_escaping_key_rejected_at_fs_boundary(self, client: LocalOssClient,
                                                   root_dir: str) -> None:
        """消解后仍外指的键（超根 ..）被本地实现的 realpath 包含性校验拒绝。"""
        with pytest.raises(ValueError, match='逃逸'):
            client.put_object('../../etc/evil.txt', b'x')
        # 根目录之外确无落盘
        assert not os.path.exists(os.path.join(os.path.dirname(root_dir), 'etc'))

    def test_absolute_style_key_resolves_under_root(self, client: LocalOssClient) -> None:
        """以 / 开头的键在本地实现下仍解析到根目录之内（normpath 保留绝对形态，
        由实现的空段过滤兜底），并与对应相对键指向同一对象。"""
        client.put_object('/sub/k.txt', 'v')
        assert client.get_object_text('sub/k.txt') == 'v'
        # 绝对路径下 .. 到根即止（POSIX: /.. == /），不会逃逸，落在根内
        client.put_object('/../evil.txt', b'x')
        assert client.get_object_text('evil.txt') == 'x'

# endregion


# region ======== 键预处理钩子 ========

class TestKeyPrepareHook:
    """测试 _prepare_key 键预处理钩子（模板方法：默认实现 + 可覆写）。"""

    def test_default_is_normpath(self, client: LocalOssClient) -> None:
        """默认实现即 pathutil.normpath 的 OSS 键规范化。"""
        assert client._prepare_key('a\\b/./c') == 'a/b/c'
        assert client._prepare_key('a/b/../c') == 'a/c'

    def test_subclass_override_applies_to_all_ops(self, root_dir: str) -> None:
        """覆写钩子后所有操作统一走新键策略（读写删列举同一恒等）。"""

        class LowerKeyClient(LocalOssClient):
            """演示覆写：键统一转小写。"""

            def _prepare_key(self, key: str) -> str:
                return super()._prepare_key(key).lower()

        client = LowerKeyClient(OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))
        client.put_object('Dir/File.TXT', 'v')
        # 写入键经覆写策略落到小写键；读/存在性/列举全部一致
        assert client.get_object_text('dir/file.txt') == 'v'
        assert client.exists('DIR/FILE.txt')
        assert client.list_keys() == ['dir/file.txt']
        client.delete_object('Dir/File.TXT')
        assert client.list_keys() == []

# endregion


# region ======== LocalOssClient 基础读写 ========

class TestPutAndGet:
    """测试 put/get 基础读写。"""

    def test_put_and_get_bytes(self, client: LocalOssClient, root_dir: str) -> None:
        client.put_object('a/b.bin', b'\x00\x01\xff')
        assert client.get_object('a/b.bin') == b'\x00\x01\xff'
        # 落盘位置符合键映射
        assert (os.path.join(root_dir, 'oss', 'a', 'b.bin'))

    def test_put_text_encoded_utf8(self, client: LocalOssClient) -> None:
        """str 内容按 UTF-8 编码写入，get_object_text 可还原。"""
        client.put_object('t.txt', '你好，世界')
        assert client.get_object('t.txt') == '你好，世界'.encode()
        assert client.get_object_text('t.txt') == '你好，世界'

    def test_get_text_custom_encoding(self, client: LocalOssClient) -> None:
        client.put_object('gbk.txt', '中文'.encode('gbk'))
        assert client.get_object_text('gbk.txt', encoding='gbk') == '中文'

    def test_put_overwrite(self, client: LocalOssClient) -> None:
        """同名键覆盖写。"""
        client.put_object('k.txt', 'v1')
        client.put_object('k.txt', 'v2-longer')
        assert client.get_object_text('k.txt') == 'v2-longer'

    def test_get_missing_raises_file_not_found(self, client: LocalOssClient) -> None:
        """跨实现统一约定：键不存在抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            client.get_object('no/such/key')

    def test_windows_style_key(self, client: LocalOssClient) -> None:
        """反斜杠键与正斜杠键等价。"""
        client.put_object('x\\y.txt', 'ok')
        assert client.exists('x/y.txt')

# endregion


# region ======== 文件级操作 ========

class TestFileOps:
    """测试 upload/download 文件级操作。"""

    def test_upload_and_download_roundtrip(self, client: LocalOssClient, tmp_path: Path) -> None:
        src = str(tmp_path / 'src.bin')
        dst = str(tmp_path / 'nested' / 'dst.bin')
        with open(src, 'wb') as f:
            f.write(b'\x01\x02\x03')
        client.upload_file('files/src.bin', src)
        client.download_file('files/src.bin', dst)  # 目标父目录应自动创建
        with open(dst, 'rb') as f:
            assert f.read() == b'\x01\x02\x03'

    def test_download_missing_raises(self, client: LocalOssClient, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            client.download_file('nope', str(tmp_path / 'out.bin'))

# endregion


# region ======== 流式底层钩子契约 ========

class _CloseTrackingStream(io.RawIOBase):
    """最小包装流：透传读取，close 时回调记录（用于验证调用方关闭纪律）。"""

    def __init__(self, inner: BinaryIO, on_close: Callable[[], None]) -> None:
        super().__init__()
        self._inner = inner
        self._on_close = on_close

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        return self._inner.read(size)

    def close(self) -> None:
        if not self.closed:
            self._on_close()
        super().close()


class TestStreamHooks:
    """测试 _put/_get 流契约：谁开谁关、缺失键同步抛、分块转写。"""

    def test_get_hook_returns_stream_from_start(self, client: LocalOssClient) -> None:
        """_get 返回已打开、定位在起始位置的二进制流（连续读取不重置）。"""
        client.put_object('s/a.txt', 'hello')
        stream = client._get('oss', 's/a.txt')
        try:
            assert stream.read(2) == b'he'
            assert stream.read() == b'llo'
        finally:
            stream.close()

    def test_get_hook_missing_key_raises_eagerly(self, client: LocalOssClient) -> None:
        """键不存在时 _get 调用即抛 FileNotFoundError，而非首次读取时。"""
        with pytest.raises(FileNotFoundError):
            client._get('oss', 's/missing.txt')

    def test_put_hook_keeps_caller_stream_open(self, client: LocalOssClient) -> None:
        """_put 只消费入流不关流：写入后流仍可读，关闭归调用方。"""
        payload = BytesIO(b'data')
        client._put('oss', 's/b.txt', payload)
        assert client.get_object_text('s/b.txt') == 'data'
        assert payload.read() == b''  # 流已被读到末尾但未关闭（关闭则此处抛 ValueError）

    def test_get_object_closes_underlying_stream(self, root_dir: str) -> None:
        """get_object 读完即关闭底层流（基类经 closing 履行调用方关闭义务）。"""

        class TrackingClient(LocalOssClient):
            """_get 出流包一层关闭记录。"""

            def __init__(self, cfg: OssCfg) -> None:
                super().__init__(cfg)
                self.closed_keys: list[str] = []

            def _get(self, bucket: str, key: str) -> BinaryIO:
                # 包装流仅实现读侧（RawIOBase 结构上不含 BinaryIO 的写侧成员），故 cast
                return cast(BinaryIO, _CloseTrackingStream(
                    super()._get(bucket, key),
                    lambda: self.closed_keys.append(key),
                ))

        client = TrackingClient(OssCfg(oss_type='local', bucket='oss',
                                       storage_options={'base_dir': root_dir}))
        client.put_object('s/c.txt', 'v')
        assert client.get_object_text('s/c.txt') == 'v'
        assert client.closed_keys == ['s/c.txt']

    def test_download_streams_large_object(self, client: LocalOssClient,
                                           tmp_path: Path) -> None:
        """大于单转写块（1 MiB）的对象分块下载，内容完整。"""
        payload = os.urandom(2 * 1024 * 1024 + 7)
        client.put_object('big.bin', payload)
        dst = str(tmp_path / 'big.bin')
        client.download_file('big.bin', dst)
        with open(dst, 'rb') as f:
            assert f.read() == payload

    def test_download_missing_creates_no_dir(self, client: LocalOssClient,
                                             tmp_path: Path) -> None:
        """键不存在时先开流即抛，目标父目录不被创建（时序保证）。"""
        dst = str(tmp_path / 'nested' / 'out.bin')
        with pytest.raises(FileNotFoundError):
            client.download_file('nope', dst)
        assert not os.path.exists(os.path.dirname(dst))

# endregion


# region ======== 探测与列举 ========

class TestListAndStat:
    """测试 exists/stat/list_objects/list_keys。"""

    def _seed(self, client: LocalOssClient) -> None:
        client.put_object('docs/a.txt', 'a')
        client.put_object('docs/sub/b.txt', 'b')
        client.put_object('imgs/c.png', b'c')

    def test_exists(self, client: LocalOssClient) -> None:
        self._seed(client)
        assert client.exists('docs/a.txt')
        assert not client.exists('docs/missing.txt')

    def test_stat_fields(self, client: LocalOssClient) -> None:
        self._seed(client)
        st = client.stat('docs/a.txt')
        assert st is not None
        assert st.key == 'docs/a.txt'
        assert st.size == 1
        assert st.last_modified is not None
        assert st.extra == {}  # 本地实现无实现特有字段
        assert client.stat('missing') is None

    def test_list_prefix_filter(self, client: LocalOssClient) -> None:
        self._seed(client)
        keys = client.list_keys('docs/')
        assert sorted(keys) == ['docs/a.txt', 'docs/sub/b.txt']
        assert client.list_keys() == sorted(client.list_keys(''))
        # 全量列举 = 三个对象（顺序由实现决定）
        assert set(client.list_keys()) == {'docs/a.txt', 'docs/sub/b.txt', 'imgs/c.png'}

    def test_list_returns_object_stat(self, client: LocalOssClient) -> None:
        client.put_object('a.txt', 'hello')
        stats = client.list_objects()
        assert len(stats) == 1
        assert isinstance(stats[0], ObjectStat)
        assert stats[0].size == 5
        assert stats[0].extra == {}

    def test_list_empty_root(self, client: LocalOssClient) -> None:
        assert client.list_keys() == []

# endregion


# region ======== 复制/移动/删除 ========

class TestCopyMoveDelete:
    """测试 copy/move/delete。"""

    def test_copy_object(self, client: LocalOssClient) -> None:
        client.put_object('src.txt', 'data')
        client.copy_object('src.txt', 'dst/deep.txt')
        assert client.get_object_text('dst/deep.txt') == 'data'
        assert client.exists('src.txt')  # 源保留

    def test_copy_missing_source_raises(self, client: LocalOssClient) -> None:
        with pytest.raises(FileNotFoundError):
            client.copy_object('nope', 'dst')

    def test_move_object(self, client: LocalOssClient) -> None:
        client.put_object('old/a.txt', 'mv')
        client.move_object('old/a.txt', 'new/b.txt')
        assert not client.exists('old/a.txt')
        assert client.get_object_text('new/b.txt') == 'mv'

    def test_delete_idempotent(self, client: LocalOssClient) -> None:
        client.put_object('gone.txt', 'x')
        client.delete_object('gone.txt')
        assert not client.exists('gone.txt')
        client.delete_object('gone.txt')  # 再删一次不抛异常

# endregion


# region ======== 前缀隔离 ========

class TestPrefix:
    """测试 cfg.prefix 全局前缀对调用方透明。"""

    def test_prefix_transparent(self, root_dir: str) -> None:
        client = LocalOssClient(OssCfg(oss_type='local', bucket='oss',
                                       storage_options={'base_dir': root_dir}, prefix='tenant-a'))
        client.put_object('a.txt', 'pfx')  # 实际落盘 tenant-a/a.txt
        assert client.get_object_text('a.txt') == 'pfx'
        assert client.list_keys() == ['a.txt']  # 列举剥前缀
        # 磁盘上确实带前缀
        assert os.path.exists(os.path.join(root_dir, 'oss', 'tenant-a', 'a.txt'))

    def test_prefix_trailing_slash_normalized(self, root_dir: str) -> None:
        """前缀带不带尾部斜杠、含反斜杠，均归一为 x/ 形式。"""
        c1 = LocalOssClient(OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}, prefix='x/'))
        c2 = LocalOssClient(OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}, prefix='x'))
        c1.put_object('k', '1')
        assert c2.exists('k')

# endregion


# region ======== 只读配置 ========

class TestReadOnly:
    """测试 read_only 配置写拦截。"""

    @pytest.fixture
    def ro_client(self, root_dir: str) -> LocalOssClient:
        c = LocalOssClient(OssCfg(oss_type='local', bucket='oss',
                                  storage_options={'base_dir': root_dir}, read_only=True))
        # 预置一个对象供读（直接写文件，绕过客户端写拦截）
        pre_path = os.path.join(root_dir, 'oss', 'pre.txt')
        os.makedirs(os.path.dirname(pre_path), exist_ok=True)
        with open(pre_path, 'wb') as f:
            f.write(b'read me')
        return c

    @pytest.mark.parametrize('op', [
        'put_object', 'upload_file', 'delete_object', 'copy_object', 'move_object',
    ])
    def test_write_ops_rejected(self, ro_client: LocalOssClient, op: str, tmp_path: Path) -> None:
        method = getattr(ro_client, op)
        args: tuple[object, ...]
        if op == 'put_object':
            args = ('k', b'v')
        elif op in ('upload_file', 'download_file'):
            args = ('k', str(tmp_path / 'f.bin'))
        elif op == 'delete_object':
            args = ('pre.txt',)
        else:  # copy/move
            args = ('pre.txt', 'dst.txt')
        with pytest.raises(PermissionError):
            method(*args)

    def test_read_ops_allowed(self, ro_client: LocalOssClient) -> None:
        """只读配置不影响读操作。"""
        assert ro_client.get_object_text('pre.txt') == 'read me'
        assert ro_client.list_keys() == ['pre.txt']
        assert ro_client.exists('pre.txt')

# endregion


# region ======== bucket 映射与目录安全 ========

class TestBucketAndSafety:
    """测试 bucket 子目录映射与目录穿越防护。"""

    def test_bucket_maps_to_subdir(self, root_dir: str) -> None:
        c1 = LocalOssClient(OssCfg(oss_type='local', storage_options={'base_dir': root_dir}, bucket='b1'))
        c2 = LocalOssClient(OssCfg(oss_type='local', storage_options={'base_dir': root_dir}, bucket='b2'))
        c1.put_object('k.txt', 'one')
        c2.put_object('k.txt', 'two')
        # 同键不同桶互不可见
        assert c1.get_object_text('k.txt') == 'one'
        assert c2.get_object_text('k.txt') == 'two'
        assert os.path.exists(os.path.join(root_dir, 'b1', 'k.txt'))

    def test_escape_via_symlink_rejected(self, root_dir: str, tmp_path: Path) -> None:
        """键经符号链接指向根目录之外时拒绝（realpath 复核兜底）。"""
        outside_dir = str(tmp_path / 'outside')
        os.makedirs(outside_dir, exist_ok=True)
        link_path = os.path.join(root_dir, 'oss', 'link')
        try:
            os.symlink(outside_dir, link_path, target_is_directory=True)
        except OSError:
            # Windows 无符号链接特权（WinError 1314）时跳过；类 Unix 环境始终执行
            pytest.skip("当前环境无创建符号链接的特权")
        client = LocalOssClient(OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))
        with pytest.raises(ValueError, match='逃逸'):
            client.put_object('link/evil.txt', b'x')

    def test_presigned_url_is_file_uri(self, client: LocalOssClient) -> None:
        client.put_object('u.txt', 'url')
        url = client.presigned_url('u.txt')
        assert url.startswith('file://')

    def test_missing_base_dir_rejected(self) -> None:
        with pytest.raises(ValueError, match='base_dir'):
            LocalOssClient(OssCfg(oss_type='local'))

    def test_oss_type_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match='oss_type'):
            LocalOssClient(OssCfg(oss_type='aliyun', storage_options={'base_dir': '/tmp/x'}))

# endregion


# region ======== 桶入参语义（调用传桶 > cfg 默认桶 > 报错） ========

class TestBucketArg:
    """测试 bucket 入参的三级解析：入参优先、cfg 默认桶回退、皆缺报错。"""

    def test_default_bucket_used_when_no_arg(self, client: LocalOssClient,
                                             root_dir: str) -> None:
        """调用不传 bucket 时走 cfg.bucket 默认桶，落盘到根目录下的桶子目录。"""
        client.put_object('k.txt', 'v')
        assert client.get_object_text('k.txt') == 'v'
        assert os.path.exists(os.path.join(root_dir, 'oss', 'k.txt'))

    def test_call_bucket_overrides_default(self, client: LocalOssClient,
                                           root_dir: str) -> None:
        """调用显式传 bucket 时优先于 cfg 默认桶，两桶互不可见。"""
        client.put_object('k.txt', 'default-bucket')
        client.put_object('k.txt', 'call-bucket', bucket='tmp')

        assert client.get_object_text('k.txt') == 'default-bucket'
        assert client.get_object_text('k.txt', bucket='tmp') == 'call-bucket'
        # 落盘位置各自符合桶映射
        assert os.path.exists(os.path.join(root_dir, 'oss', 'k.txt'))
        assert os.path.exists(os.path.join(root_dir, 'tmp', 'k.txt'))
        # 默认桶列举看不到显式桶的对象
        assert client.list_keys(bucket='tmp') == ['k.txt']
        assert client.list_keys() == ['k.txt']

    def test_all_ops_accept_bucket(self, client: LocalOssClient,
                                   tmp_path: Path) -> None:
        """读写删列举拷贝等全部公共 API 均接受 bucket 入参且桶内寻址一致。"""
        src = str(tmp_path / 'up.bin')
        dst = str(tmp_path / 'down.bin')
        with open(src, 'wb') as f:
            f.write(b'bin')

        client.upload_file('ops/up.bin', src, bucket='tmp')
        assert client.exists('ops/up.bin', bucket='tmp')
        assert client.stat('ops/up.bin', bucket='tmp') is not None
        assert client.stat('ops/up.bin') is None  # 默认桶无此对象
        client.copy_object('ops/up.bin', 'ops/copy.bin', bucket='tmp')
        client.move_object('ops/copy.bin', 'ops/moved.bin', bucket='tmp')
        assert sorted(client.list_keys('ops/', bucket='tmp')) == ['ops/moved.bin', 'ops/up.bin']
        client.download_file('ops/moved.bin', dst, bucket='tmp')
        with open(dst, 'rb') as f:
            assert f.read() == b'bin'
        assert client.presigned_url('ops/up.bin', bucket='tmp').startswith('file://')
        client.delete_object('ops/up.bin', bucket='tmp')
        assert not client.exists('ops/up.bin', bucket='tmp')

    def test_cross_bucket_copy_and_move(self, client: LocalOssClient,
                                        root_dir: str) -> None:
        """copy/move 支持跨桶：dst_bucket 省略时同桶，显式传则源/目标独立寻址。"""
        client.put_object('x/src.txt', 'data', bucket='b-from')

        # 跨桶复制：源保留、目标落在新桶、旁车元数据随行
        client.put_object('x/meta.txt', 'm', content_type='text/plain', bucket='b-from')
        client.copy_object('x/src.txt', 'y/dst.txt', bucket='b-from', dst_bucket='b-to')
        client.copy_object('x/meta.txt', 'y/meta.txt', bucket='b-from', dst_bucket='b-to')
        assert client.exists('x/src.txt', bucket='b-from')  # 源保留
        assert client.get_object_text('y/dst.txt', bucket='b-to') == 'data'
        st = client.stat('y/meta.txt', bucket='b-to')
        assert st is not None and st.content_type == 'text/plain'
        assert os.path.exists(os.path.join(root_dir, 'b-to', 'y', 'dst.txt'))
        # 覆盖式语义：目标桶旧同键对象被覆盖
        client.put_object('y/dst.txt', 'old', bucket='b-to')
        client.copy_object('x/src.txt', 'y/dst.txt', bucket='b-from', dst_bucket='b-to')
        assert client.get_object_text('y/dst.txt', bucket='b-to') == 'data'

        # 跨桶移动：源删除、目标保留
        client.move_object('x/src.txt', 'z/moved.txt', bucket='b-from', dst_bucket='b-to')
        assert not client.exists('x/src.txt', bucket='b-from')
        assert client.get_object_text('z/moved.txt', bucket='b-to') == 'data'

    def test_dst_bucket_defaults_to_src_bucket(self, client: LocalOssClient) -> None:
        """省略 dst_bucket 时与源同桶（含空白串视同未传）。"""
        client.put_object('s.txt', 'v', bucket='b1')
        client.copy_object('s.txt', 'c.txt', bucket='b1', dst_bucket=None)
        client.move_object('c.txt', 'm.txt', bucket='b1', dst_bucket='  ')
        assert client.get_object_text('m.txt', bucket='b1') == 'v'
        assert not client.exists('c.txt', bucket='b1')
        assert sorted(client.list_keys(bucket='b1')) == ['m.txt', 's.txt']

    def test_missing_bucket_rejected(self, root_dir: str) -> None:
        """入参与 cfg 默认桶皆缺时，读写均抛 ValueError。"""
        client = LocalOssClient(OssCfg(oss_type='local', storage_options={'base_dir': root_dir}))
        with pytest.raises(ValueError, match='bucket'):
            client.put_object('k.txt', 'v')
        with pytest.raises(ValueError, match='bucket'):
            client.get_object('k.txt')
        with pytest.raises(ValueError, match='bucket'):
            client.list_keys()

    def test_blank_bucket_treated_as_missing(self, root_dir: str) -> None:
        """空白桶名视同未传：入参空白回退 cfg 默认桶，cfg 也空白则报错。"""
        client = LocalOssClient(OssCfg(oss_type='local', bucket='oss',
                                       storage_options={'base_dir': root_dir}))
        client.put_object('k.txt', 'v', bucket='   ')
        assert client.get_object_text('k.txt') == 'v'  # 回退默认桶读到同一对象

        blank = LocalOssClient(OssCfg(oss_type='local', storage_options={'base_dir': root_dir}))
        with pytest.raises(ValueError, match='bucket'):
            blank.put_object('k.txt', 'v', bucket='  ')

# endregion


# region ======== 元数据（旁车 .meta.json） ========

class TestMetadata:
    """测试 content_type / metadata 的旁车承载与读回。"""

    def test_put_with_meta_and_stat_readback(self, client: LocalOssClient, root_dir: str) -> None:
        client.put_object('m/a.txt', 'v', content_type='text/plain',
                          metadata={'author': 'kahle', 'app': 'demo'})
        st = client.stat('m/a.txt')
        assert st is not None
        assert st.content_type == 'text/plain'
        assert st.metadata == {'author': 'kahle', 'app': 'demo'}
        # 旁车落在对象旁
        assert os.path.exists(os.path.join(root_dir, 'oss', 'm', 'a.txt.meta.json'))

    def test_put_without_meta_stat_none(self, client: LocalOssClient) -> None:
        client.put_object('m/plain.txt', 'v')
        st = client.stat('m/plain.txt')
        assert st is not None
        assert st.content_type is None
        assert st.metadata is None

    def test_overwrite_clears_stale_meta(self, client: LocalOssClient) -> None:
        """覆盖写未带元数据时，旧元数据一并清除（覆盖式语义）。"""
        client.put_object('m/k', 'v1', content_type='text/plain', metadata={'a': '1'})
        client.put_object('m/k', 'v2')
        st = client.stat('m/k')
        assert st is not None
        assert st.content_type is None
        assert st.metadata is None

    def test_overwrite_replaces_meta(self, client: LocalOssClient) -> None:
        """覆盖写带新元数据时整组替换。"""
        client.put_object('m/k', 'v1', content_type='text/plain', metadata={'a': '1'})
        client.put_object('m/k', 'v2', content_type='application/json', metadata={'b': '2'})
        st = client.stat('m/k')
        assert st is not None
        assert st.content_type == 'application/json'
        assert st.metadata == {'b': '2'}

    def test_content_type_only(self, client: LocalOssClient) -> None:
        client.put_object('m/only.bin', b'\x00', content_type='application/octet-stream')
        st = client.stat('m/only.bin')
        assert st is not None
        assert st.content_type == 'application/octet-stream'
        assert st.metadata is None

    def test_delete_removes_sidecar(self, client: LocalOssClient, root_dir: str) -> None:
        client.put_object('m/gone.txt', 'v', content_type='text/plain')
        sidecar = os.path.join(root_dir, 'oss', 'm', 'gone.txt.meta.json')
        assert os.path.exists(sidecar)
        client.delete_object('m/gone.txt')
        assert not os.path.exists(sidecar)

    def test_copy_move_carry_metadata(self, client: LocalOssClient) -> None:
        client.put_object('m/src.txt', 'v', content_type='text/plain',
                          metadata={'author': 'kahle'})
        client.copy_object('m/src.txt', 'm/copy.txt')
        client.move_object('m/src.txt', 'm/moved.txt')
        for key in ('m/copy.txt', 'm/moved.txt'):
            st = client.stat(key)
            assert st is not None
            assert st.content_type == 'text/plain'
            assert st.metadata == {'author': 'kahle'}

    def test_copy_clears_dst_stale_meta(self, client: LocalOssClient) -> None:
        """复制为覆盖语义：目标旧元数据不残留（源无元数据时目标清空）。"""
        client.put_object('m/dst.txt', 'old', content_type='text/plain')
        client.put_object('m/src.txt', 'new')
        client.copy_object('m/src.txt', 'm/dst.txt')
        st = client.stat('m/dst.txt')
        assert st is not None
        assert st.content_type is None
        assert st.metadata is None

    def test_list_hides_sidecars(self, client: LocalOssClient) -> None:
        """旁车按保留键过滤，不出现在列举中；列举不读旁车内容。"""
        client.put_object('m/a.txt', 'v', content_type='text/plain', metadata={'a': '1'})
        client.put_object('m/sub/b.txt', 'w')
        assert client.list_keys() == ['m/a.txt', 'm/sub/b.txt']
        assert all(st.metadata is None and st.content_type is None
                   for st in client.list_objects())

    def test_upload_file_guesses_content_type(self, client: LocalOssClient,
                                               tmp_path: Path) -> None:
        """upload_file 未指定 content_type 时按扩展名经 mimetypes 推导。"""
        src = str(tmp_path / 'note.txt')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('hello')
        client.upload_file('u/note.txt', src)
        st = client.stat('u/note.txt')
        assert st is not None
        assert st.content_type == 'text/plain'

    def test_upload_file_unknown_ext_guesses_none(self, client: LocalOssClient,
                                                   tmp_path: Path) -> None:
        src = str(tmp_path / 'file.noext')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('hello')
        client.upload_file('u/file.noext', src)
        st = client.stat('u/file.noext')
        assert st is not None
        assert st.content_type is None

    def test_upload_file_explicit_overrides_guess(self, client: LocalOssClient,
                                                   tmp_path: Path) -> None:
        src = str(tmp_path / 'data.txt')
        with open(src, 'w', encoding='utf-8') as f:
            f.write('hello')
        client.upload_file('u/data.txt', src, content_type='application/custom',
                           metadata={'tag': 'x'})
        st = client.stat('u/data.txt')
        assert st is not None
        assert st.content_type == 'application/custom'
        assert st.metadata == {'tag': 'x'}

    def test_prefix_client_sidecar_scoped(self, root_dir: str) -> None:
        """带 prefix 的客户端旁车同样落在前缀目录内，stat 正常读回。"""
        client = LocalOssClient(OssCfg(oss_type='local', bucket='oss',
                                       storage_options={'base_dir': root_dir}, prefix='tenant-a'))
        client.put_object('k.txt', 'v', metadata={'a': '1'})
        st = client.stat('k.txt')
        assert st is not None
        assert st.metadata == {'a': '1'}
        assert client.list_keys() == ['k.txt']

    def test_corrupted_sidecar_degrades_gracefully(self, client: LocalOssClient,
                                                   root_dir: str) -> None:
        """旁车损坏不阻断对象读取：stat 返回 None 元数据，get 正常。

        损坏含两种形态：非法 JSON、合法 JSON 但结构不符（顶层/metadata 非对象）。
        """
        client.put_object('m/bad.txt', 'v', content_type='text/plain')
        sidecar = os.path.join(root_dir, 'oss', 'm', 'bad.txt.meta.json')
        for broken in ('{not-json', '[]', '"plain"', '{"metadata": 123}'):
            with open(sidecar, 'w', encoding='utf-8') as f:
                f.write(broken)
            st = client.stat('m/bad.txt')
            assert st is not None
            assert st.content_type is None
            assert st.metadata is None
            assert client.get_object_text('m/bad.txt') == 'v'

# endregion


# region ======== OssManager ========

class TestOssManager:
    """测试管理器注册表与便捷转发。"""

    def test_register_class_and_cfg_factory(self, root_dir: str) -> None:
        mgr = OssManager()
        mgr.register_client_class(LocalOssClient)
        mgr.register('dev', OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))
        assert isinstance(mgr.get_client('dev'), LocalOssClient)
        assert mgr.get_registered_client_types() == ['local']

    def test_register_instance_directly(self, client: LocalOssClient) -> None:
        mgr = OssManager()
        mgr.register('default', client)
        mgr.put_object('via-mgr.txt', 'hello')
        assert mgr.get_object_text('via-mgr.txt') == 'hello'

    def test_named_instances_isolated(self, root_dir: str, client: LocalOssClient) -> None:
        mgr = OssManager()
        mgr.register('default', client)
        other = LocalOssClient(OssCfg(oss_type='local', storage_options={'base_dir': root_dir}, bucket='other'))
        mgr.register('other', other)
        mgr.put_object('k', 'd1')
        mgr.put_object('k', 'd2', name='other')
        assert mgr.get_object_text('k') == 'd1'
        assert mgr.get_object_text('k', name='other') == 'd2'
        assert set(mgr.get_registered_names()) == {'default', 'other'}

    def test_unknown_type_rejected(self) -> None:
        mgr = OssManager()
        mgr.register_client_class(LocalOssClient)
        with pytest.raises(ValueError, match='oss_type'):
            mgr.register('x', OssCfg(oss_type='s3', storage_options={'base_dir': '/tmp'}))

    def test_unknown_name_rejected(self) -> None:
        mgr = OssManager()
        with pytest.raises(ValueError, match='default'):
            mgr.get_client()

    def test_register_non_client_rejected(self) -> None:
        mgr = OssManager()
        with pytest.raises(TypeError):
            mgr.register_client_class(object)  # type: ignore[arg-type]

    def test_full_lifecycle_via_manager(self, root_dir: str) -> None:
        """经管理器走一遍完整生命周期。"""
        mgr = OssManager()
        mgr.register_client_class(LocalOssClient)
        mgr.register('default', OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))
        mgr.put_object('lifecycle/a.txt', 'v1')
        mgr.copy_object('lifecycle/a.txt', 'lifecycle/b.txt')
        mgr.move_object('lifecycle/b.txt', 'lifecycle/c.txt')
        assert mgr.exists('lifecycle/c.txt')
        assert mgr.stat('lifecycle/c.txt') is not None
        assert mgr.list_keys('lifecycle/') == ['lifecycle/a.txt', 'lifecycle/c.txt']
        mgr.delete_object('lifecycle/a.txt')
        assert mgr.list_keys() == ['lifecycle/c.txt']

    def test_bucket_passthrough_via_manager(self, root_dir: str) -> None:
        """管理器便捷方法透传 bucket：缺省走默认桶，显式传走指定桶。"""
        mgr = OssManager()
        mgr.register_client_class(LocalOssClient)
        mgr.register('default', OssCfg(oss_type='local', bucket='oss', storage_options={'base_dir': root_dir}))
        mgr.put_object('k.txt', 'default', bucket='oss')
        mgr.put_object('k.txt', 'explicit', bucket='tmp')
        assert mgr.get_object_text('k.txt') == 'default'
        assert mgr.get_object_text('k.txt', bucket='tmp') == 'explicit'
        assert mgr.list_keys(bucket='tmp') == ['k.txt']

# endregion

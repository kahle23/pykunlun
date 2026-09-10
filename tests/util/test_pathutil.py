"""
pykunlun.util.pathutil 字符串路径工具的单元测试。

覆盖（:func:`normpath` 为 posixpath.normpath 浅层包装，相对与绝对路径都支持）：
  - :func:`normpath`：分隔符统一、空段/``.`` 段剔除、尾部 ``/`` 剥除、
    ``..`` 消解（弹出上一段 / 超根保留 / 空结果归一 ``.`` / 绝对路径到根即止）、
    绝对形态保留、空路径拒绝
  - :func:`join_path`：多段合并、空段跳过、绝对风格段剥 ``/``、段间 ``..`` 消解、
    全空返回空串
  - :func:`sub_path`：前缀剥除、相等返回空串、未命中原样返回、按段命中
  - :func:`sub_path_by_index`：按段区间截取、负索引回数、越界收敛、边界交换
"""

import pytest

from pykunlun.util import pathutil

# region ======== normpath ========

class TestNormpath:
    """测试 normpath 规范化规则（posixpath.normpath 浅层包装）。"""

    def test_plain_path_unchanged(self) -> None:
        assert pathutil.normpath('a/b/c.txt') == 'a/b/c.txt'

    def test_backslash_unified(self) -> None:
        assert pathutil.normpath('a\\b\\c.txt') == 'a/b/c.txt'

    def test_empty_segments_and_dot_removed(self) -> None:
        assert pathutil.normpath('a//b/./c') == 'a/b/c'
        assert pathutil.normpath('./a/b') == 'a/b'

    def test_trailing_slash_stripped(self) -> None:
        """尾部分隔符剥除（posixpath 标准）；是否目录由 append_slash 显式表达。"""
        assert pathutil.normpath('a/b/c/') == 'a/b/c'
        assert pathutil.normpath('a\\b//./c/') == 'a/b/c'

    def test_parent_resolved(self) -> None:
        """.. 消解为上一段（标准规范化语义）。"""
        assert pathutil.normpath('a/b/../c') == 'a/c'
        assert pathutil.normpath('a/b/c/../../d') == 'a/d'

    def test_unresolvable_parent_kept(self) -> None:
        """相对路径的超根 .. 无法消解时保留。"""
        assert pathutil.normpath('../a') == '../a'
        assert pathutil.normpath('a/../../b') == '../b'

    def test_absolute_preserved(self) -> None:
        """绝对路径保留形态：重复分隔符消解、开头 / 不剥除。"""
        assert pathutil.normpath('/a//b') == '/a/b'
        assert pathutil.normpath('//a') == '//a'  # POSIX 惯例：恰好两个开头斜杠保留

    def test_absolute_parent_stops_at_root(self) -> None:
        """绝对路径下 .. 到根即止（POSIX: /.. == /）。"""
        assert pathutil.normpath('/../a') == '/a'
        assert pathutil.normpath('/a/../..') == '/'

    def test_empty_result_is_dot(self) -> None:
        """消解后为空的路径归一为当前目录 '.'。"""
        assert pathutil.normpath('a/..') == '.'
        assert pathutil.normpath('.') == '.'
        assert pathutil.normpath('./') == '.'


    def test_backslash_unified_before_resolve(self) -> None:
        """反斜杠先归一为 / 再消解（POSIX 中 \\ 本是合法文件名字符）。"""
        assert pathutil.normpath('a\\..\\b') == 'b'
        assert pathutil.normpath('a\\b\\..\\c') == 'a/c'

    def test_triple_dot_is_plain_segment(self) -> None:
        """'...' 是普通段，不参与消解。"""
        assert pathutil.normpath('a/.../b') == 'a/.../b'


    def test_empty_path_rejected(self) -> None:
        for bad in ('', '   '):
            with pytest.raises(ValueError):
                pathutil.normpath(bad)

# endregion


# region ======== join_path ========

class TestJoinPath:
    """测试 join_path 路径合并。"""

    def test_join_multiple_parts(self) -> None:
        assert pathutil.join_path('a', 'b', 'c.txt') == 'a/b/c.txt'

    def test_empty_parts_skipped(self) -> None:
        assert pathutil.join_path('a/', '', 'b') == 'a/b'

    def test_none_parts_skipped(self) -> None:
        assert pathutil.join_path(None, 'a', None, 'b') == 'a/b'

    def test_all_empty_returns_empty(self) -> None:
        assert pathutil.join_path() == ''
        assert pathutil.join_path('', None, '  ') == ''

    def test_absolute_style_part_stripped(self) -> None:
        """非首段的绝对风格入参不重置前缀（与 os.path.join 语义不同）。"""
        assert pathutil.join_path('a', '/b') == 'a/b'

    def test_absolute_first_part_preserved(self) -> None:
        """首段为绝对路径时结果保留绝对形态。"""
        assert pathutil.join_path('/a', 'b') == '/a/b'

    def test_parent_resolves_previous_part(self) -> None:
        """合并后整体规范化，段间 .. 消解前段。"""
        assert pathutil.join_path('a/b', '../c') == 'a/c'
        assert pathutil.join_path('a', '..') == '.'

# endregion


# region ======== sub_path ========

class TestSubPath:
    """测试 sub_path 前缀剥除。"""

    def test_strip_prefix(self) -> None:
        assert pathutil.sub_path('a/b/c/d.txt', 'a/b') == 'c/d.txt'

    def test_equal_returns_empty(self) -> None:
        """path 与 prefix 相同时相对自身为空路径。"""
        assert pathutil.sub_path('a/b', 'a/b') == ''
        assert pathutil.sub_path('a', 'a') == ''

    def test_unmatched_returns_original(self) -> None:
        """不构成前缀关系时宽容返回原样。"""
        assert pathutil.sub_path('x/y.txt', 'a/b') == 'x/y.txt'

    def test_empty_prefix_returns_original(self) -> None:
        assert pathutil.sub_path('a/b.txt', '') == 'a/b.txt'
        assert pathutil.sub_path('a/b.txt', None) == 'a/b.txt'  # type: ignore[arg-type]

    def test_prefix_normalized_internally(self) -> None:
        """基准内部组合 normpath + '/' 归一为目录形态。"""
        assert pathutil.sub_path('a/b.txt', 'a') == 'b.txt'
        assert pathutil.sub_path('a/b/c', 'a//b/') == 'c'

    def test_partial_segment_not_stripped(self) -> None:
        """前缀必须按段命中：'ab' 不应命中 'abc/x'。"""
        assert pathutil.sub_path('abc/x.txt', 'ab') == 'abc/x.txt'

    def test_absolute_prefix(self) -> None:
        """绝对基准与绝对目标同域时正常剥除。"""
        assert pathutil.sub_path('/a/b/c.txt', '/a/b') == 'c.txt'

    def test_case_sensitive(self) -> None:
        """大小写敏感（POSIX 键/URL 语义）。"""
        assert pathutil.sub_path('a/b/c', 'A/B') == 'a/b/c'

# endregion

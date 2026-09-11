"""
pykunlun.util.fileutil 文件操作工具的单元测试。

覆盖（:func:`make_parent_dirs` 为本模块目录创建能力的入口）：
  - :func:`make_parent_dirs`：嵌套父目录创建、``str`` / ``Path`` 双类型入参、
    文件与目录路径入参一律建其 ``.parent``、路径本身不创建、
    幂等（父目录已存在静默）、纯文件名不建目录、
    父路径被同名文件占据时报 :class:`FileExistsError`
"""

from pathlib import Path

import pytest

from pykunlun.util import fileutil


# region ======== make_parent_dirs ========
class TestMakeParentDirs:
    """测试 make_parent_dirs 的父目录创建语义（一律建入参的 .parent）。"""

    def test_creates_nested_parent_dirs(self, tmp_path: Path) -> None:
        """str 入参：多级不存在的父目录一次性建全。"""
        file_path = tmp_path / 'a' / 'b' / 'c.txt'
        fileutil.make_parent_dirs(str(file_path))
        assert (tmp_path / 'a' / 'b').is_dir()

    def test_accepts_path_object(self, tmp_path: Path) -> None:
        """pathlib.Path 入参与 str 等价。"""
        file_path = tmp_path / 'x' / 'y.txt'
        fileutil.make_parent_dirs(file_path)
        assert (tmp_path / 'x').is_dir()

    def test_dir_path_input_creates_its_parent(self, tmp_path: Path) -> None:
        """目录路径入参同样建其 parent：传 a/b 建 a，b 本身不创建。"""
        dir_path = tmp_path / 'p' / 'q'
        fileutil.make_parent_dirs(dir_path)
        assert (tmp_path / 'p').is_dir()
        assert not dir_path.exists()

    def test_path_itself_not_created(self, tmp_path: Path) -> None:
        """只建父目录，入参路径本身不创建（由调用方负责写入）。"""
        file_path = tmp_path / 'd' / 'f.txt'
        fileutil.make_parent_dirs(file_path)
        assert (tmp_path / 'd').is_dir()
        assert not file_path.exists()

    def test_idempotent_when_parent_exists(self, tmp_path: Path) -> None:
        """父目录已存在时静默通过，重复调用不报错。"""
        file_path = tmp_path / 'e' / 'f.txt'
        fileutil.make_parent_dirs(file_path)
        fileutil.make_parent_dirs(file_path)
        assert (tmp_path / 'e').is_dir()

    def test_bare_filename_is_noop(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """纯文件名（无目录部分）的父目录为当前目录，不创建任何东西。"""
        monkeypatch.chdir(tmp_path)
        fileutil.make_parent_dirs('f.txt')
        assert list(tmp_path.iterdir()) == []

    def test_parent_occupied_by_file_raises(self, tmp_path: Path) -> None:
        """父路径被同名文件占据时创建失败，抛 FileExistsError（不静默）。"""
        blocker = tmp_path / 'blocker'
        blocker.write_text('occupied', encoding='utf-8')
        with pytest.raises(FileExistsError):
            fileutil.make_parent_dirs(blocker / 'f.txt')
# endregion

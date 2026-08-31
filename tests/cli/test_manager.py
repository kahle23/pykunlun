"""
pykunlun.cli.manager 的单元测试。

覆盖框架级 ``--version`` / ``-v`` 即退标记：命中即打印"<包名> <版本>"单行、
不进入命令分发（无"未知命令"报错、不 SystemExit）；以及未知命令的原有
报错路径不受影响（回归保护）。
"""

import sys

import pytest

from pykunlun.cli.manager import CommandManager


def test_version_flag_prints_single_line(monkeypatch, capsys):
    # --version：打印一行"<包名> <版本>"，包名与版本均非空（查不到元数据时回退"(版本未知)"）
    monkeypatch.setattr(sys, "argv", ["prog", "--version"])
    CommandManager().main_cli()
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    name, _, ver = lines[0].partition(" ")
    assert name and ver


def test_version_short_flag(monkeypatch, capsys):
    # -v 短形态与 --version 等价
    monkeypatch.setattr(sys, "argv", ["prog", "-v"])
    CommandManager().main_cli()
    assert capsys.readouterr().out.strip()


def test_version_flag_wins_over_command(monkeypatch, capsys):
    # 标记可出现在任意位置，且优先于命令分发（含合法命令名在前的情况）
    monkeypatch.setattr(sys, "argv", ["prog", "help", "--version"])
    CommandManager().main_cli()
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1  # 只打印版本行，没有再执行 help


def test_unknown_command_still_errors(monkeypatch, capsys):
    # 回归：无 --version 时未知命令仍走原有报错路径（打印提示并 exit 1）
    monkeypatch.setattr(sys, "argv", ["prog", "no_such_cmd"])
    with pytest.raises(SystemExit) as exc_info:
        CommandManager().main_cli()
    assert exc_info.value.code == 1
    assert "未知命令" in capsys.readouterr().out

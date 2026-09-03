"""
pykunlun.action.manager 的单元测试。

覆盖 ActionManager 的注册/覆盖/注销/通配符查询/清空/执行全链路，以及
参数校验（空名、非可调用对象）与多实例隔离。全部用全新实例测试，不依赖
任何全局状态。
"""

import pytest

from pykunlun.action import ActionManager


def test_register_and_get():
    # 首次注册返回 None；get_action/has_action 可查到
    mgr = ActionManager()
    def ping():
        return 'pong'
    assert mgr.register('ip.query', ping) is None
    assert mgr.get_action('ip.query') is ping
    assert mgr.has_action('ip.query') is True
    assert mgr.has_action('ip.other') is False


def test_register_overwrite_returns_old():
    # 覆盖同名注册返回被覆盖的旧动作（支持包装增强后回注册）
    mgr = ActionManager()
    def v1():
        return 1
    def v2():
        return 2
    assert mgr.register('ip.query', v1) is None
    assert mgr.register('ip.query', v2) is v1
    assert mgr.execute('ip.query') == 2


def test_register_strips_name():
    # 名称前后空格被去除
    mgr = ActionManager()
    def f():
        pass
    mgr.register('  ip.query  ', f)
    assert mgr.has_action('ip.query') is True


def test_unregister():
    mgr = ActionManager()
    def f():
        pass
    mgr.register('ip.query', f)
    assert mgr.unregister('ip.query') is f
    assert mgr.unregister('ip.query') is None  # 不存在时返回 None


def test_get_names_wildcard_and_sorted():
    # 通配符查询（fnmatch），结果按名称升序
    mgr = ActionManager()
    for name in ('ip.query2', 'ip.query1', 'dns.resolve'):
        mgr.register(name, lambda: None)
    assert mgr.get_names('ip.*') == ['ip.query1', 'ip.query2']
    assert mgr.get_names() == ['dns.resolve', 'ip.query1', 'ip.query2']


def test_clear_returns_count():
    mgr = ActionManager()
    for name in ('ip.query1', 'ip.query2', 'dns.resolve'):
        mgr.register(name, lambda: None)
    # 按模式清空，返回实际清除数量
    assert mgr.clear('ip.*') == 2
    assert mgr.get_names() == ['dns.resolve']
    # 清空全部
    assert mgr.clear() == 1
    assert mgr.get_names() == []


def test_execute_passthrough_args_and_kwargs():
    # 位置参数与关键字参数透传给动作本体
    mgr = ActionManager()
    def add(a, b, *, offset=0):
        return a + b + offset
    mgr.register('math.add', add)
    assert mgr.execute('math.add', 1, 2, offset=10) == 13


def test_execute_missing_raises_keyerror():
    mgr = ActionManager()
    with pytest.raises(KeyError):
        mgr.execute('not.exist')


def test_empty_name_raises_valueerror():
    # None / 空串 / 纯空白均为非法名称
    mgr = ActionManager()
    def f():
        pass
    for bad in (None, '', '   '):
        with pytest.raises(ValueError):
            mgr.register(bad, f)
        with pytest.raises(ValueError):
            mgr.unregister(bad)
        with pytest.raises(ValueError):
            mgr.execute(bad)


def test_non_callable_raises_typeerror():
    mgr = ActionManager()
    with pytest.raises(TypeError):
        mgr.register('ip.query', 'not-callable')


def test_instances_isolated():
    # 管理器可多实例，动作表互不串扰
    m1, m2 = ActionManager(), ActionManager()
    def f():
        return 1
    m1.register('ip.query', f)
    assert m1.has_action('ip.query') is True
    assert m2.has_action('ip.query') is False
    assert m2.get_names() == []

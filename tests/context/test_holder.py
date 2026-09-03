"""
pykunlun.context.holder 的单元测试。

覆盖 ContextHolder 的 get/set/reset/using 语义：默认值、Token 还原、
上下文管理器退出/异常时自动还原、yield 回传。
"""

from pykunlun.context import ContextHolder


def test_get_default_none():
    # 未 set 时返回默认值 None
    holder = ContextHolder('holder_test')
    assert holder.get() is None


def test_custom_default():
    holder = ContextHolder('holder_test', default='fallback')
    assert holder.get() == 'fallback'


def test_name_property():
    holder = ContextHolder('my_name')
    assert holder.name == 'my_name'


def test_set_then_reset():
    # set 返回 Token，reset 后恢复到 set 之前的值
    holder = ContextHolder('holder_test')
    token = holder.set('v1')
    assert holder.get() == 'v1'
    holder.reset(token)
    assert holder.get() is None


def test_using_restores_on_exit():
    holder = ContextHolder('holder_test')
    with holder.using('v'):
        assert holder.get() == 'v'
    assert holder.get() is None  # 出了 with 自动还原


def test_using_yields_value():
    # with ... as x 拿到传入值本身
    holder = ContextHolder('holder_test')
    with holder.using('v') as x:
        assert x == 'v'


def test_using_restores_on_exception():
    # with 块内抛异常也能还原
    holder = ContextHolder('holder_test')
    try:
        with holder.using('v'):
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    assert holder.get() is None


def test_nested_using():
    # 嵌套 using 逐层还原
    holder = ContextHolder('holder_test')
    with holder.using('outer'):
        with holder.using('inner'):
            assert holder.get() == 'inner'
        assert holder.get() == 'outer'
    assert holder.get() is None

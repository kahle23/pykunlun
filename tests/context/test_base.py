"""
pykunlun.context.base 的单元测试。

覆盖 Context 抽象基类的契约：不可直接实例化，子类实现 get_storage 即可用。
"""

from collections.abc import MutableMapping

import pytest

from pykunlun.context import Context


def test_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        Context()  # type: ignore[abstract]


def test_subclass_with_get_storage_usable():
    # 实现唯一抽象方法 get_storage 即可实例化使用
    class MyContext(Context):
        def __init__(self):
            self._storage: dict[str, object] = {}

        def get_storage(self) -> MutableMapping[str, object]:
            return self._storage

    ctx = MyContext()
    ctx.get_storage()['key'] = 'value'
    assert ctx.get_storage() == {'key': 'value'}

"""
pykunlun.registry 注册表原子 :class:`~pykunlun.registry.atom.Registry` 的单元测试。

覆盖：小写折叠 / 保留原样、替换语义、注销幂等、未命中返回 None、
None 值拒绝、replace=False 拒绝覆盖、非 str 键快速失败。
"""

import pytest

from pykunlun.registry import Registry


class TestRegistry:
    """
    测试注册表原子的存取语义。
    """

    def test_fold_case(self) -> None:
        """
        默认小写折叠：任意大小写入参等价，键以折叠形态存储。
        """
        reg: Registry[str] = Registry()
        reg.register('ABC', 'x')
        assert reg.get('abc') == 'x'
        assert reg.keys() == ['abc']
        assert reg.unregister('AbC') is True
        assert reg.unregister('abc') is False  # 幂等

    def test_preserve_case(self) -> None:
        """
        fold_case=False 保留原样：大小写不同的键互不混淆。
        """
        reg: Registry[str] = Registry(fold_case=False)
        reg.register('AbC', 'x')
        assert reg.get('ABC') is None
        assert reg.get('AbC') == 'x'

    def test_replace_semantics(self) -> None:
        """
        重复注册同键覆盖旧值。
        """
        reg: Registry[str] = Registry()
        reg.register('k', 'old')
        reg.register('k', 'new')
        assert reg.get('k') == 'new'
        assert reg.keys() == ['k']

    def test_get_miss_returns_none(self) -> None:
        """
        未命中返回 None（报错语义由调用方表达）。
        """
        reg: Registry[str] = Registry()
        assert reg.get('missing') is None

    def test_register_rejects_none_item(self) -> None:
        """
        None 保留作 get 的未命中哨兵，注册即拒（ValueError）。
        """
        reg: Registry[str] = Registry()
        with pytest.raises(ValueError, match='None'):
            reg.register('k', None)  # type: ignore[arg-type]
        assert reg.keys() == []

    def test_register_replace_false_rejects_existing(self) -> None:
        """
        replace=False 时同键重复注册抛 ValueError 且保留旧值；默认 True 保持替换。
        """
        reg: Registry[str] = Registry()
        reg.register('k', 'old')
        with pytest.raises(ValueError, match='replace=False'):
            reg.register('k', 'new', replace=False)
        assert reg.get('k') == 'old'
        reg.register('k', 'new')
        assert reg.get('k') == 'new'

    def test_non_str_key_fails_fast(self) -> None:
        """
        非 str 键在 register/get/unregister 上均快速失败（TypeError），
        含 fold_case=False 时原本会静默入库坏键的路径。
        """
        reg: Registry[str] = Registry()
        preserve: Registry[str] = Registry(fold_case=False)
        with pytest.raises(TypeError, match='键必须为 str'):
            reg.register(None, 'x')  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='键必须为 str'):
            preserve.register(object(), 'x')  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='键必须为 str'):
            reg.get(123)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='键必须为 str'):
            preserve.unregister(object())  # type: ignore[arg-type]
        assert preserve.keys() == []

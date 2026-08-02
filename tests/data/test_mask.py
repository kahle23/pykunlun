"""
kunlun.data.mask 抽象层的单元测试。

仅测抽象本身：Masker 的实例占位符配置与私有原语（_mask_all / _mask_part）、
ABC 约束，以及 MaskManager 的注册表 CRUD / 自动探测 / 按名分发 / _resolve_name
（含"默认注册全部内置数据策略"与"无命中原样返回"等契约）。
具体内置策略的行为由 tests/util/test_maskutil.py 覆盖。
"""

from typing import Any, Callable

import pytest

from kunlun.data.mask import Masker, MaskManager


# 测试用桩策略：按 support_fn 判定，apply_fn 处理；name/priority/占位符透传给 Masker.__init__。
# 泛型基类收到 Any，故 support/apply 的 value 也收 Any。
class _StubMasker(Masker[Any]):
    def __init__(self, name: str,
                 support_fn: Callable[[Any], bool],
                 apply_fn: Callable[[Any], Any],
                 priority: int = 0,
                 mask_placeholder: str = '*') -> None:
        super().__init__(name=name, priority=priority,
                         mask_placeholder=mask_placeholder)
        self._support_fn = support_fn
        self._apply_fn = apply_fn

    def support(self, value: Any) -> bool:
        return self._support_fn(value)

    def apply(self, value: Any) -> Any:
        return self._apply_fn(value)


# 用于验证占位符配置的桩：apply 走继承到的私有原语（str 专用）。
class _PlaceholderMasker(Masker[str]):
    def __init__(self, name: str = 'placeholder', priority: int = 0, **kwargs):
        super().__init__(name, priority, **kwargs)

    def support(self, value: str) -> bool:
        return True

    def apply(self, value: str) -> str:
        return self._mask_part(value, 1, 1)


class _AlwaysHi(Masker[str]):
    def __init__(self, name: str = 'hi', priority: int = 100, **kwargs):
        super().__init__(name, priority, **kwargs)

    def support(self, value: str) -> bool:
        return value.startswith('hi')

    def apply(self, value: str) -> str:
        return 'HI'


# region ======== Masker：实例占位符 + 私有原语 + ABC ========

class TestMaskerAbc:
    def test_not_instantiable(self):
        """Masker 是 ABC，不能直接实例化。"""
        with pytest.raises(TypeError):
            Masker()  # type: ignore[abstract]


class TestMaskerPlaceholders:
    """mask_placeholder 可在实例化时配置，默认为单个 ``'*'``。"""

    def test_default_placeholder(self):
        m = _PlaceholderMasker()
        assert m.mask_placeholder == '*'

    def test_custom_placeholder_via_constructor(self):
        m = _PlaceholderMasker(mask_placeholder='#')
        assert m.mask_placeholder == '#'

    def test_per_instance_independence(self):
        """不同实例的占位符互不影响。"""
        a = _PlaceholderMasker(mask_placeholder='#')
        b = _PlaceholderMasker(mask_placeholder='X')
        assert a.mask_placeholder == '#'
        assert b.mask_placeholder == 'X'


class TestMaskerNamePriority:
    """name / priority 为实例属性，默认来自子类 __init__，可按实例覆盖。"""

    def test_defaults_from_init(self):
        """不传时取子类 __init__ 的默认值。"""
        assert _AlwaysHi().name == 'hi'
        assert _AlwaysHi().priority == 100
        assert _StubMasker('s', lambda v: True, lambda v: 'S', priority=7).priority == 7

    def test_override_name(self):
        assert _AlwaysHi(name='hi-custom').name == 'hi-custom'

    def test_override_priority(self):
        assert _AlwaysHi(priority=1).priority == 1

    def test_per_instance_independence(self):
        """不同实例的 name/priority 互不影响。"""
        a = _AlwaysHi(name='a')
        b = _AlwaysHi(name='b')
        assert a.name == 'a'
        assert b.name == 'b'
        assert _AlwaysHi().name == 'hi'  # 新实例仍是默认值

    def test_override_registers_under_custom_name(self):
        """覆盖 name 后可按自定义名注册到管理器。"""
        mgr = MaskManager()
        mgr.register_masker(_AlwaysHi(name='hi-v2'))
        assert mgr.has_masker('hi-v2') is True
        assert mgr.has_masker('hi') is False


class TestMaskAllPrimitive:
    """_mask_all：整体替换为 mask_placeholder 重复 3 次。"""

    def test_default_placeholder(self):
        m = _PlaceholderMasker()
        assert m._mask_all('secret') == '***'
        assert m._mask_all('') == '***'

    def test_custom_placeholder(self):
        m = _PlaceholderMasker(mask_placeholder='?')
        assert m._mask_all('secret') == '???'


class TestMaskPartPrimitive:
    """_mask_part：保首尾、掩中间，用实例 mask_placeholder。"""

    def test_phone_style(self):
        m = _PlaceholderMasker()
        assert m._mask_part('13812345678', 3, 4) == '138****5678'

    def test_single_middle_char(self):
        assert _PlaceholderMasker()._mask_part('abc', 1, 1) == 'a*c'

    def test_keep_zero(self):
        assert _PlaceholderMasker()._mask_part('abcd', 0, 0) == '****'

    def test_overlap_returns_unchanged(self):
        m = _PlaceholderMasker()
        assert m._mask_part('ab', 1, 1) == 'ab'
        assert m._mask_part('ab', 5, 5) == 'ab'

    def test_empty_string(self):
        assert _PlaceholderMasker()._mask_part('', 3, 4) == ''

    def test_negative_keep_treated_as_zero(self):
        assert _PlaceholderMasker()._mask_part('abcd', -1, 2) == '**cd'

    def test_custom_mask_placeholder(self):
        m = _PlaceholderMasker(mask_placeholder='X')
        assert m._mask_part('13812345678', 3, 4) == '138XXXX5678'

    def test_placeholder_flows_through_apply(self):
        """apply 经 _mask_part 透传实例占位符。"""
        m = _PlaceholderMasker(mask_placeholder='#')
        assert m.apply('abcdef') == 'a####f'

# endregion


# region ======== MaskManager ========

class TestMaskManagerRegister:
    def test_empty_manager(self):
        mgr = MaskManager()
        assert set(mgr.get_masker_names()) == {
            'phone', 'idcard', 'bankcard', 'email', 'name', 'default',
        }
        assert mgr.has_masker('x') is False

    def test_register_multiple_maskers(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: True, lambda v: 'A'))
        mgr.register_masker(_StubMasker('b', lambda v: True, lambda v: 'B'))
        assert {'a', 'b'} <= set(mgr.get_masker_names())

    def test_register_returns_none_for_new(self):
        mgr = MaskManager()
        assert mgr.register_masker(_StubMasker('x', lambda v: True, lambda v: 'X')) is None

    def test_register_overrides_and_returns_old(self):
        mgr = MaskManager()
        old = _StubMasker('x', lambda v: True, lambda v: 'old')
        new = _StubMasker('x', lambda v: True, lambda v: 'new')
        mgr.register_masker(old)
        returned = mgr.register_masker(new)
        assert returned is old
        assert mgr.get_masker('x') is new

    def test_register_non_masker_raises(self):
        mgr = MaskManager()
        with pytest.raises(TypeError):
            mgr.register_masker('not-a-masker')  # type: ignore[arg-type]

    def test_register_empty_name_raises(self):
        mgr = MaskManager()
        with pytest.raises(ValueError):
            mgr.register_masker(_StubMasker('   ', lambda v: True, lambda v: 'x'))

    def test_get_and_has(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: True, lambda v: 'A'))
        assert mgr.has_masker('a') is True
        assert mgr.get_masker('a') is not None
        assert mgr.get_masker('nope') is None
        assert mgr.has_masker('nope') is False

    def test_unregister(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: True, lambda v: 'A'))
        assert mgr.unregister_masker('a') is not None
        assert mgr.unregister_masker('a') is None

    def test_names_pattern(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('all', lambda v: True, lambda v: '1'))
        mgr.register_masker(_StubMasker('bank', lambda v: True, lambda v: '2'))
        assert {'all', 'bank'} <= set(mgr.get_masker_names())
        assert mgr.get_masker_names('????') == ['bank', 'name']  # 仅 4 字母名命中（'all' 3 字母不匹配）
        assert mgr.get_masker_names('a*') == ['all']


class TestMaskManagerAutoDetect:
    def test_first_support_wins(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: v.startswith('a'), lambda v: 'A', priority=999))
        mgr.register_masker(_StubMasker('b', lambda v: True, lambda v: 'B', priority=999))
        assert mgr.mask('apple') == 'A'
        assert mgr.mask('other') == 'B'  # b 兜底

    def test_priority_orders_probe(self):
        """高优先级先试探，即使后注册。"""
        low = _StubMasker('low', lambda v: True, lambda v: 'LOW', priority=999)
        high = _StubMasker('high', lambda v: True, lambda v: 'HIGH', priority=9999)
        mgr = MaskManager()
        mgr.register_masker(low)   # low 先注册但优先级低
        mgr.register_masker(high)
        assert mgr.mask('x') == 'HIGH'

    def test_priority_tie_keeps_registration_order(self):
        """同优先级按注册顺序。"""
        first = _StubMasker('first', lambda v: True, lambda v: '1', priority=999)
        second = _StubMasker('second', lambda v: True, lambda v: '2', priority=999)
        mgr = MaskManager()
        mgr.register_masker(first)
        mgr.register_masker(second)
        assert mgr.mask('x') == '1'

    def test_no_match_returns_unchanged(self):
        """无策略命中时原样返回（识别不了就不乱改）。

        新建 MaskManager 已注册 ``'default'``（str 兜底），它会吃掉任何字符串，故需先
        注销兜底，才能真正验证"无人命中 → 原样返回"的契约。
        """
        mgr = MaskManager()
        mgr.unregister_masker('default')
        mgr.register_masker(_StubMasker('a', lambda v: False, lambda v: 'A', priority=999))
        assert mgr.mask('anything') == 'anything'

    def test_non_str_value_passthrough(self):
        """非字符串值不被任何内置数据策略认领时原样返回（各策略 support 均类型守卫）。"""
        mgr = MaskManager()
        assert mgr.mask(12345) == 12345
        assert mgr.mask(None) is None

    def test_mask_does_not_mutate_input(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: True, lambda v: 'A'))
        value = 'hello'
        mgr.mask(value)
        assert value == 'hello'


class TestMaskManagerByName:
    def test_explicit_dispatch(self):
        mgr = MaskManager()
        mgr.register_masker(_StubMasker('a', lambda v: False, lambda v: 'A'))
        # support 故意返回 False，但 mask_by_name 跳过判定直接处理
        assert mgr.mask_by_name('a', 'x') == 'A'

    def test_unknown_name_raises_keyerror(self):
        mgr = MaskManager()
        with pytest.raises(KeyError):
            mgr.mask_by_name('nope', 'x')

    def test_empty_name_raises(self):
        mgr = MaskManager()
        with pytest.raises(ValueError):
            mgr.mask_by_name('  ', 'x')


class TestResolveName:
    def test_strips_and_validates(self):
        assert MaskManager()._resolve_name('  name  ') == 'name'

    def test_empty_raises(self):
        mgr = MaskManager()
        with pytest.raises(ValueError):
            mgr._resolve_name('')
        with pytest.raises(ValueError):
            mgr._resolve_name('   ')

    def test_overridable_by_subclass(self):
        """_resolve_name 为实例方法，子类可覆写自定义名称规则。"""

        class _CustomManager(MaskManager):
            def _resolve_name(self, name):
                return super()._resolve_name(name).upper()

        mgr = _CustomManager()
        assert mgr._resolve_name('  abc  ') == 'ABC'

# endregion

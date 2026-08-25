"""
pykunlun.ai.ocr 的单元测试。

用**不依赖 rapidocr / opencv** 的 stub 引擎类验证：
  - :class:`OcrEngine` 的 ``engine_type`` 推导与类型不匹配校验；
  - :class:`OcrEngine` 的 ``engine_type`` / ``cfg`` 运行时不可变保护；
  - :class:`OcrManager` 类注册表（:meth:`register_engine_class` /
    :meth:`get_engine_class` / :meth:`get_registered_engine_types` /
    :meth:`unregister_engine_class`）；
  - :class:`OcrManager` 实例注册表（:meth:`register_engine` 工厂化创建与直传实例、
    :meth:`get_engine` 按名查找、:meth:`get_registered_engine_names`、
    :meth:`unregister_engine`），以及同类型多别名并存。
"""

import pytest

from pykunlun.ai.ocr import OcrCfg, OcrEngine, OcrManager


class _StubOcr(OcrEngine):
    """最小 stub 引擎：engine_type='stub'，识别恒定返回空（不触达任何重依赖）。"""

    engine_type = 'stub'

    def _recognize_array(self, image):
        return []


# region ======== OcrEngine：engine_type 与不可变性 ========


class TestOcrEngineEngineType:
    def test_engine_type_derived_when_cfg_omits(self):
        # cfg.engine_type 省略时由实现类常量推导并回填 cfg
        cfg = OcrCfg()
        eng = _StubOcr(cfg)
        assert eng.engine_type == 'stub'
        assert cfg.engine_type == 'stub'

    def test_engine_type_explicit_match_ok(self):
        eng = _StubOcr(OcrCfg(engine_type='stub'))
        assert eng.engine_type == 'stub'

    def test_engine_type_mismatch_raises(self):
        # 显式传入与实现类常量不一致 → 类型不匹配
        with pytest.raises(ValueError, match='引擎类型不匹配'):
            _StubOcr(OcrCfg(engine_type='another'))

    def test_engine_type_immutable(self):
        eng = _StubOcr(OcrCfg())
        with pytest.raises(AttributeError):
            eng.engine_type = 'x'

    def test_cfg_immutable_after_construction(self):
        eng = _StubOcr(OcrCfg())
        with pytest.raises(AttributeError):
            eng.cfg = OcrCfg()


# endregion

# region ======== OcrManager：类注册表 + 工厂 + 实例别名 ========


class TestOcrManager:
    def test_register_engine_class_and_lookup(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        assert m.get_engine_class('stub') is _StubOcr
        assert 'stub' in m.get_registered_engine_types()

    def test_register_engine_class_case_insensitive(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        assert m.get_engine_class('STUB') is _StubOcr

    def test_unregister_engine_class(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        assert m.unregister_engine_class('stub') is True
        assert 'stub' not in m.get_registered_engine_types()

    def test_register_engine_factory_from_cfg(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        m.register_engine('default', OcrCfg(engine_type='stub'))
        eng = m.get_engine()
        assert isinstance(eng, _StubOcr)
        assert 'default' in m.get_registered_engine_names()

    def test_register_engine_factory_unknown_type_raises(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        with pytest.raises(ValueError, match='未找到 engine_type'):
            m.register_engine('x', OcrCfg(engine_type='missing'))

    def test_register_engine_direct_instance(self):
        m = OcrManager()
        eng = _StubOcr(OcrCfg())
        m.register_engine('a', eng)
        assert m.get_engine('a') is eng

    def test_same_type_multiple_names_coexist(self):
        # "A 与定制版并存"：同 engine_type、不同配置，靠 name 别名区分
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        m.register_engine('default', OcrCfg(engine_type='stub', use_angle_cls=True))
        m.register_engine('fast', OcrCfg(engine_type='stub', use_angle_cls=False))
        assert isinstance(m.get_engine('default'), _StubOcr)
        assert isinstance(m.get_engine('fast'), _StubOcr)
        assert m.get_engine('default').cfg.use_angle_cls is True
        assert m.get_engine('fast').cfg.use_angle_cls is False

    def test_get_engine_missing_raises(self):
        m = OcrManager()
        with pytest.raises(ValueError, match='未找到实例'):
            m.get_engine('nope')

    def test_unregister_engine(self):
        m = OcrManager()
        m.register_engine_class(_StubOcr)
        m.register_engine('default', OcrCfg(engine_type='stub'))
        assert m.unregister_engine() is True
        assert 'default' not in m.get_registered_engine_names()

    def test_config_loader_fallback(self):
        loaded = []

        def loader(mgr, name):
            loaded.append(name)
            mgr.register_engine(name, _StubOcr(OcrCfg()))

        m = OcrManager(config_loader=loader)
        eng = m.get_engine('lazy')  # 首次未命中 → 触发 loader
        assert loaded == ['lazy']
        assert isinstance(eng, _StubOcr)


# endregion

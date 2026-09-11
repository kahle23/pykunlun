"""
pykunlun.registry 双层注册表管理器基类 :class:`~pykunlun.registry.base.RegistryManager` 的单元测试。

以"影子子类" :class:`DemoManager` 验证派生效果（词汇常量 + API 别名 + 钩子，
即将来 oss/ocr/rdb manager 迁移后的形态），覆盖：
  - :class:`~pykunlun.registry.base.RegistryManager` 类注册表：注册/替换、
    类型校验（TypeError）、键属性校验（ValueError）、大小写不敏感查找、
    未注册报错文案（含词汇插值）、已注册类型列举
  - 实例注册表：cfg 工厂化注册、实例直接注册（含运行时类型校验）、默认名回落、
    名称保留大小写、配置加载器回退（未命中才调用、加载后重试）、
    未注册报错文案、replace=False 拒绝覆盖
  - 模板方法：cfg 空类型键显式报错；:meth:`_wrap_item` 钩子在两条注册路径均生效
  - :meth:`RegistryManager.__init_subclass__`：词汇常量缺失在类定义时即报 TypeError
  - 并发：多线程注册不丢项
"""

import threading
from dataclasses import dataclass
from typing import ClassVar

import pytest

from pykunlun.registry import RegistryManager


# region ======== 演示领域（影子子类：模拟 oss manager 迁移后的形态） ========
@dataclass
class DemoCfg:
    """
    演示配置，``kind`` 即类型标识（对应 oss 的 oss_type）。
    """

    kind: str | None = None


class DemoClient:
    """
    演示客户端基类，类型标识取自类属性 ``kind``。
    """

    kind: ClassVar[str]

    def __init__(self, cfg: DemoCfg) -> None:
        self.cfg = cfg


class FastDemoClient(DemoClient):
    kind = 'fast'


class DeepDemoClient(DemoClient):
    kind = 'deep'


class DemoManager(RegistryManager[DemoClient, DemoCfg]):
    """
    演示管理器：词汇常量 + API 别名，即领域 manager 迁移后的全部样子。
    """

    TYPE_ATTR = 'kind'
    ITEM_LABEL = 'DemoClient 实现类'
    REGISTERED_LABEL = '类型'
    CLASS_HINT = 'register_client_class'
    INSTANCE_HINT = 'register'
    CFG_LABEL = 'DemoCfg'
    ITEM_BASE: ClassVar[type[DemoClient]] = DemoClient
    CFG_CLS: ClassVar[type[DemoCfg]] = DemoCfg

    # 既有公共 API 名以别名保持不变（从特化后的基类取方法，保签名完整特化）
    register_client_class = RegistryManager[DemoClient, DemoCfg].register_class
    unregister_client_class = RegistryManager[DemoClient, DemoCfg].unregister_class
    get_client_class = RegistryManager[DemoClient, DemoCfg].get_class
    get_registered_client_types = RegistryManager[DemoClient, DemoCfg].get_registered_types
    register = RegistryManager[DemoClient, DemoCfg].register_instance
    unregister = RegistryManager[DemoClient, DemoCfg].unregister_instance
    get_client = RegistryManager[DemoClient, DemoCfg].get_instance
    get_registered_names = RegistryManager[DemoClient, DemoCfg].get_registered_names
# endregion

# region ======== 类注册表 ========
class TestClassRegistry:
    """
    测试类注册表动词（经 DemoManager 别名调用，验证 API 形态）。
    """

    def test_register_and_get_case_insensitive(self) -> None:
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        manager.register_client_class(DeepDemoClient)
        assert manager.get_client_class('FAST') is FastDemoClient
        assert manager.get_client_class('deep') is DeepDemoClient
        assert manager.get_registered_client_types() == ['fast', 'deep']

    def test_register_replaces(self) -> None:
        """
        同类型重复注册覆盖旧类。
        """

        class Fast2DemoClient(FastDemoClient):
            pass

        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        manager.register_client_class(Fast2DemoClient)
        assert manager.get_client_class('fast') is Fast2DemoClient
        assert manager.get_registered_client_types() == ['fast']

    def test_register_rejects_non_subclass(self) -> None:
        manager = DemoManager()
        with pytest.raises(TypeError, match='仅接受 DemoClient 的子类'):
            manager.register_client_class(DemoCfg)  # type: ignore[arg-type]

    def test_register_rejects_empty_kind(self) -> None:
        class EmptyKindDemoClient(FastDemoClient):
            kind = ''

        manager = DemoManager()
        with pytest.raises(ValueError, match='kind 必须是非空字符串'):
            manager.register_client_class(EmptyKindDemoClient)

    def test_get_class_rejects_empty(self) -> None:
        manager = DemoManager()
        with pytest.raises(ValueError, match='kind 不能为空'):
            manager.get_client_class('')

    def test_get_class_miss_message(self) -> None:
        """
        未注册报错按词汇常量插值，领域词保留。
        """
        manager = DemoManager()
        with pytest.raises(ValueError, match="未找到 kind='missing' 对应的 DemoClient 实现类"):
            manager.get_client_class('missing')
        # 空注册表时列出"（无）"
        with pytest.raises(ValueError, match='已注册的类型: （无）'):
            manager.get_client_class('missing')

    def test_unregister_class(self) -> None:
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        assert manager.unregister_client_class('FAST') is True
        assert manager.unregister_client_class('fast') is False
        assert manager.unregister_client_class('') is False
        with pytest.raises(ValueError, match='（无）'):
            manager.get_client_class('fast')

    def test_register_class_replace_false(self) -> None:
        """
        replace=False 时同类型重复注册抛 ValueError 且保留旧类。
        """
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        with pytest.raises(ValueError, match='replace=False'):
            manager.register_client_class(FastDemoClient, replace=False)
        assert manager.get_registered_client_types() == ['fast']
# endregion

# region ======== 实例注册表 ========
class TestInstanceRegistry:
    """
    测试实例注册表动词与配置加载器回退。
    """

    def test_register_via_cfg_factory(self) -> None:
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        manager.register('default', DemoCfg(kind='fast'))
        client = manager.get_client()
        assert isinstance(client, FastDemoClient)
        assert client.cfg == DemoCfg(kind='fast')

    def test_register_via_instance(self) -> None:
        manager = DemoManager()
        prebuilt = FastDemoClient(DemoCfg(kind='fast'))
        manager.register('pre', prebuilt)  # 不依赖类注册表
        assert manager.get_client('pre') is prebuilt

    def test_register_rejects_non_item_object(self) -> None:
        """
        实例分支运行时校验：非 DemoClient 实例在注册现场即抛 TypeError，
        不再延迟到使用点才爆 AttributeError。
        """
        manager = DemoManager()
        with pytest.raises(TypeError, match='须为 DemoCfg 配置或 DemoClient 实例'):
            manager.register('bad', {'oops': 'a dict'})  # type: ignore[arg-type]
        with pytest.raises(TypeError, match='收到: None'):
            manager.register('n', None)  # type: ignore[arg-type]
        assert manager.get_registered_names() == []

    def test_register_swapped_args_fail_fast(self) -> None:
        """
        参数传反（cfg 当 name、name 当 item）在注册现场即抛 TypeError，
        报错里能看到落错位置的值；不再静默入库坏键。
        """
        manager = DemoManager()
        with pytest.raises(TypeError, match="收到: 'default'"):
            manager.register(DemoCfg(kind='fast'), 'default')  # type: ignore[arg-type]
        assert manager.get_registered_names() == []

    def test_register_replace_false(self) -> None:
        """
        replace=False 时同名重复注册抛 ValueError 且保留旧实例。
        """
        manager = DemoManager()
        first = FastDemoClient(DemoCfg(kind='fast'))
        second = DeepDemoClient(DemoCfg(kind='deep'))
        manager.register('a', first)
        with pytest.raises(ValueError, match='replace=False'):
            manager.register('a', second, replace=False)
        assert manager.get_client('a') is first

    def test_default_name_fallback(self) -> None:
        """
        name 省略/为空均回落 DEFAULT_NAME。
        """
        manager = DemoManager()
        prebuilt = FastDemoClient(DemoCfg(kind='fast'))
        manager.register('', prebuilt)
        assert manager.get_client() is prebuilt
        assert manager.get_client(None) is prebuilt
        assert manager.get_registered_names() == ['default']

    def test_names_preserve_case(self) -> None:
        """
        实例名保留大小写（与类型键的小写折叠口径不同）。
        """
        manager = DemoManager()
        prebuilt = FastDemoClient(DemoCfg(kind='fast'))
        manager.register('ReportFiles', prebuilt)
        assert manager.get_client('ReportFiles') is prebuilt
        with pytest.raises(ValueError, match="未找到实例 'reportfiles'"):
            manager.get_client('reportfiles')

    def test_replace_and_unregister(self) -> None:
        manager = DemoManager()
        first = FastDemoClient(DemoCfg(kind='fast'))
        second = DeepDemoClient(DemoCfg(kind='deep'))
        manager.register('a', first)
        manager.register('a', second)
        assert manager.get_client('a') is second
        assert manager.unregister('a') is True
        assert manager.unregister('a') is False

    def test_get_miss_message(self) -> None:
        manager = DemoManager()
        manager.register('a', FastDemoClient(DemoCfg(kind='fast')))
        with pytest.raises(ValueError, match="未找到实例 'ghost'，已注册的实例: a；请先通过 register\\(\\) 注册"):
            manager.get_client('ghost')

    def test_cfg_empty_kind_raises_explicitly(self) -> None:
        """
        cfg 空类型键显式报错（统一后的行为，不再静默 miss）。
        """
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        with pytest.raises(ValueError, match='必须显式提供 cfg.kind'):
            manager.register('x', DemoCfg(kind=None))

    def test_config_loader_fallback(self) -> None:
        """
        按名未命中才调用加载器，加载成功后重试查找命中。
        """
        calls: list[str] = []

        def loader(manager: DemoManager, name: str) -> None:
            calls.append(name)
            manager.register(name, DemoCfg(kind='fast'))

        manager = DemoManager(config_loader=loader)
        manager.register_client_class(FastDemoClient)
        client = manager.get_client('lazy')
        assert isinstance(client, FastDemoClient)
        assert calls == ['lazy']
        # 已注册的名称不再触发加载器
        manager.get_client('lazy')
        assert calls == ['lazy']

    def test_config_loader_miss_still_raises(self) -> None:
        """
        加载器未能加载（未注册）时仍抛未注册错误。
        """

        def noop_loader(manager: DemoManager, name: str) -> None:
            return None

        manager = DemoManager(config_loader=noop_loader)
        with pytest.raises(ValueError, match='（无）'):
            manager.get_client('missing')

    def test_get_config_loader(self) -> None:
        def loader(manager: DemoManager, name: str) -> None:
            return None

        assert DemoManager().get_config_loader() is None
        assert DemoManager(config_loader=loader).get_config_loader() is loader
# endregion

# region ======== 模板方法与钩子 ========
class TestTemplateAndHook:
    """
    测试 _create_from_cfg 模板与 _wrap_item 钩子的生效路径。
    """

    def test_wrap_item_applies_to_both_paths(self) -> None:
        """
        钩子在 cfg 工厂化与预构造实例两条注册路径均被调用。
        """

        class WrappingManager(DemoManager):
            def __init__(self) -> None:
                super().__init__()
                self.wrapped: list[DemoClient] = []

            def _wrap_item(self, item: DemoClient) -> DemoClient:
                self.wrapped.append(item)
                return item

        manager = WrappingManager()
        manager.register_client_class(FastDemoClient)
        manager.register('via_cfg', DemoCfg(kind='fast'))
        manager.register('via_instance', FastDemoClient(DemoCfg(kind='fast')))
        assert len(manager.wrapped) == 2

    def test_default_wrap_is_identity(self) -> None:
        manager = DemoManager()
        item = FastDemoClient(DemoCfg(kind='fast'))
        assert manager._wrap_item(item) is item
# endregion

# region ======== 派生校验与并发 ========
class TestSubclassValidation:
    """
    测试词汇常量的类定义期校验。
    """

    def test_missing_vocab_fails_at_definition(self) -> None:
        with pytest.raises(TypeError, match='缺少注册表词汇常量'):
            class _BadManager(RegistryManager[DemoClient, DemoCfg]):  # pyright: ignore[reportUnusedClass]
                pass

    def test_partial_vocab_reports_all_missing(self) -> None:
        with pytest.raises(TypeError) as excinfo:
            class _PartialManager(RegistryManager[DemoClient, DemoCfg]):  # pyright: ignore[reportUnusedClass]
                TYPE_ATTR = 'kind'
        message = str(excinfo.value)
        for missing in ('ITEM_LABEL', 'REGISTERED_LABEL', 'CLASS_HINT', 'INSTANCE_HINT',
                        'CFG_LABEL', 'ITEM_BASE', 'CFG_CLS'):
            assert missing in message


class TestConcurrency:
    """
    测试共享单锁下多线程注册不丢项。
    """

    def test_concurrent_instance_registration(self) -> None:
        manager = DemoManager()
        manager.register_client_class(FastDemoClient)
        errors: list[Exception] = []

        def worker(index: int) -> None:
            try:
                for i in range(20):
                    manager.register(f'name{index}_{i}', DemoCfg(kind='fast'))
            except Exception as e:  # 测试收集线程内任意异常，汇总后断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert len(manager.get_registered_names()) == 8 * 20

    def test_concurrent_class_and_instance_registration(self) -> None:
        """
        类表与实例表并发读写共享一把锁，互不丢项。
        """
        manager = DemoManager()

        def class_worker() -> None:
            for _ in range(50):
                manager.register_client_class(FastDemoClient)

        def instance_worker(index: int) -> None:
            for _ in range(50):
                manager.register(f'inst{index}', DemoCfg(kind='fast'))

        threads = [threading.Thread(target=class_worker) for _ in range(2)]
        threads += [threading.Thread(target=instance_worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert manager.get_registered_client_types() == ['fast']
        assert len(manager.get_registered_names()) == 4
# endregion

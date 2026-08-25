"""
OCR 引擎管理器（双层注册表：engine 类 + engine 实例）。

:class:`OcrManager` 维护两张注册表：``engine_type -> OcrEngine 子类`` 的类注册表，
以及 ``name -> OcrEngine 实例`` 的实例注册表。前者用于按引擎类型工厂化创建引擎实例，
后者用于按别名管理绑定具体配置的实例。
"""

import threading
from collections.abc import Callable

from pykunlun.util import logutil

from .engine import OcrEngine
from .model import OcrCfg, OcrResult

log = logutil.getLogger(__name__)


class OcrManager:
    """
    OCR 引擎管理器（双层注册表：类 + 实例）。

    维护两张注册表：

      - **类注册表** ``engine_type -> OcrEngine 子类(class)``：管理各引擎类型对应的实现类。
        注册键取自类自身的 :attr:`~OcrEngine.engine_type`（自动小写归一化），无需调用方显式提供。
        通过 :meth:`register_engine_class` 注册后，即可用 :meth:`register_engine` 直接传入 :class:`OcrCfg`，
        由本管理器按 ``cfg.engine_type`` 工厂化创建实例——调用方无需手动 ``new``。
      - **实例注册表** ``name -> OcrEngine 实例``：管理绑定具体配置的引擎实例，每个实例绑定一份 :class:`OcrCfg`。
        同一管理器可注册多份不同配置的实例，通过名称（别名）区分。

    关于 ``name`` 的用途：name 是注册实例的**别名**（即引擎的注册名），用于区分同一引擎类型的不同配置，
        而非区分引擎本身。典型场景是按环境/用途隔离——例如为不同精度需求各注册一个 :class:`PaddleOcr` 实例
        （实现类相同、配置不同），通过 ``name="fast"`` / ``name="accurate"`` 分别访问；也可按业务模块命名。

    :attr:`DEFAULT_NAME` 为默认实例名称。
    除 :meth:`register_engine_class`（按类自身 engine_type 归档）与 :meth:`register_engine`（须显式提供 name）外，
    其余方法（:meth:`unregister_engine`、:meth:`get_engine`、:meth:`recognize`、:meth:`recognize_with_details`、
    :meth:`recognize_and_draw`）的 ``name`` 参数均可省略，省略时使用默认名称。

    本类额外提供 :meth:`recognize` / :meth:`recognize_with_details` / :meth:`recognize_and_draw` 便捷方法，
    比直接调用 :class:`OcrEngine` 同名方法多一个 ``name`` 参数（用于选择已注册的实例），其余参数语义一致。

    用法示例::

        manager = OcrManager()

        # 1) 注册实现类（engine_type 取自类自身，一次性）
        manager.register_engine_class(EasyOcr)
        manager.register_engine_class(PaddleOcr)

        # 2) 注册实例：直接传 OcrCfg，按 cfg.engine_type 自动 new
        cfg = OcrCfg(engine_type='easy')
        manager.register_engine("default", cfg)

        # 也仍可显式传入已构造的实例（不依赖类注册表）
        # manager.register_engine("default", EasyOcr(cfg))

        # 3) 通过管理器直接识别（name 可省略，默认 "default"）
        text = manager.recognize("image.png")

        # 指定 name 操作非默认实例
        manager.recognize("image.png", name="accurate")
    """

    # region ======== 构造 ========

    #: 默认实例名称
    DEFAULT_NAME = "default"

    def __init__(self, config_loader: Callable[['OcrManager', str], None] | None = None) -> None:
        """
        Args:
            config_loader: 配置加载器，当 :meth:`get_engine` 按名称查找失败时调用。
                签名 ``(manager: OcrManager, name: str) -> None``，
                由 loader 自行决定加载策略（如一次性加载、按需加载等）。
                为 ``None`` 时不启用 fallback。
        """
        # 类注册表：engine_type -> OcrEngine 子类（用于按 cfg.engine_type 工厂化创建实例）
        self._class_registry: dict[str, type[OcrEngine]] = {}
        # 实例注册表：name -> OcrEngine 实例（本实例独有）
        self._engine_registry: dict[str, OcrEngine] = {}
        self._lock = threading.RLock()
        self._config_loader = config_loader

    # endregion

    # region ======== getter ========

    def get_config_loader(self) -> Callable[['OcrManager', str], None] | None:
        """
        获取配置加载器。

        Returns:
            配置加载器 callable，未设置时返回 None。
        """
        return self._config_loader

    # endregion

    # region ======== 类注册表（engine_type -> OcrEngine 子类） ========

    def _create_engine_from_cfg(self, cfg: OcrCfg) -> OcrEngine:
        """
        按 ``cfg.engine_type`` 从类注册表取出实现类并实例化（内部工具）。

        Args:
            cfg: OCR 配置；由实现类在构造时按需校验/补全。``cfg.engine_type`` 为 ``None`` 时，
                由取出的实现类的 :attr:`~OcrEngine.engine_type` 推导。

        Returns:
            绑定该 cfg 的 :class:`OcrEngine` 实例。

        Raises:
            ValueError: ``cfg.engine_type`` 为空、或该类型未注册时抛出。
        """
        engine_type = cfg.engine_type
        key = engine_type.lower() if engine_type else ''
        with self._lock:
            engine_cls = self._class_registry.get(key)
            if engine_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 engine_type={engine_type!r} 对应的 OcrEngine 实现类，"
                    f"已注册的引擎: {registered}；请先通过 register_engine_class() 注册"
                )
        return engine_cls(cfg)

    def register_engine_class(self, engine_cls: type[OcrEngine]) -> None:
        """
        注册或替换一个 :class:`OcrEngine` 实现类（按类自身的 :attr:`~OcrEngine.engine_type` 归档）。

        注册后即可通过 :meth:`register_engine` 传入 :class:`OcrCfg`，
        由本管理器根据 ``cfg.engine_type`` 工厂化创建实例，调用方无需手动 ``new``。

        Args:
            engine_cls: :class:`OcrEngine` 的具体子类（类对象，非实例）。

        Raises:
            TypeError: 传入的不是 :class:`OcrEngine` 子类时抛出。
            ValueError: 类的 :attr:`~OcrEngine.engine_type` 为空时抛出。
        """
        if not (isinstance(engine_cls, type) and issubclass(engine_cls, OcrEngine)):
            raise TypeError(
                f"register_engine_class 仅接受 OcrEngine 的子类，"
                f"收到: {engine_cls!r}"
            )
        engine_type = getattr(engine_cls, 'engine_type', None)
        if not isinstance(engine_type, str) or not engine_type:
            raise ValueError(
                f"{engine_cls.__name__}.engine_type 必须是非空字符串，"
                f"当前值: {engine_type!r}"
            )
        key = engine_type.lower()
        with self._lock:
            self._class_registry[key] = engine_cls

    def unregister_engine_class(self, engine_type: str) -> bool:
        """
        取消注册指定类型的 :class:`OcrEngine` 实现类。

        Args:
            engine_type: 引擎类型标识（大小写不敏感）。

        Returns:
            是否成功移除。
        """
        if not isinstance(engine_type, str) or not engine_type:
            return False
        key = engine_type.lower()
        with self._lock:
            if key in self._class_registry:
                del self._class_registry[key]
                return True
            return False

    def get_engine_class(self, engine_type: str) -> type[OcrEngine]:
        """
        获取指定引擎类型的 :class:`OcrEngine` 实现类。

        Args:
            engine_type: 引擎类型标识（大小写不敏感）。

        Returns:
            :class:`OcrEngine` 子类。

        Raises:
            ValueError: 该类型未注册时抛出。
        """
        if not isinstance(engine_type, str) or not engine_type:
            raise ValueError("engine_type 不能为空")
        key = engine_type.lower()
        with self._lock:
            engine_cls = self._class_registry.get(key)
            if engine_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 engine_type={engine_type!r} 对应的 OcrEngine 实现类，"
                    f"已注册的引擎: {registered}；请先通过 register_engine_class() 注册"
                )
            return engine_cls

    def get_registered_engine_types(self) -> list[str]:
        """
        获取所有已注册（即支持工厂化创建）的引擎类型列表。

        Returns:
            引擎类型标识列表。
        """
        with self._lock:
            return list(self._class_registry.keys())

    # endregion

    # region ======== 实例注册表（name -> OcrEngine 实例） ========

    def _resolve_name(self, name: str | None) -> str:
        """将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。"""
        return name if name else self.DEFAULT_NAME

    def register_engine(self, name: str, engine: OcrEngine | OcrCfg) -> None:
        """
        注册或替换指定名称的引擎实例。

        第二个参数支持两种形式：

          - :class:`OcrEngine` 实例：直接按名称归档（不依赖类注册表）；
          - :class:`OcrCfg` 配置：按 ``cfg.engine_type`` 从类注册表取出实现类，自动 ``cls(cfg)`` 工厂化创建实例后归档。
            此时要求对应实现类已通过 :meth:`register_engine_class` 注册。

        Args:
            name: 实例名称（别名，即引擎注册名）；为空时使用 :attr:`DEFAULT_NAME`。
            engine: :class:`OcrEngine` 实例，或 :class:`OcrCfg` 配置。

        Raises:
            ValueError: 传入 :class:`OcrCfg` 但 ``engine_type`` 为空、或对应类型
                未注册时抛出。
        """
        key = self._resolve_name(name)
        if isinstance(engine, OcrCfg):
            resolved = self._create_engine_from_cfg(engine)
        else:
            resolved = engine
        with self._lock:
            self._engine_registry[key] = resolved

    def unregister_engine(self, name: str | None = None) -> bool:
        """
        取消注册指定名称的引擎实例。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            是否成功移除。
        """
        key = self._resolve_name(name)
        with self._lock:
            if key in self._engine_registry:
                del self._engine_registry[key]
                return True
            return False

    def get_engine(self, name: str | None = None) -> OcrEngine:
        """
        获取指定名称的引擎实例。

        若按名称未找到且已设置 :attr:`_config_loader`，会先调用配置加载器
        （传入 manager 自身与请求的 name），再重新查找；仍未找到则抛出异常。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`OcrEngine` 实例。

        Raises:
            ValueError: 该名称尚未注册且配置加载器未能成功加载时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            engine = self._engine_registry.get(key)
            if engine is None and self._config_loader is not None:
                self._config_loader(self, key)
                engine = self._engine_registry.get(key)
            if engine is None:
                registered = ", ".join(self._engine_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到实例 '{key}'，已注册的实例: {registered}；"
                    f"请先通过 register_engine() 注册"
                )
            return engine

    def get_registered_engine_names(self) -> list[str]:
        """
        获取所有已注册的实例名称列表。

        Returns:
            实例名称列表。
        """
        with self._lock:
            return list(self._engine_registry.keys())

    # endregion

    # region ======== 识别便捷方法（透传 OcrEngine） ========

    def recognize(self, image, name: str | None = None) -> str:
        """
        识别图片中的文字，返回纯文本（透传 :meth:`OcrEngine.recognize`）。

        Args:
            image: 图片路径或 OpenCV 图像数组。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            识别出的文本内容，多行文本以换行符分隔。
        """
        return self.get_engine(name).recognize(image)

    def recognize_with_details(
        self, image, name: str | None = None
    ) -> list[OcrResult]:
        """
        识别图片中的文字，返回含位置与置信度的详细结果（透传 :meth:`OcrEngine.recognize_with_details`）。

        Args:
            image: 图片路径或 OpenCV 图像数组。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`OcrResult` 对象列表。
        """
        return self.get_engine(name).recognize_with_details(image)

    def recognize_and_draw(
        self,
        image,
        color: tuple[int, int, int] = (0, 255, 0),
        thickness: int | None = None,
        output_path: str | None = None,
        name: str | None = None,
    ):
        """
        识别图片中的文字，并在图片上绘制边界框与文本标签（透传 :meth:`OcrEngine.recognize_and_draw`）。

        Args:
            image: 图片路径或 OpenCV 图像数组。
            color: 边界框颜色，BGR 格式。
            thickness: 边界框线条粗细；为 ``None`` 时透传给 cv2、沿用其默认（1）。
            output_path: 结果保存路径；为 ``None`` 时不保存。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            绘制了边界框的图像数组。
        """
        return self.get_engine(name).recognize_and_draw(
            image, output_path=output_path, color=color, thickness=thickness
        )

    # endregion

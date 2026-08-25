"""
OCR 策略抽象基类。

:class:`OcrEngine` 绑定一份 :class:`OcrCfg` 配置，把"因引擎而异"的差异收敛为
可覆盖的钩子方法，把"放之四海皆准"的识别编排（图片加载 → 识别 → 结果清洗 → 绘制）
统一写在本类。新增一种引擎 = 继承本类并覆盖少数钩子，基类的识别流程无需改动。

设计上采用策略模式结合模板方法：图片加载（:meth:`OcrEngine._load_image`）与
结果清洗（:meth:`OcrEngine._filter_results`）均为本类内部钩子方法，可被子类按需
覆写；子类实现核心识别方法 :meth:`OcrEngine._recognize_array`，最大程度复用代码
并保证各引擎行为一致。

numpy / opencv 是 OCR 场景下不可避免的依赖（OpenCV 的 imread 返回 ndarray，
polylines / putText 也只接收 ndarray）。本模块不在顶部导入它们，仅做 ``TYPE_CHECKING``
类型提示（运行时零成本），运行期的实际导入下沉到 :meth:`OcrEngine._load_image` 与
:meth:`recognize_and_draw` 内部，保证 ``import pykunlun.ai.ocr`` 时不连带加载重依赖。
"""

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Union

from pykunlun.util import logutil

from .model import OcrCfg, OcrResult

if TYPE_CHECKING:
    import numpy as np

log = logutil.getLogger(__name__)


# region ======== 策略抽象基类 ========

class OcrEngine(ABC):
    """
    OCR 策略抽象基类（绑定一份 :class:`OcrCfg` 配置）。

    每个实例绑定一个 :class:`OcrCfg`，把"因引擎而异"的差异收敛为可覆盖的钩子方法，
    把"放之四海皆准"的识别编排放到本类。新增一种引擎 = 继承本类并覆盖少数钩子，
    基类的执行逻辑无需改动。

    方法分两层：

    【配置层】—— 构造时即校验
      - ``engine_type``            : 引擎**类型**标识（实现类硬编码的类级常量）；
                                      cfg.engine_type 省略时由本类推导，显式传入则校验一致性。
      - ``_validate_and_prepare_cfg``: 校验 cfg 必填字段并补全可推导默认值；
                                      默认校验 ``lang`` 非空。引擎特定的配置（如 ``server``
                                      的连接参数）不进 cfg，而由各实现类在自身构造参数中处理。
                                      由 ``__init__`` 自动调用，**构造即校验并补全**。

    【引擎差异钩子】—— 子类必须实现
      - ``_recognize_array``        : 对已加载的图像数组执行识别，返回
                                      :class:`OcrResult` 列表（子类唯一需实现的核心方法）。

    【通用识别接口】—— 基类实现，调用方直接使用
      - ``recognize``               : ``_load_image → _recognize_array → _filter_results → 纯文本``
      - ``recognize_with_details``  : 同上，返回 :class:`OcrResult` 列表
      - ``recognize_and_draw``      : 在图像上绘制边界框与文本标签并可选保存

    调用链示意::

        engine.recognize(image)
          └─ self._load_image(image)        # 字符串路径 / numpy 数组统一为 BGR ndarray（可覆写）
          └─ _recognize_array(img)          # 钩子（子类实现）
          └─ self._filter_results(results)  # 去空白、过滤空文本（可覆写）

    其中 :meth:`_recognize_array` 为必须实现的抽象方法；:meth:`_load_image` 与
    :meth:`_filter_results` 提供跨引擎通用的默认实现，子类可按需覆盖（如按置信度阈值
    过滤、从 URL 加载等），也可整体覆盖 :meth:`recognize` 等编排方法（例如某些
    HTTP 代理型引擎会覆盖 :meth:`recognize` / :meth:`recognize_with_details`，
    在路径/字节输入时跳过本地 opencv 加载）。

    Note:
        传入的 numpy 图像数组不会被修改——:meth:`_load_image` 在加载阶段会创建副本，
        因此 :meth:`recognize_and_draw` 的绘制操作不会影响调用方持有的原图。

    通常直接构造使用（构造时自动校验），也可注册到 :class:`OcrManager` 按名称管理::

        engine = SomeOcrEngine(cfg)
        text = engine.recognize("image.png")
    """

    # region ======== 构造与配置校验 ========

    def __init__(self, cfg: OcrCfg) -> None:
        """
        Args:
            cfg: 绑定的 OCR 配置对象。

        Raises:
            ValueError: 显式声明的 ``engine_type`` 与本实现类 :attr:`engine_type` 不一致、
                或 :meth:`_validate_and_prepare_cfg` 校验不通过时抛出。
        """
        self.cfg = cfg
        # engine_type：cfg 未声明（None）时由本实现类的 engine_type 推导；显式声明则校验
        # 一致性，不符即说明配置用错了实现类。
        if cfg.engine_type is None:
            cfg.engine_type = self.engine_type
        elif cfg.engine_type != self.engine_type:
            raise ValueError(
                f"引擎类型不匹配：配置 engine_type={cfg.engine_type!r}，"
                f"实现类 {type(self).__name__} 仅支持 {self.engine_type!r}"
            )
        # 构造即校验+补全：不同引擎的必填字段与默认值不同，交由各实现判定
        self._validate_and_prepare_cfg()

    def __setattr__(self, name: str, value: Any) -> None:
        """
        拦截实例属性赋值，保护 :attr:`engine_type` 与 :attr:`cfg` 不被运行时篡改。

        - ``engine_type``：基类虽把它声明为抽象只读 property，但子类为满足抽象约束会用类级常量
          ``engine_type = 'easy'`` 覆盖——该常量是普通字符串（非 data descriptor），会遮蔽基类 property，
          使 property 的只读保护失效，``instance.engine_type = x`` 将悄悄创建实例级遮蔽。
          本方法显式抛 :class:`AttributeError` 堵住此缺口。
        - ``cfg``：允许构造时首次赋值（由 :meth:`__init__` 触发），构造完成后禁止替换。
          绑定的 cfg 已经过 :meth:`_validate_and_prepare_cfg` 校验与默认值补全，
          运行期整体替换会绕过校验、破坏不变量；如需变更配置请重新构造实例。
          cfg 内部字段的逐个修改同样会绕过校验，应避免。
        其余属性（引擎句柄等）照常赋值。

        Raises:
            AttributeError: 尝试给实例的 ``engine_type`` 赋值，或构造完成后再次给 ``cfg`` 赋值时抛出。
        """
        if name == 'engine_type':
            raise AttributeError(
                f"{type(self).__name__}.engine_type 是实现类硬编码的类级常量，"
                f"代表本类所属的引擎类型，禁止运行时修改。"
            )
        if name == 'cfg' and 'cfg' in self.__dict__:
            raise AttributeError(
                f"{type(self).__name__}.cfg 在构造完成后不可替换（绑定配置已经校验），"
                f"如需变更配置请重新构造实例。"
            )
        super().__setattr__(name, value)

    def _validate_and_prepare_cfg(self) -> None:
        """
        校验并补全绑定的 :attr:`cfg`：必填字段缺失报错，可推导字段填默认。

        本默认实现按通用 OCR 场景处理：校验 ``lang`` 非空。

        引擎特定的配置（如 ``server`` 引擎的连接参数 server_url / timeout）**不放入通用
        :class:`OcrCfg`**，而由各实现类在自身构造参数中处理，故本方法无需关心；
        若某引擎需在 cfg 上补默认值，可在覆盖时**先填再调** ``super()``。

        本方法由 :meth:`__init__` 自动调用，确保构造出的实例配置一定有效且完整。

        Raises:
            ValueError: ``lang`` 为空时抛出。
        """
        if not self.cfg.lang:
            raise ValueError("OCR 配置缺少必填字段: lang")

    # endregion

    # region ======== 引擎标识（抽象） ========

    @property
    @abstractmethod
    def engine_type(self) -> str:
        """
        本实现类代表的引擎**类型**标识（如 ``rapid``、``easy``、``paddle``、``paddle3``、``server``）。

        由各实现类以**类级常量**形式硬编码提供，标识"本类是哪种引擎的策略"。
        基类声明为抽象只读 property，强制子类在类级覆盖；
        其运行时不可修改性由 :meth:`__setattr__` 显式拦截保证（详见该方法的说明）。

        注意：这是"类型"而非"名字"。同一类型可在 :class:`OcrManager` 中注册多份不同配置的
        实例，由 :meth:`OcrManager.register_engine` 的 ``name``（实例别名）区分。
        """
        pass

    # endregion

    # region ======== 引擎差异钩子 ========

    @abstractmethod
    def _recognize_array(self, image: 'np.ndarray') -> list[OcrResult]:
        """
        对已加载的图像数组执行 OCR 识别（子类唯一需实现的核心方法）。

        输入保证为经过 :meth:`_load_image` 校验的 OpenCV 图像数组（BGR），
        子类无需重复加载或校验文件，也不必关心空白文本的过滤——
        后者由基类经 :meth:`_filter_results` 统一处理。

        Args:
            image: OpenCV 图像数组（BGR 格式）。

        Returns:
            :class:`OcrResult` 对象列表，文本字段可为原始值（基类会统一清洗）。

        Raises:
            RuntimeError: 底层引擎识别失败时抛出。
        """

    # endregion

    # region ======== 图片加载与结果清洗（可覆写钩子） ========

    def _load_image(self, image: Union[str, 'np.ndarray']) -> 'np.ndarray':
        """
        将输入统一加载为 OpenCV 图像数组（**可覆写钩子**）。

        - 字符串路径：读取文件，校验存在性与可读性。
        - numpy 数组：返回副本，避免后续绘制修改调用方的原始图像。

        子类可覆写本方法以扩展输入形式（如 URL、字节流、PIL Image 等），
        或替换图像加载策略（如使用 PIL 而非 opencv）。覆写时仍应返回
        ``np.ndarray``，以保证下游 :meth:`_recognize_array` / :meth:`recognize_and_draw`
        的契约不变。

        Args:
            image: 图片路径或 OpenCV 图像数组。

        Returns:
            OpenCV 图像数组（BGR 格式）。

        Raises:
            FileNotFoundError: 图片路径不存在。
            ValueError: 无法读取图片文件（损坏或格式不支持）。
            TypeError: image 既不是字符串也不是 numpy 数组。
        """
        import cv2
        import numpy as np

        if isinstance(image, str):
            if not os.path.exists(image):
                raise FileNotFoundError(f"图片文件不存在: {image}")
            img = cv2.imread(image)
            if img is None:
                raise ValueError(
                    f"无法读取图片文件，请检查文件是否损坏或格式是否支持: {image}"
                )
            return img

        if isinstance(image, np.ndarray):
            return image.copy()

        raise TypeError(
            f"image 必须是图片路径(str)或 numpy 数组，实际类型: {type(image)}"
        )

    def _filter_results(self, results: list[OcrResult]) -> list[OcrResult]:
        """
        清洗识别结果：去除首尾空白，过滤空文本（**可覆写钩子**）。

        子类可覆写本方法以定制清洗策略（如按置信度阈值过滤、合并近邻框、
        修正特定字符等）。覆写时无需调用 ``super()``，但应保证返回值类型为
        ``list[OcrResult]``。

        Args:
            results: 引擎返回的原始识别结果列表。

        Returns:
            清洗后的 :class:`OcrResult` 列表，文本均为去除首尾空白的非空字符串。
        """
        cleaned: list[OcrResult] = []
        for r in results:
            text = r.text.strip() if r.text else ""
            if text:
                cleaned.append(
                    OcrResult(text=text, bbox=r.bbox, confidence=r.confidence)
                )
        return cleaned

    # endregion

    # region ======== 通用识别接口（模板方法） ========

    def recognize(self, image: Union[str, 'np.ndarray']) -> str:
        """
        识别图片中的文字，返回纯文本结果。

        Args:
            image: 图片路径或 OpenCV 图像数组。

        Returns:
            识别出的文本内容，多行文本以换行符 ``\\n`` 分隔，空白文本被过滤。

        Raises:
            FileNotFoundError: 图片路径不存在。
            ValueError: 无法读取图片文件。
            TypeError: image 类型不支持。
        """
        img = self._load_image(image)
        results = self._filter_results(self._recognize_array(img))
        return '\n'.join(r.text for r in results)

    def recognize_with_details(
        self, image: Union[str, 'np.ndarray']
    ) -> list[OcrResult]:
        """
        识别图片中的文字，返回包含位置与置信度的详细结果。

        Args:
            image: 图片路径或 OpenCV 图像数组。

        Returns:
            :class:`OcrResult` 对象列表，空白文本已被过滤。

        Raises:
            FileNotFoundError: 图片路径不存在。
            ValueError: 无法读取图片文件。
            TypeError: image 类型不支持。
        """
        img = self._load_image(image)
        return self._filter_results(self._recognize_array(img))

    def recognize_and_draw(
        self,
        image: Union[str, 'np.ndarray'],
        color: tuple[int, int, int] = (0, 255, 0),
        thickness: int | None = None,
        output_path: str | None = None,
    ) -> 'np.ndarray':
        """
        识别图片中的文字，并在图片上绘制边界框与文本标签。

        Args:
            image: 图片路径或 OpenCV 图像数组。传入数组时会创建副本，不修改原图。
            color: 边界框与文本颜色，BGR 格式。
            thickness: 边界框线条粗细；为 ``None`` 时透传给 cv2、沿用其默认（1）。
            output_path: 结果保存路径；为 ``None`` 时仅返回图像数组不保存。

        Returns:
            绘制了边界框与文本标签的图像数组（BGR 格式）。

        Raises:
            FileNotFoundError: 图片路径不存在。
            ValueError: 无法读取图片文件。
            TypeError: image 类型不支持。
        """
        import cv2
        import numpy as np

        img = self._load_image(image)
        for item in self._filter_results(self._recognize_array(img)):
            pts = np.array(item.bbox, np.int32).reshape((-1, 1, 2))
            cv2.polylines(img, [pts], isClosed=True, color=color, thickness=thickness)

            x, y = int(item.bbox[0][0]), int(item.bbox[0][1]) - 10
            cv2.putText(
                img,
                item.text,
                (x, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
            )

        if output_path:
            cv2.imwrite(output_path, img)

        return img

    # endregion


# endregion

"""
RapidOCR 策略实现模块（轻量本地默认实现）。

基于 ``rapidocr`` v3.x（ONNX Runtime 后端 + PP-OCRv6 small 模型），
提供 ``pip install pykunlun[rapidocr]`` 即装即用的本地 OCR 能力，
无需 PaddlePaddle / PyTorch / CUDA 等重依赖——是 pykunlun 的**默认 OCR 实现**。

定位对比（pykunlun 默认实现 vs pybaibao 重型实现）：

  - 本模块 :class:`RapidOcr` —— 轻量默认（约 30MB 包体，CPU 推理，中英文够用）
  - ``pybaibao.ai.ocr.PaddleOcr`` —— 精度首选（paddlepaddle + 大模型，准确率顶尖）
  - ``pybaibao.ai.ocr.EasyOcr``    —— torch 系生态（pytorch，多语言丰富）
  - ``pybaibao.ai.ocr.ServerOcr``  —— 服务端代理（无本地依赖，需远程服务）

引擎类型（engine_type）：``rapid``。仅实现 :meth:`OcrEngine._recognize_array` 钩子，
图片加载、结果清洗、识别编排放由基类 :class:`OcrEngine` 通用流程统一处理。

延迟加载：本模块顶部**不导入 rapidocr / onnxruntime**，仅在 :meth:`RapidOcr.__init__`
内按需导入，保证 ``import pykunlun.ai.ocr`` 时不连带加载重依赖。未安装 rapidocr /
onnxruntime 时，首次实例化会通过 ``pykunlun.system.pip`` **自动安装**（镜像顺序同
``DEFAULT_MIRRORS``，与 baibao 的 EasyOcr / PaddleOcr 同策略），自动安装失败才抛
``ImportError``。
"""

from typing import TYPE_CHECKING, Any

from pykunlun.util import logutil

from .engine import OcrEngine
from .model import OcrCfg, OcrResult

if TYPE_CHECKING:
    import numpy.typing as npt

log = logutil.getLogger(__name__)


class RapidOcr(OcrEngine):
    """
    基于 ``rapidocr`` v3.x 的轻量本地 OCR 实现（**pykunlun 默认**）。

    默认走 ONNX Runtime CPU 后端 + PP-OCRv6 small 模型（中英文），
    ``pip install pykunlun[rapidocr]`` 即装即用，无需 GPU / Torch / Paddle。

    与其他实现的定位差异见模块 docstring。

    特性:
        - 中英文识别（默认；其他语言需自定义模型，详见 rapidocr 文档）
        - CPU 友好（ONNX Runtime 后端，无需 CUDA）
        - 包体小（约 30 MB，含模型）
        - 与 :class:`OcrEngine` 模板方法编排无缝集成

    OcrCfg 字段映射:
        - ``use_angle_cls`` —— 映射为每次识别时 ``use_cls`` 开关；关闭可加速
          （跳过方向分类模型推理）。
        - ``lang`` —— RapidOCR 默认就是中英文模型，本字段在 RapidOcr 中仅作配置标识，
          不改变实际识别模型（多语言需通过 ``rapidocr`` 自身的 ``params`` 配置自定义模型）。
        - ``gpu`` / ``cpu_threads`` —— 当前实现忽略，永远 CPU（PP-OCRv6 small 在 CPU
          上已足够快）。未来如需 GPU 支持，可在覆盖 :meth:`__init__` 时通过
          ``RapidOCR(params={...})`` 切换 engine_type。

    示例::

        from pykunlun.ai.ocr import RapidOcr, OcrCfg

        ocr = RapidOcr()                              # 默认（中英文 + 角度分类）
        text = ocr.recognize("image.png")

        ocr = RapidOcr(OcrCfg(use_angle_cls=False))   # 关闭方向分类（更快）
        results = ocr.recognize_with_details("image.png")

    Raises:
        ImportError: rapidocr / onnxruntime 未安装且**自动安装失败**（如断网、镜像不可达）。
            可手动 ``pip install pykunlun[rapidocr]`` 或 ``pip install rapidocr onnxruntime``。
    """

    engine_type = 'rapid'

    # region ======== 构造 ========

    def __init__(self, cfg: OcrCfg | None = None) -> None:
        """
        Args:
            cfg: OCR 配置。``None`` 时用默认配置（engine_type 自动推导为 ``rapid``）。
                传入时 ``engine_type`` 字段省略或为 ``'rapid'`` 均可。
        """
        if cfg is None:
            cfg = OcrCfg(engine_type='rapid')
        super().__init__(cfg)

        try:
            import onnxruntime  # noqa: F401  默认推理后端；缺失时一并触发自动安装
            from rapidocr import RapidOCR
        except ImportError as e:
            # 缺依赖自动安装（与 baibao EasyOcr / PaddleOcr 同策略），
            # 走 kunlun pip 工具的镜像顺序（DEFAULT_MIRRORS）；已装的包 pip 会跳过。
            from pykunlun.system import pip as kl_pip

            _ok, _fail = kl_pip.install(['rapidocr', 'onnxruntime'])
            if _fail:
                raise ImportError(
                    f"rapidocr / onnxruntime 未安装，自动安装失败: {_fail}\n"
                    "请手动运行: pip install rapidocr onnxruntime\n"
                    "或: pip install pykunlun[rapidocr]"
                ) from e
            from rapidocr import RapidOCR

        # 默认配置：PP-OCRv6 small + onnxruntime CPU + 中英文（rapidocr>=3.9.0）。
        # 包体内置模型，无需联网下载。
        self._engine = RapidOCR()
        self._use_cls: bool = cfg.use_angle_cls

    # endregion

    # region ======== OcrEngine 实现 ========

    def _recognize_array(self, image: 'npt.NDArray[Any]') -> list[OcrResult]:
        """
        调用 RapidOCR 识别图像数组。

        RapidOCR v3.x 默认模式（检测 + 方向分类 + 识别）返回 ``RapidOCROutput``
        dataclass，关键字段：

          - ``boxes``  : shape ``(N, 4, 2)``，N 行文本的四点框
          - ``txts``   : ``Tuple[str]``，每行识别文本
          - ``scores`` : ``Tuple[float]``，每行识别置信度

        Args:
            image: OpenCV 图像数组（BGR 格式，由基类 :meth:`_load_image` 提供）。

        Returns:
            :class:`OcrResult` 对象列表。空白过滤由基类 :meth:`_filter_results` 统一处理。
        """
        result = self._engine(image, use_cls=self._use_cls)

        boxes = getattr(result, 'boxes', None)
        txts = getattr(result, 'txts', None) or ()
        scores = getattr(result, 'scores', None) or ()

        if boxes is None or len(txts) == 0:
            return []

        out: list[OcrResult] = []
        for i, text in enumerate(txts):
            confidence = float(scores[i]) if i < len(scores) else 0.0
            if i < len(boxes):
                bbox = [(int(p[0]), int(p[1])) for p in boxes[i]]
            else:
                bbox = []
            out.append(OcrResult(text=text or '', bbox=bbox, confidence=confidence))
        return out

    # endregion

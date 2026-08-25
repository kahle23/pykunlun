"""
OCR 数据模型（POJO 集合）。

集中承载 OCR 子包中"纯数据容器"性质的两个类：

  - :class:`OcrCfg`    —— 识别配置（**输入**契约，调用方填）
  - :class:`OcrResult` —— 识别结果（**输出**契约，引擎产）

两者均为 ``@dataclass``，自身不做任何校验；各引擎的校验与默认值补全交由
:meth:`pykunlun.ai.ocr.engine.OcrEngine._validate_and_prepare_cfg` 在构造引擎实例时自动完成。

集中成单一数据模型模块便于按需 import，并避免与
:mod:`pykunlun.ai.ocr.engine` 形成循环依赖。
"""

from dataclasses import dataclass


@dataclass
class OcrCfg:
    """
    OCR 识别配置。

    封装各 OCR 引擎通用的识别参数。所有字段均可选（默认值表示未设置或通用默认），
    引擎特定的校验与默认值补全交由各 :class:`OcrEngine` 实现的
    :meth:`~OcrEngine._validate_and_prepare_cfg` 在构造引擎时完成。

    只收敛"所有引擎都可能用到"的通用字段；引擎专属配置（如 ``server`` 引擎的服务端
    地址 / 超时）不放入本类，而由各实现类在自身构造参数中表达，避免通用配置被
    特定引擎的字段污染。

    Attributes:
        engine_type: 引擎**类型**标识，如 ``rapid`` / ``easy`` / ``paddle`` / ``paddle2`` / ``paddle3`` / ``server``；
            省（``None``）时由所构造的实现类 :attr:`~OcrEngine.engine_type` 推导，显式传入则校验一致性。
            :class:`OcrManager` 按 ``cfg.engine_type`` 从类注册表工厂化创建引擎实例。
            注意：这是"类型"而非"名字"——同类型可注册多份不同配置的实例，由
            :meth:`OcrManager.register_engine` 的 ``name`` 参数区分（实例别名）。
        lang: 语言代码。PaddleOCR / RapidOCR 生态对"简体中文（含英文）"的标准码是
            ``'ch'``（**非** ISO 639-1 的 ``'zh'``——``'zh'`` 是 i18n 等其他生态的用法；
            Tesseract 用 ``'chi_sim'``、EasyOCR 用 ``'ch_sim'``），常用值：``'ch'``（中英）、
            ``'en'``（英文）、``'japan'``、``'korean'``、``'chinese_cht'``（繁体）。
            各引擎映射不同（EasyOCR 会把 ``'ch'`` 映射为 ``['ch_sim', 'en']`` 等），由各实现自行转换。
        gpu: 是否启用 GPU。EasyOCR 映射为 ``gpu``；PaddleOCR 3.x 映射为 ``device='gpu:0'``。
            需安装对应 GPU 版（easyocr 需 CUDA 版 torch；paddle 需 ``paddlepaddle-gpu``）。
        cpu_threads: CPU 推理线程数（仅 PaddleOCR 生效；EasyOCR 走 torch 默认线程策略）。
        use_angle_cls: 是否启用**方向/角度分类**。详见字段定义处的内联注释。

    校验策略：OcrCfg 为纯数据容器，自身不做任何校验；
    各引擎的校验与默认值补全（必填字段差异）交由
    :meth:`OcrEngine._validate_and_prepare_cfg` 在构造引擎实例时自动完成。
    """

    engine_type: str | None = None
    lang: str = 'ch'
    gpu: bool = False
    cpu_threads: int | None = None
    # 方向/角度分类（det→cls→rec 流水线的 cls 环节）：检测每个文本行是否 180° 颠倒，
    # 颠倒的行先旋转矫正再送识别，提升倒置文本的准确率；关闭可省一次模型推理、加快速度，
    # 但颠倒/倾斜文本的精度会下降。各引擎参数名：RapidOCR ``use_cls``、
    # PaddleOCR 2.x ``use_angle_cls``、PaddleOCR 3.x ``use_textline_orientation``、
    # EasyOCR 无直接对应（其文本行方向由 rec 模型自带，忽略本字段）。
    use_angle_cls: bool = True


@dataclass
class OcrResult:
    """
    OCR 识别结果。

    Attributes:
        text: 识别出的文字内容。
        bbox: 四边形边界框坐标，格式 ``[(x1, y1), (x2, y2), (x3, y3), (x4, y4)]``。
        confidence: 识别置信度，取值范围 0~1。
    """

    text: str
    bbox: list[tuple[int, int]]
    confidence: float

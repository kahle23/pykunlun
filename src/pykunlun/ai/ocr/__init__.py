"""
OCR 策略抽象与管理子包。

提供引擎无关配置（:class:`OcrCfg`）、识别结果（:class:`OcrResult`）、策略抽象基类
（:class:`OcrEngine`）、引擎管理器（:class:`OcrManager`），以及基于 ``rapidocr`` 的
**轻量本地默认实现** :class:`RapidOcr`（``pip install pykunlun[rapidocr]`` 即用）。
其他重型引擎实现（EasyOCR / PaddleOCR / ServerOcr 等）由 :mod:`baibao.ai.ocr` 提供。

引擎实例统一由 :class:`OcrManager` 管理：按 ``engine_type`` 工厂化创建（类注册表）、
按 ``name`` 索引实例（实例注册表），并提供 ``recognize*`` 便捷方法。

模块组织：

  - :mod:`pykunlun.ai.ocr.model`            — 数据模型 :class:`OcrCfg` / :class:`OcrResult`
  - :mod:`pykunlun.ai.ocr.engine`           — 策略抽象基类 :class:`OcrEngine`
  - :mod:`pykunlun.ai.ocr.manager`          — 引擎管理器 :class:`OcrManager`
  - :mod:`pykunlun.ai.ocr.rapidocr_engine`  — 轻量本地默认实现 :class:`RapidOcr`
"""

from .engine import OcrEngine
from .manager import OcrManager
from .model import OcrCfg, OcrResult
from .rapidocr_engine import RapidOcr

__all__ = [
    'OcrCfg',
    'OcrEngine',
    'OcrManager',
    'OcrResult',
    'RapidOcr',
]

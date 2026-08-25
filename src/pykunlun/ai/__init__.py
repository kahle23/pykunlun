"""
AI 相关抽象模块。

提供人工智能相关能力的通用抽象，按子包组织：

  - ocr: 光学字符识别（OCR）的策略抽象、引擎管理器与轻量本地默认实现 :class:`RapidOcr`

后续可扩展 LLM 等子包。
"""

from .ocr import OcrCfg, OcrEngine, OcrManager, OcrResult, RapidOcr

__all__ = [
    'OcrCfg',
    'OcrEngine',
    'OcrManager',
    'OcrResult',
    'RapidOcr',
]

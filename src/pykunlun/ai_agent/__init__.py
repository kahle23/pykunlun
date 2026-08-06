"""
AI Agent 能力模块。

承载自治 agent 的组成件（记忆、技能管理等），按子模块组织。与 :mod:`baibao.ai`
（模型推理能力：llm / ocr）区分：本包聚焦 agent 机制本身。

当前包含：
  - 记忆（:mod:`pykunlun.ai_agent.memory` 抽象 + :mod:`pykunlun.ai_agent.memory_builtin` 内置实现）

后续可扩展技能管理等。
"""

from .memory import (
    UPDATABLE_FIELDS,
    VALID_CATEGORIES,
    VALID_SOURCES,
    MemoryManager,
    MemoryRecord,
    MemoryStore,
    permission_clause,
    tokenize_query,
    visibility_clause,
)
from .memory_builtin import SqliteMemoryStore

__all__ = [
    'UPDATABLE_FIELDS',
    'VALID_CATEGORIES',
    'VALID_SOURCES',
    'MemoryManager',
    'MemoryRecord',
    'MemoryStore',
    'SqliteMemoryStore',
    'permission_clause',
    'tokenize_query',
    'visibility_clause',
]

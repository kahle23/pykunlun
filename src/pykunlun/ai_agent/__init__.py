"""
AI Agent 能力模块。

承载自治 agent 的组成件（记忆、长任务、技能管理等），按子模块组织。与 :mod:`baibao.ai`
（模型推理能力：llm / ocr）区分：本包聚焦 agent 机制本身。

当前包含：
  - 记忆（:mod:`pykunlun.ai_agent.memory` 抽象 + :mod:`pykunlun.ai_agent.memory_builtin` 内置实现）
  - 长任务（:mod:`pykunlun.ai_agent.long_task` 抽象 + SQLite 内置实现，包内按
    state / model / service / manager / sqlite_service 分模块组织）

后续可扩展技能管理等。
"""

from .long_task import (
    RUN_STATUSES,
    STEP_STATUSES,
    STEP_TRANSITIONS,
    TASK_STATUSES,
    TASK_TRANSITIONS,
    UPDATABLE_STEP_FIELDS,
    UPDATABLE_TASK_FIELDS,
    VALID_ART_TYPES,
    VALID_EVENT_LEVELS,
    VALID_EVENT_TYPES,
    VALID_STEP_TYPES,
    AgentRun,
    LongTaskManager,
    LongTaskService,
    SqliteLongTaskService,
    TaskArtifact,
    TaskEvent,
    TaskInstance,
    TaskStep,
    TaskTemplate,
    assert_step_transition,
    assert_task_transition,
    step_disposition_on_fail,
)
from .memory import (
    PATH_LIKE_CATEGORIES,
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
    'PATH_LIKE_CATEGORIES',
    'RUN_STATUSES',
    'STEP_STATUSES',
    'STEP_TRANSITIONS',
    'TASK_STATUSES',
    'TASK_TRANSITIONS',
    'UPDATABLE_FIELDS',
    'UPDATABLE_STEP_FIELDS',
    'UPDATABLE_TASK_FIELDS',
    'VALID_ART_TYPES',
    'VALID_CATEGORIES',
    'VALID_EVENT_LEVELS',
    'VALID_EVENT_TYPES',
    'VALID_SOURCES',
    'VALID_STEP_TYPES',
    'AgentRun',
    'LongTaskManager',
    'LongTaskService',
    'MemoryManager',
    'MemoryRecord',
    'MemoryStore',
    'SqliteLongTaskService',
    'SqliteMemoryStore',
    'TaskArtifact',
    'TaskEvent',
    'TaskInstance',
    'TaskStep',
    'TaskTemplate',
    'assert_step_transition',
    'assert_task_transition',
    'permission_clause',
    'step_disposition_on_fail',
    'tokenize_query',
    'visibility_clause',
]

"""
AI Agent 长任务能力子包。

让 AI 以「断点可续、重试有据、全程留痕」的方式执行超出单会话承载能力的长任务。
形态 = 技能（SKILL.md）+ Python CLI + 数据库表；**数据库是唯一真相源**，AI 会话
只是可随时替换的执行单元——任何新会话（或 cron 唤醒）凭库里的状态无缝接手。

计划与尝试分离：step 是"计划中要做的事"，run 是"实际的一次尝试"；失败重试 =
同一 step 新增一条 run，不污染 step 语义。每次状态流转由实现自动追加一条
``ai_task_event``（append-only 留痕），不暴露给调用方。

模块组织（对标 :mod:`pykunlun.ai.ocr` 的包拆分）：

  - :mod:`pykunlun.ai_agent.long_task.state`           — 状态机常量与裁决纯函数
  - :mod:`pykunlun.ai_agent.long_task.model`           — 数据模型（六个 POJO 数据类）
  - :mod:`pykunlun.ai_agent.long_task.service`         — 服务策略抽象基类 :class:`LongTaskService`
  - :mod:`pykunlun.ai_agent.long_task.manager`         — 服务管理器 :class:`LongTaskManager`
  - :mod:`pykunlun.ai_agent.long_task.sqlite_service`  — 轻量本地默认实现 :class:`SqliteLongTaskService`

具体后端实现（如基于 rdb_mgr 的 ``MySqlLongTaskService``）由上层包提供。
"""

from .manager import LongTaskManager
from .model import AgentRun, TaskArtifact, TaskEvent, TaskInstance, TaskStep, TaskTemplate
from .service import LongTaskService
from .sqlite_service import SqliteLongTaskService
from .state import (
    RUN_STATUSES,
    STEP_STATUSES,
    STEP_TRANSITIONS,
    TASK_STATUSES,
    TASK_TRANSITIONS,
    UPDATABLE_TASK_FIELDS,
    VALID_ART_TYPES,
    VALID_EVENT_LEVELS,
    VALID_EVENT_TYPES,
    VALID_STEP_TYPES,
    assert_step_transition,
    assert_task_transition,
    step_disposition_on_fail,
)

__all__ = [
    'RUN_STATUSES',
    'STEP_STATUSES',
    'STEP_TRANSITIONS',
    'TASK_STATUSES',
    'TASK_TRANSITIONS',
    'UPDATABLE_TASK_FIELDS',
    'VALID_ART_TYPES',
    'VALID_EVENT_LEVELS',
    'VALID_EVENT_TYPES',
    'VALID_STEP_TYPES',
    'AgentRun',
    'LongTaskManager',
    'LongTaskService',
    'SqliteLongTaskService',
    'TaskArtifact',
    'TaskEvent',
    'TaskInstance',
    'TaskStep',
    'TaskTemplate',
    'assert_step_transition',
    'assert_task_transition',
    'step_disposition_on_fail',
]

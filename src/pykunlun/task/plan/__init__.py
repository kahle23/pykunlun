"""
计划任务子包：通用的多步骤任务管理。

任何使用者（不限于 AI 会话）都可基于本包管理「断点可续、重试有据、全程留痕」的
计划任务：把任务拆成有序步骤，认领执行、失败重试、中断后凭库里的状态无缝接手。
**数据库是唯一真相源**，执行方（AI 会话、脚本、cron 唤醒）只是可随时替换的执行
单元。形态 = 库 API + 数据库表，上层可再包 CLI / 技能文档等门面。

计划与尝试分离：step 是"计划中要做的事"，run 是"实际的一次尝试"；失败重试 =
同一 step 新增一条 run，不污染 step 语义。每次状态流转由实现自动追加一条事件
记录（append-only 留痕），不暴露给调用方。

模块组织（对标 :mod:`pykunlun.ai.ocr` 的包拆分）：

  - :mod:`pykunlun.task.plan.state`           — 状态机常量与裁决纯函数
  - :mod:`pykunlun.task.plan.model`           — 数据模型（六个 POJO 数据类）
  - :mod:`pykunlun.task.plan.service`         — 服务策略抽象基类 :class:`PlanTaskService`
  - :mod:`pykunlun.task.plan.manager`         — 服务管理器 :class:`PlanTaskManager`
  - :mod:`pykunlun.task.plan.sqlite_service`  — 轻量本地默认实现 :class:`SqlitePlanTaskService`

具体后端实现（如基于 rdb_mgr 的 ``MySqlPlanTaskService``）由上层包提供。
"""

from .manager import PlanTaskManager
from .model import TaskArtifact, TaskEvent, TaskInstance, TaskRun, TaskStep, TaskTemplate
from .service import PlanTaskService
from .sqlite_service import SqlitePlanTaskService
from .state import (
    DEP_SATISFIED_STATUSES,
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
    deps_satisfied,
    parse_depends_on,
    step_disposition_on_fail,
)

__all__ = [
    "DEP_SATISFIED_STATUSES",
    "RUN_STATUSES",
    "STEP_STATUSES",
    "STEP_TRANSITIONS",
    "TASK_STATUSES",
    "TASK_TRANSITIONS",
    "UPDATABLE_TASK_FIELDS",
    "VALID_ART_TYPES",
    "VALID_EVENT_LEVELS",
    "VALID_EVENT_TYPES",
    "VALID_STEP_TYPES",
    "PlanTaskManager",
    "PlanTaskService",
    "SqlitePlanTaskService",
    "TaskArtifact",
    "TaskEvent",
    "TaskInstance",
    "TaskRun",
    "TaskStep",
    "TaskTemplate",
    "assert_step_transition",
    "assert_task_transition",
    "deps_satisfied",
    "parse_depends_on",
    "step_disposition_on_fail",
]

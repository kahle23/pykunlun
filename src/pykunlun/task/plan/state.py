"""
计划任务状态机：合法状态集、流转表与裁决纯函数。

规则只有一份（本模块），各后端实现复用——对标 :mod:`pykunlun.ai_agent.memory`
把 ``visibility_clause`` 放抽象层的做法。全部为纯函数/常量，无任何 I/O 依赖。

两条**绕过自动流转表**的管理动作（不在 :data:`TASK_TRANSITIONS` /
:data:`STEP_TRANSITIONS` 内表达，由实现层 :meth:`PlanTaskService.retry_step`
与 :meth:`PlanTaskService.cancel` 显式处理）：

  - ``step failed → pending``：手动 retry_step（预算 +1 后复活）；
  - ``task failed → running``：retry_step 复活因该步骤失败的任务。

0.0.5 起新增的管理动作（同样绕过自动流转表，由实现层显式处理）：

  - ``step running → pending``（**不加** retry_count）：release_run 释放认领，
    把未完成的步骤还回队列（区别于 fail_run 的一次失败、消耗预算）；
  - ``step succeeded → pending/running``：verify_task --fix / retry_step(force=True)
    对假成功（绕过状态机直接改库）的就地修复；``task completed → running`` 同理；
  - 步骤可声明 ``depends_on``（依赖更早 seq 列表）：claim 依赖感知模式的就绪判定
    见 :func:`deps_satisfied`。
"""

import json
from typing import Any

#: 任务实例的合法状态集
TASK_STATUSES: frozenset[str] = frozenset(
    {
        "pending",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }
)

#: 任务步骤的合法状态集
STEP_STATUSES: frozenset[str] = frozenset({"pending", "running", "succeeded", "failed", "skipped"})

#: 执行记录（尝试）的合法状态集
RUN_STATUSES: frozenset[str] = frozenset({"running", "succeeded", "failed", "timeout", "cancelled"})

#: 任务状态合法流转表（值为可流入的目标状态集；空集 = 终态）
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancelled"}),
    "running": frozenset({"paused", "completed", "failed", "cancelled"}),
    "paused": frozenset({"running", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

#: 步骤状态合法流转表。``running → pending`` = 失败但重试预算未耗尽（自动流转）；
#: ``failed → pending``（手动 retry_step 复活）不走本表，见 :meth:`PlanTaskService.retry_step`。
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "skipped"}),
    "running": frozenset({"succeeded", "failed", "pending"}),
    "succeeded": frozenset(),
    "failed": frozenset(),
    "skipped": frozenset(),
}

#: update_task() 允许修改的字段白名单（除 id/状态/心跳/时间戳外的业务字段）
UPDATABLE_TASK_FIELDS: frozenset[str] = frozenset(
    {
        "title",
        "goal",
        "params",
        "max_retries",
        "heartbeat_timeout_sec",
        "timeout_sec",
    }
)

#: 合法的 step_type 取值
VALID_STEP_TYPES: frozenset[str] = frozenset({"agent", "bash", "human_approval", "condition"})

#: 合法的产物类型取值
VALID_ART_TYPES: frozenset[str] = frozenset({"file", "report", "diff", "log", "other"})

#: 合法的事件类型取值
VALID_EVENT_TYPES: frozenset[str] = frozenset({"state_change", "error", "checkpoint", "note", "artifact"})

#: 合法的事件级别取值
VALID_EVENT_LEVELS: frozenset[str] = frozenset({"info", "warn", "error"})


def assert_task_transition(old: str, new: str) -> None:
    """
    校验任务状态流转是否合法，非法抛 :class:`ValueError`。

    Args:
        old: 流转前状态（须在 :data:`TASK_STATUSES` 内）。
        new: 流转目标状态。

    Raises:
        ValueError: ``old`` 未知，或 ``old → new`` 不在 :data:`TASK_TRANSITIONS` 内。
    """
    if old not in TASK_TRANSITIONS:
        raise ValueError(f"未知的任务状态: {old!r}（合法值: {sorted(TASK_STATUSES)}）")
    if new not in TASK_TRANSITIONS[old]:
        legal = sorted(TASK_TRANSITIONS[old])
        raise ValueError(f"非法的任务状态流转: {old} → {new}（{old} 的合法目标: {legal}）")


def assert_step_transition(old: str, new: str) -> None:
    """
    校验步骤状态流转是否合法，非法抛 :class:`ValueError`。

    Args:
        old: 流转前状态（须在 :data:`STEP_STATUSES` 内）。
        new: 流转目标状态。

    Raises:
        ValueError: ``old`` 未知，或 ``old → new`` 不在 :data:`STEP_TRANSITIONS` 内。
    """
    if old not in STEP_TRANSITIONS:
        raise ValueError(f"未知的步骤状态: {old!r}（合法值: {sorted(STEP_STATUSES)}）")
    if new not in STEP_TRANSITIONS[old]:
        legal = sorted(STEP_TRANSITIONS[old])
        raise ValueError(f"非法的步骤状态流转: {old} → {new}（{old} 的合法目标: {legal}）")


def step_disposition_on_fail(retry_count: int, max_retries: int) -> str:
    """
    步骤失败后的去向裁决（纯函数，实现层在 fail_run / sweep 内复用）。

    重试预算语义：``max_retries`` 为最大重试次数，含首次共 ``max_retries + 1`` 次机会；
    ``retry_count`` 为已重试次数。

    Args:
        retry_count: 已重试次数。
        max_retries: 最大重试次数。

    Returns:
        ``'pending'``（预算未耗尽，回 pending 待重试）或 ``'failed'``（预算耗尽，终败）。
    """
    return "pending" if retry_count < max_retries else "failed"


#: 依赖就绪判定中视为"已完成"的步骤状态集合。含 ``skipped``：跳过是有意决策
#: （如功能放弃），不应永久阻塞下游步骤。
DEP_SATISFIED_STATUSES: frozenset[str] = frozenset({"succeeded", "skipped"})


def parse_depends_on(value: Any) -> list[int]:
    """
    解析步骤 ``depends_on`` 列值为依赖 seq 列表（纯函数，读侧容错）。

    Args:
        value: 列原值——JSON 数组字符串（``"[3,7]"``）/ 已解析的 list / None / 空串。

    Returns:
        依赖 seq 列表（int 升序去重）；值为空返回 ``[]``；非法元素（非 int）丢弃。
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(value, list):
        return []
    return sorted({v for v in value if isinstance(v, int) and not isinstance(v, bool)})


def deps_satisfied(dep_seqs: list[int], status_by_seq: dict[int, str]) -> bool:
    """
    依赖就绪判定（纯函数，claim 依赖感知模式复用）。

    全部依赖 seq 的当前状态都在 :data:`DEP_SATISFIED_STATUSES` 内即就绪
    （依赖列表为空视为就绪）；依赖引用了不存在的 seq 视为**未就绪**（脏数据不误放行）。

    Args:
        dep_seqs: 依赖 seq 列表（经 :func:`parse_depends_on` 解析）。
        status_by_seq: 同任务全部步骤的 ``{seq: status}`` 映射。

    Returns:
        是否就绪。
    """
    for seq in dep_seqs:
        if status_by_seq.get(seq) not in DEP_SATISFIED_STATUSES:
            return False
    return True

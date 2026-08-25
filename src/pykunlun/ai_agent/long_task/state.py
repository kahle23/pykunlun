"""
长任务状态机：合法状态集、流转表与裁决纯函数。

规则只有一份（本模块），各后端实现复用——对标 :mod:`pykunlun.ai_agent.memory`
把 ``visibility_clause`` 放抽象层的做法。全部为纯函数/常量，无任何 I/O 依赖。

两条**绕过自动流转表**的管理动作（不在 :data:`TASK_TRANSITIONS` /
:data:`STEP_TRANSITIONS` 内表达，由实现层 :meth:`LongTaskService.retry_step`
与 :meth:`LongTaskService.cancel` 显式处理）：

  - ``step failed → pending``：手动 retry_step（预算 +1 后复活）；
  - ``task failed → running``：retry_step 复活因该步骤失败的任务。
"""

#: 任务实例的合法状态集
TASK_STATUSES: frozenset[str] = frozenset({
    'pending', 'running', 'paused', 'completed', 'failed', 'cancelled',
})

#: 任务步骤的合法状态集
STEP_STATUSES: frozenset[str] = frozenset({'pending', 'running', 'succeeded', 'failed', 'skipped'})

#: 执行记录（尝试）的合法状态集
RUN_STATUSES: frozenset[str] = frozenset({'running', 'succeeded', 'failed', 'timeout', 'cancelled'})

#: 任务状态合法流转表（值为可流入的目标状态集；空集 = 终态）
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    'pending':   frozenset({'running', 'cancelled'}),
    'running':   frozenset({'paused', 'completed', 'failed', 'cancelled'}),
    'paused':    frozenset({'running', 'cancelled'}),
    'completed': frozenset(),
    'failed':    frozenset(),
    'cancelled': frozenset(),
}

#: 步骤状态合法流转表。``running → pending`` = 失败但重试预算未耗尽（自动流转）；
#: ``failed → pending``（手动 retry_step 复活）不走本表，见 :meth:`LongTaskService.retry_step`。
STEP_TRANSITIONS: dict[str, frozenset[str]] = {
    'pending':   frozenset({'running', 'skipped'}),
    'running':   frozenset({'succeeded', 'failed', 'pending'}),
    'succeeded': frozenset(),
    'failed':    frozenset(),
    'skipped':   frozenset(),
}

#: update_task() 允许修改的字段白名单（除 id/状态/心跳/时间戳外的业务字段）
UPDATABLE_TASK_FIELDS: frozenset[str] = frozenset({
    'title', 'goal', 'params', 'max_retries', 'heartbeat_timeout_sec', 'timeout_sec',
})

#: update_step（经 result_summary 回填等场景）允许修改的字段白名单
UPDATABLE_STEP_FIELDS: frozenset[str] = frozenset({
    'name', 'instruction', 'timeout_sec', 'max_retries', 'result_summary',
})

#: 合法的 step_type 取值
VALID_STEP_TYPES: frozenset[str] = frozenset({'agent', 'bash', 'human_approval', 'condition'})

#: 合法的产物类型取值
VALID_ART_TYPES: frozenset[str] = frozenset({'file', 'report', 'diff', 'log', 'other'})

#: 合法的事件类型取值
VALID_EVENT_TYPES: frozenset[str] = frozenset({'state_change', 'error', 'checkpoint', 'note', 'artifact'})

#: 合法的事件级别取值
VALID_EVENT_LEVELS: frozenset[str] = frozenset({'info', 'warn', 'error'})


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
    return 'pending' if retry_count < max_retries else 'failed'

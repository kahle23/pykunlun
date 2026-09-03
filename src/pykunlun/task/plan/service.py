"""
计划任务服务策略抽象基类。

:class:`PlanTaskService` 定义跨后端（sqlite / mysql / 未来 http）的统一接口，
对标 :class:`pykunlun.ai_agent.memory.MemoryStore` 的角色，但命名为 **Service**
而非 Store：接口方法不是纯存储操作——claim_next_step / finish_run / fail_run /
sweep 是带事务语义与事件留痕的**业务编排操作**（这正是设计文档"抽象层方法必须
是业务级粗粒度"的体现）；且下期整体切换为 HTTP 后端时
``HttpPlanTaskService(PlanTaskService)`` 名副其实，"Store"则名不副实。
"""

from abc import ABC, abstractmethod
from typing import Any

from .model import TaskInstance, TaskRun, TaskStep, TaskTemplate


class PlanTaskService(ABC):
    """
    计划任务服务策略抽象基类。

    各后端实现（sqlite / mysql / 未来 http）继承本类，对外提供**业务级粗粒度**
    的统一操作。方法刻意保持粗粒度（如 :meth:`claim_next_step` 是一个方法而非
    "查一步 + 改一步 + 插一条 run" 三个方法）：本期直连数据库，下期整体切换为
    HTTP 后端时逐方法转发即可，抽象层与调用方零改动。

    **留痕副作用**：实现须在每次状态流转时自动追加一条事件
    （``event_type='state_change'``），并约定 claim / finish / fail / cancel 等写操作
    顺带刷新任务心跳（活动即心跳）。二者均在实现内部完成，不暴露给调用方。
    """

    # region ======== 服务标识与初始化 ========
    @property
    @abstractmethod
    def service_type(self) -> str:
        """
        本实现的类型标识（如 'sqlite'、'mysql'；未来 'http'）——标识服务走什么底座，
        对标 ``RdbClient.db_type`` / ``MemoryStore.backend_type``。
        """

    @abstractmethod
    def setup(self) -> None:
        """
        初始化服务（幂等）：SQL 后端建六张任务表，HTTP 后端探活/握手。
        首次使用前调用。
        """

    # endregion

    # region ======== 任务 ========
    @abstractmethod
    def create_task(self, inst: TaskInstance) -> int:
        """
        创建任务实例（插入 pending 任务），回填并返回新 id。

        Args:
            inst: 任务实例（id 通常为 None；status 忽略输入固定为 pending）。

        Returns:
            新任务的主键 id。
        """

    @abstractmethod
    def get_task(self, id: int) -> TaskInstance | None:
        """
        按 id 取任务；不存在返回 None。
        """

    @abstractmethod
    def list_tasks(
        self,
        status: str | None = None,
        created_by: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        任务列表（按 id 倒序），含动态进度。

        进度不落列，由 steps 现算：每行附加 ``total``（步骤总数）与 ``done``
        （succeeded 数）。

        Args:
            status: 限定任务状态；None 表示不限。
            created_by: 限定创建者标签；None 表示不限。
            limit: 最多返回条数（默认 50）。
        """

    @abstractmethod
    def update_task(self, id: int, fields: dict[str, Any]) -> bool:
        """
        按 id 部分更新任务（仅 :data:`UPDATABLE_TASK_FIELDS` 白名单生效）。

        Returns:
            是否命中并更新（id 不存在或无可更新字段返回 False）。
        """

    @abstractmethod
    def heartbeat(self, id: int) -> None:
        """
        刷新任务心跳（单条 ``heartbeat_at = now``）。
        """

    @abstractmethod
    def pause(self, id: int) -> bool:
        """
        暂停任务（running → paused）。已 running 的步骤不强杀，只是不再派发新步骤。

        Returns:
            是否命中（非 running 状态返回 False）。
        """

    @abstractmethod
    def resume(self, id: int) -> bool:
        """
        恢复任务（paused → running）。

        Returns:
            是否命中（非 paused 状态返回 False）。
        """

    @abstractmethod
    def cancel(self, id: int, reason: str = "") -> bool:
        """
        取消任务（非终态 → cancelled）：running 步骤连带置 failed（注明取消原因），
        running run 置 cancelled；同一事务内完成。

        Args:
            id: 任务 id。
            reason: 取消原因（记入事件）。

        Returns:
            是否命中（已终态返回 False）。
        """

    # endregion

    # region ======== 步骤 ========
    @abstractmethod
    def add_step(self, step: TaskStep) -> int:
        """
        追加一个步骤，回填并返回新 id。

        ``seq`` 缺省（None）时按 ``max(seq) + 1`` 自动取号；``max_retries`` 缺省时
        继承任务级 ``TaskInstance.max_retries``；``step_type`` 须在
        :data:`VALID_STEP_TYPES` 内（非法抛 ``ValueError``）。

        Args:
            step: 步骤（id 通常为 None）。

        Returns:
            新步骤的主键 id。
        """

    @abstractmethod
    def add_steps(self, steps: list[TaskStep]) -> int:
        """
        批量追加步骤（同一事务；全部校验通过才插入，任一非法整体失败）。

        ``seq`` 依列表顺序自动取号（已显式给 seq 的按给定值，校验同任务内唯一）。

        Args:
            steps: 步骤列表（非空，须同属一个任务）。

        Returns:
            成功插入的步骤数。
        """

    @abstractmethod
    def get_step(self, id: int) -> TaskStep | None:
        """
        按 id 取步骤；不存在返回 None。
        """

    @abstractmethod
    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        """
        按任务列出全部步骤（按 seq 升序），返回行字典列表。
        """

    @abstractmethod
    def skip_step(self, id: int, reason: str = "") -> bool:
        """
        跳过 pending 步骤（pending → skipped）。

        skip 视同有意完成：若跳过后任务已无 pending/running/failed 步骤，
        running 任务自动收口为 completed（判定与 :meth:`finish_run` 一致；
        pending 任务保持 pending）。

        Args:
            id: 步骤 id。
            reason: 跳过原因（记入事件）。

        Returns:
            是否命中（非 pending 返回 False）。
        """

    @abstractmethod
    def retry_step(self, id: int, force: bool = False) -> bool:
        """
        手动重置失败/跳过的步骤回 pending（管理动作，绕过自动流转表）。

        语义："再给一次机会"——``max_retries + 1`` 后回 pending；若所属任务已因
        该步骤失败（failed），任务一并复活为 running（刷心跳），等下次 claim 继续。

        Args:
            id: 步骤 id。
            force: 管理强制开关——True 时额外放行 ``succeeded`` 步骤（用于就地修复
                被绕过状态机直接改库造成的假完成等异常现场）：步骤回 pending 并清空
                ``result_summary``；任务若已 ``completed`` 亦一并复活为 running。
                正常流程不应使用 force。

        Returns:
            是否命中（步骤状态不在允许范围返回 False）。
        """

    # endregion

    # region ======== 执行 ========
    @abstractmethod
    def claim_next_step(
        self,
        task_id: int,
        session_id: str | None = None,
        agent_name: str | None = None,
        ignore_deps: bool = False,
    ) -> dict[str, Any] | None:
        """
        原子认领任务的下一个可执行 pending 步骤，创建 running run。

        并发安全靠条件 UPDATE 的受影响行数做乐观锁。认领成功后：插 run（running）、
        任务首次 claim 时 pending → running、刷新心跳、追加事件——同一事务。

        **依赖感知**：任务内任一步骤声明了 ``depends_on``（依赖同任务更早 seq 列表）
        时，候选按 seq 升序遍历，只认领"依赖全部 succeeded/skipped"的步骤（多执行方
        并行消费同一任务时天然跳开未就绪的下游）；依赖全部未就绪返回 None。
        任务无任何 depends_on 声明时保持旧行为（认领 seq 最小 pending）。

        Args:
            task_id: 任务 id。
            session_id: 执行会话标识（盖章 run）。
            agent_name: agent 外壳标识（盖章 run）。
            ignore_deps: 逃生开关——True 时无视依赖声明，按旧行为认领 seq 最小 pending。

        Returns:
            **续跑上下文包**（新会话接手所需的全部信息）::

                {
                  "task":    {任务全部字段},
                  "step":    {步骤全部字段},
                  "run_id":  本次执行的 run id,
                  "context": [{"seq", "name", "result_summary"}, ...]  # 前序成功步骤摘要
                }

            任务无可认领步骤（或任务非 pending/running 状态）返回 None，
            调用方应转 status 查看终态。
        """

    @abstractmethod
    def finish_run(
        self, run_id: int, output: str = "", summary: str | None = None, token_usage: int | None = None
    ) -> bool:
        """
        成功收口一次执行：run → succeeded、step → succeeded（写 result_summary），
        该任务最后一个步骤完成时任务 → completed；同一事务。

        ``summary`` 缺省时截取 output 前 2000 字。任务未完成时顺带刷新心跳。

        Args:
            run_id: 执行 id。
            output: 执行输出原文（子代理返回）。
            summary: 一句话结果摘要（后续步骤的上下文来源，比 output 更重要）。
            token_usage: 本次执行 token 消耗（可选回填，仅落 run 行，不影响流转）。

        Returns:
            是否流转成功（run 已是终态返回 False，不重复流转）。
        """

    @abstractmethod
    def fail_run(self, run_id: int, error: str) -> str:
        """
        失败上报：run → failed，步骤去向按重试预算自动裁决（同一事务）。

        - 预算未耗尽：step running → pending（``retry_count + 1``），任务不变，等下次 claim；
        - 预算耗尽：step → failed，任务 → failed。

        Args:
            run_id: 执行 id。
            error: 失败原因。

        Returns:
            ``'retried'``（步骤已回 pending 待重试）、``'step_failed'``（预算耗尽终败）
            或 ``''``（run 已是终态，未做流转）。
        """

    @abstractmethod
    def get_run(self, run_id: int) -> TaskRun | None:
        """
        按 id 取执行记录；不存在返回 None。
        """

    @abstractmethod
    def list_runs(self, step_id: int) -> list[dict[str, Any]]:
        """
        按步骤列出全部执行尝试（按 id 升序），返回行字典列表。
        """

    @abstractmethod
    def list_task_runs(self, task_id: int) -> list[dict[str, Any]]:
        """
        按任务列出全部执行尝试（按 id 升序），返回行字典列表。
        """

    @abstractmethod
    def release_run(self, run_id: int, reason: str = "") -> str:
        """
        释放一次执行（管理动作）：run running → cancelled、所属步骤 running → pending
        （**不消耗重试预算**，retry_count 不变），任务保持 running 并刷心跳；同一事务。

        适用：执行方会话已结束/派发放弃但工作未完成，把步骤还回队列由其他会话
        续跑——区别于 :meth:`fail_run`（视作一次失败、消耗预算）。

        Args:
            run_id: 执行 id。
            reason: 释放原因（记入事件）。

        Returns:
            ``'released'``（已释放）或 ``''``（run 不存在或非 running，未流转）。
        """

    # endregion

    # region ======== 恢复 ========
    @abstractmethod
    def sweep(self, heartbeat_timeout_sec: int | None = None) -> list[dict[str, Any]]:
        """
        僵尸检测与恢复（幂等，可重复执行），同一事务。

        1. **任务总超时**：``status='running'`` 且 ``timeout_sec`` 非空且
           ``started_at`` 距今超过它——其 running 步骤的 running run 置 ``timeout``
           （error_msg='task total timeout'）、running 步骤直接置 ``failed``
           （不走预算裁决：整体时间预算已尽，重试无意义）、任务 → ``failed``。
           可用 :meth:`retry_step` 手动复活（预算 +1 且任务回 running）。
        2. **任务心跳**：``status='running'`` 且 ``heartbeat_at`` 超过心跳阈值的任务——
           其 running 步骤的 running run 置 ``timeout``（error_msg='heartbeat timeout'），
           步骤按重试预算回 pending 或置 failed（耗尽则任务 failed）。任务本身保持
           running（心跳断了 ≠ 任务死了，回 pending 的步骤等下次 claim）。
        3. **步骤级**：run.started_at 超过 step.timeout_sec 但仍在 running（任务心跳
           正常，属单步卡死）——按预算处理。

        Args:
            heartbeat_timeout_sec: 心跳超时阈值秒的全局覆盖；None = 逐任务用其自身
                ``heartbeat_timeout_sec`` 列（任务总超时不提供覆盖，只用自身 ``timeout_sec``）。

        Returns:
            被恢复对象的摘要列表，每项 ``{task_id, step_id, run_id, action, detail}``；
            ``action`` 取值 ``run_timeout / step_retry / step_failed / task_timeout /
            task_failed``（后两类仅在命中任务总超时时出现）。
        """

    @abstractmethod
    def verify_task(self, task_id: int, fix: bool = False) -> list[dict[str, Any]]:
        """
        一致性对账（防篡改审计）：以事件流水与 run 记录为真相源，核对步骤/任务当前
        状态的一致性，识别被绕过状态机直接改库造成的异常（如批量伪造 succeeded）。

        规则（每处异常一条发现）::

          V1 step=succeeded 但无对应 succeeded run 或缺 finish 事件 → 假成功；
             fix 时：有存活 running run 则步骤回 running（接管活执行），否则回
             pending 并清空 result_summary
          V2 step=running 但无任何 running run → 僵尸步骤；fix 时回 pending
          V3 run=running 但所属步骤非 running → 孤儿 run；fix 时置 cancelled
          V4 ≥3 个步骤 result_summary 完全相同 → 伪造指纹告警（仅报告，不自动修复）
          V5 task=completed 但存在 pending/running/failed 步骤 → 收口失真；
             fix 时任务回 running（finished_at 置空）
          V6 step=skipped 但无对应 skip 事件 → 仅告警

        Args:
            task_id: 任务 id。
            fix: 是否就地修复（全部修复动作同一事务执行，并追加一条 warn 级汇总事件留痕）。

        Returns:
            发现列表，每项 ``{rule, level, kind, id, detail, fixed}``；
            ``kind`` ∈ ``step/run/task``，``level`` ∈ info/warn/error，
            ``fixed`` 仅 fix=True 且该发现已修复时为 True。
        """

    # endregion

    # region ======== 产物 / 事件 ========
    @abstractmethod
    def add_artifact(
        self,
        task_id: int,
        art_type: str,
        path: str,
        step_id: int | None = None,
        note: str | None = None,
    ) -> int:
        """
        登记一个产物，回填并返回新 id。

        Args:
            task_id: 所属任务 id。
            art_type: 产物类型，须在 :data:`VALID_ART_TYPES` 内。
            path: 产物路径（相对仓库根或绝对路径）。
            step_id: 所属步骤 id；None = 任务级产物。
            note: 备注。
        """

    @abstractmethod
    def list_artifacts(self, task_id: int) -> list[dict[str, Any]]:
        """
        按任务列出全部产物（按 id 升序），返回行字典列表。
        """

    @abstractmethod
    def add_event(
        self,
        task_id: int,
        event_type: str,
        message: str,
        level: str = "info",
        step_id: int | None = None,
        run_id: int | None = None,
    ) -> int:
        """
        手动追加一条事件，回填并返回新 id（状态流转的自动留痕在实现内部，不经此方法）。

        Args:
            task_id: 所属任务 id。
            event_type: 事件类型，须在 :data:`VALID_EVENT_TYPES` 内。
            message: 事件内容。
            level: 级别，须在 :data:`VALID_EVENT_LEVELS` 内。
            step_id: 关联步骤 id；可空。
            run_id: 关联执行 id；可空。
        """

    @abstractmethod
    def list_events(self, task_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """
        按任务查事件流水（按 id 倒序取最近 limit 条），返回行字典列表。
        """

    # endregion

    # region ======== 模板 ========
    @abstractmethod
    def create_template(self, t: TaskTemplate) -> int:
        """
        保存模板，回填并返回新 id。

        Args:
            t: 模板（id 通常为 None）。

        Returns:
            新模板的主键 id。

        Raises:
            ValueError: 模板名已存在（唯一键冲突由实现转为该异常）。
        """

    @abstractmethod
    def get_template_by_name(self, template_name: str) -> TaskTemplate | None:
        """
        按模板名取模板；不存在返回 None。
        """

    @abstractmethod
    def list_templates(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        模板列表（按 id 倒序），返回行字典列表。
        """

    # endregion

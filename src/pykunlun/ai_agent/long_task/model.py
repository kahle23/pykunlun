"""
长任务数据模型（POJO 集合）。

集中承载长任务子包中"纯数据容器"性质的六个类，字段与上层实现的 ``ai_task_*``
六张表一一对应（对标 :mod:`pykunlun.ai.ocr.model` 的组织方式）：

  - :class:`TaskTemplate` —— 任务模板（可复用的一类长任务定义）
  - :class:`TaskInstance` —— 任务实例（核心表：一次具体的长任务）
  - :class:`TaskStep`     —— 步骤（计划中要做的事，seq 串行）
  - :class:`AgentRun`     —— 执行记录（实际的一次尝试，重试 = 新 run）
  - :class:`TaskArtifact` —— 产物（任务/步骤产出的文件与报告）
  - :class:`TaskEvent`    —— 事件日志（append-only 流水账）

均为 ``@dataclass``、自身不做校验；新建时 ``id`` 与时间戳留空由存储回填。
集中成单一数据模型模块便于按需 import，并避免与
:mod:`pykunlun.ai_agent.long_task.service` 形成循环依赖。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TaskTemplate:
    """
    长任务模板（可复用的一类长任务定义）。

    字段与 ``ai_task_template`` 表一一对应。新建时 ``id`` 留空，由存储回填。

    Attributes:
        name: 模板名（唯一）。
        skill_ref: 关联的技能标识；None 表示未关联。
        description: 模板说明。
        default_params: 默认参数（JSON 对象）。
        step_blueprint: 步骤蓝图，``[{name, instruction, step_type, timeout_sec, max_retries}]``
            的列表（JSON 数组）；模板实例化时按此拆步骤。
        id: 主键；新建时为 None，插入后由存储回填。
        created_at: 创建时间；新建时为 None，由存储回填。
        updated_at: 更新时间；新建时为 None，由存储回填。
    """

    name: str
    skill_ref: str | None = None
    description: str | None = None
    default_params: dict[str, Any] | None = None
    step_blueprint: list[dict[str, Any]] | None = None
    id: int | None = field(default=None)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'TaskTemplate':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TaskInstance:
    """
    长任务实例（核心表：一次具体的长任务）。

    字段与 ``ai_task_instance`` 表一一对应。新建时 ``id`` 留空，由存储回填；
    ``status`` 新建固定为 ``pending``。

    Attributes:
        title: 任务标题。
        goal: 任务目标（喂给编排层的总指令）。
        template_id: 来源模板 id；None = 临时任务。
        parent_task_id: 父任务 id；None = 顶层任务。
        status: 见 :data:`TASK_STATUSES`；默认 ``pending``。
        params: 任务参数（模板实例化/自定义，JSON 对象）。
        max_retries: 步骤默认重试预算（步骤未指定时继承）。
        heartbeat_at: 最近心跳；超时判僵尸。约定 claim/finish/fail 等写操作顺带刷新。
        heartbeat_timeout_sec: 心跳超时阈值秒；超过判僵尸。
        timeout_sec: 任务总超时秒；None = 不限。
        created_by: 创建者标识（标签，不鉴权）。
        started_at: 首次 claim 时间。
        finished_at: 终态时间（completed/failed/cancelled）。
        id: 主键；新建时为 None，插入后由存储回填。
        created_at: 创建时间；新建时为 None，由存储回填。
        updated_at: 更新时间；新建时为 None，由存储回填。
    """

    title: str
    goal: str
    template_id: int | None = None
    parent_task_id: int | None = None
    status: str = 'pending'
    params: dict[str, Any] | None = None
    max_retries: int = 1
    heartbeat_at: datetime | None = None
    heartbeat_timeout_sec: int = 1800
    timeout_sec: int | None = None
    created_by: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: int | None = field(default=None)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'TaskInstance':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TaskStep:
    """
    长任务步骤（计划）。

    字段与 ``ai_task_step`` 表一一对应。step 是"计划中要做的事"，run 是"实际的一次
    尝试"，二者分离——失败重试 = 同一 step 新增一条 run。按 ``seq`` 串行执行。

    Attributes:
        task_id: 所属任务 id。
        name: 步骤名。
        instruction: 该步骤完整指令（prompt/命令），无截断。
        seq: 执行顺序，从 1 起，同任务内唯一；新建时可为 None，由存储按 ``max(seq)+1`` 取号。
        step_type: 见 :data:`VALID_STEP_TYPES`（agent/bash/human_approval/condition）；默认 agent。
        status: 见 :data:`STEP_STATUSES`；默认 ``pending``。
        retry_count: 已重试次数。
        max_retries: 最大重试次数（含首次共 ``max_retries + 1`` 次机会）；None 表示
            未指定，落库时继承任务级 ``TaskInstance.max_retries``。
        timeout_sec: 单步超时秒；None = 不限（sweep 步骤级检测依据）。
        result_summary: 执行结果摘要（续跑会话的上下文来源）。
        started_at: 首次 claim 时间。
        finished_at: 终态时间。
        id: 主键；新建时为 None，插入后由存储回填。
        created_at: 创建时间；新建时为 None，由存储回填。
        updated_at: 更新时间；新建时为 None，由存储回填。
        depends_on: 依赖的同任务更早步骤 seq 列表（JSON 数组落 TEXT）；None/空 = 无显式
            依赖。claim 依赖感知模式下，依赖未全部 succeeded/skipped 的步骤不会被认领。
    """

    task_id: int
    name: str
    instruction: str
    seq: int | None = None
    step_type: str = 'agent'
    status: str = 'pending'
    retry_count: int = 0
    max_retries: int | None = None
    timeout_sec: int | None = None
    result_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: int | None = field(default=None)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    depends_on: list[int] | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'TaskStep':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class AgentRun:
    """
    长任务执行记录（一次尝试）。

    字段与 ``ai_task_run`` 表一一对应。run 仅由 :meth:`LongTaskService.claim_next_step`
    在认领步骤时创建，调用方不直接插 run。

    Attributes:
        step_id: 所属步骤 id。
        task_id: 所属任务 id（冗余，便于按任务查）。
        session_id: 执行会话标识（哪个 AI 会话跑的）。
        agent_name: agent 外壳标识（标签）。
        status: 见 :data:`RUN_STATUSES`；新建固定 ``running``。
        input_snapshot: 本次输入快照（含续跑上下文包，便于复现）。
        output: 执行输出（子代理返回的原文）。
        error_msg: 失败原因。
        token_usage: 本次 token 消耗（可选回填）。
        started_at: 开始时间；新建时为 None，由存储回填。
        finished_at: 结束时间。
        id: 主键；新建时为 None，插入后由存储回填。
    """

    step_id: int
    task_id: int | None = None
    session_id: str | None = None
    agent_name: str | None = None
    status: str = 'running'
    input_snapshot: str | None = None
    output: str | None = None
    error_msg: str | None = None
    token_usage: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    id: int | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'AgentRun':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TaskArtifact:
    """
    长任务产物（任务/步骤产出的文件与报告）。

    字段与 ``ai_task_artifact`` 表一一对应。

    Attributes:
        task_id: 所属任务 id。
        path: 产物路径（相对仓库根或绝对路径）。
        art_type: 见 :data:`VALID_ART_TYPES`（file/report/diff/log/other）；默认 file。
        step_id: 所属步骤 id；None = 任务级产物。
        note: 备注。
        id: 主键；新建时为 None，插入后由存储回填。
        created_at: 创建时间；新建时为 None，由存储回填。
    """

    task_id: int
    path: str
    art_type: str = 'file'
    step_id: int | None = None
    note: str | None = None
    id: int | None = field(default=None)
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'TaskArtifact':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TaskEvent:
    """
    长任务事件日志（append-only 流水账，状态对不上时以此为准）。

    字段与 ``ai_task_event`` 表一一对应。事件主要由各实现在状态流转时自动追加
    （``event_type='state_change'``），调用方也可经 :meth:`LongTaskService.add_event`
    手动记 checkpoint/note。

    Attributes:
        task_id: 所属任务 id。
        event_type: 见 :data:`VALID_EVENT_TYPES`（state_change/error/checkpoint/note/artifact）。
        message: 事件内容。
        step_id: 关联步骤 id；可空。
        run_id: 关联执行 id；可空。
        level: 见 :data:`VALID_EVENT_LEVELS`（info/warn/error）；默认 info。
        id: 主键；新建时为 None，插入后由存储回填。
        created_at: 创建时间；新建时为 None，由存储回填。
    """

    task_id: int
    event_type: str
    message: str
    step_id: int | None = None
    run_id: int | None = None
    level: str = 'info'
    id: int | None = field(default=None)
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'TaskEvent':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

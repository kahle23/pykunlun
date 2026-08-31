"""
长任务服务管理器。

:class:`LongTaskManager` 维护 ``name -> LongTaskService`` 注册表，按名称（别名）
管理各后端实例，对外逐方法转发。完全对标
:class:`pykunlun.ai_agent.memory.MemoryManager` 的注册/转发范式（亦同
:class:`pykunlun.ai.ocr.manager.OcrManager`）。
"""

from typing import Any

from pykunlun.util import logutil

from .model import AgentRun, TaskInstance, TaskStep, TaskTemplate
from .service import LongTaskService

log = logutil.getLogger(__name__)


class LongTaskManager:
    """
    长任务服务管理器。

    维护 ``name -> LongTaskService`` 注册表，按名称（别名）管理各后端实例，对外
    逐方法转发。转发即透传、无额外副作用（留痕副作用在 service 实现内部，
    因为事件需要行上下文——新旧状态、关联 step/run）。

    典型用法::

        from pykunlun.ai_agent import LongTaskManager, TaskInstance

        mgr = LongTaskManager()
        mgr.register('default', SqliteLongTaskService('/data/agent_task.db'))
        tid = mgr.create_task(TaskInstance(title='补全文档', goal='...'))
        pkg = mgr.claim_next_step(tid, session_id='session-1')
    """

    #: 默认实例名称（省略 name 时使用）
    DEFAULT_NAME = 'default'

    def __init__(self) -> None:
        self._services: dict[str, LongTaskService] = {}

    # region ======== 注册表管理 ========
    def _resolve_name(self, name: str | None) -> str:
        """将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。"""
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, service: LongTaskService) -> None:
        """
        注册一个长任务服务实例。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            service: :class:`LongTaskService` 实例。
        """
        key = self._resolve_name(name)
        self._services[key] = service
        log.debug("已注册长任务服务实例: %s (service_type=%s)", key, service.service_type)

    def unregister(self, name: str | None = None) -> bool:
        """注销实例；返回是否曾存在。"""
        key = self._resolve_name(name)
        return self._services.pop(key, None) is not None

    def get_service(self, name: str | None = None) -> LongTaskService:
        """
        按名称获取实例；不存在则抛出 :class:`KeyError`。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        key = self._resolve_name(name)
        if key not in self._services:
            registered = sorted(self._services.keys())
            raise KeyError(
                f"未注册的长任务服务实例: '{key}'；已注册的实例: {registered}；"
                f"请先通过 register() 注册"
            )
        return self._services[key]

    def get_registered_names(self) -> list[str]:
        """返回已注册的全部实例名称（排序）。"""
        return sorted(self._services.keys())

    def setup(self, name: str | None = None) -> None:
        self.get_service(name).setup()
    # endregion

    # region ======== 任务 ========
    def create_task(self, inst: TaskInstance, name: str | None = None) -> int:
        return self.get_service(name).create_task(inst)

    def get_task(self, id: int, name: str | None = None) -> TaskInstance | None:
        return self.get_service(name).get_task(id)

    def list_tasks(
        self,
        status: str | None = None,
        created_by: str | None = None,
        limit: int = 50,
        name: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.get_service(name).list_tasks(status=status, created_by=created_by, limit=limit)

    def update_task(self, id: int, fields: dict[str, Any], name: str | None = None) -> bool:
        return self.get_service(name).update_task(id, fields)

    def heartbeat(self, id: int, name: str | None = None) -> None:
        self.get_service(name).heartbeat(id)

    def pause(self, id: int, name: str | None = None) -> bool:
        return self.get_service(name).pause(id)

    def resume(self, id: int, name: str | None = None) -> bool:
        return self.get_service(name).resume(id)

    def cancel(self, id: int, reason: str = '', name: str | None = None) -> bool:
        return self.get_service(name).cancel(id, reason=reason)
    # endregion

    # region ======== 步骤 ========
    def add_step(self, step: TaskStep, name: str | None = None) -> int:
        return self.get_service(name).add_step(step)

    def add_steps(self, steps: list[TaskStep], name: str | None = None) -> int:
        return self.get_service(name).add_steps(steps)

    def get_step(self, id: int, name: str | None = None) -> TaskStep | None:
        return self.get_service(name).get_step(id)

    def list_steps(self, task_id: int, name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_steps(task_id)

    def skip_step(self, id: int, reason: str = '', name: str | None = None) -> bool:
        return self.get_service(name).skip_step(id, reason=reason)

    def retry_step(self, id: int, force: bool = False, name: str | None = None) -> bool:
        return self.get_service(name).retry_step(id, force=force)
    # endregion

    # region ======== 执行 ========
    def claim_next_step(
        self,
        task_id: int,
        session_id: str | None = None,
        agent_name: str | None = None,
        ignore_deps: bool = False,
        name: str | None = None,
    ) -> dict[str, Any] | None:
        return self.get_service(name).claim_next_step(task_id, session_id=session_id,
                                                      agent_name=agent_name,
                                                      ignore_deps=ignore_deps)

    def finish_run(
        self,
        run_id: int,
        output: str = '',
        summary: str | None = None,
        token_usage: int | None = None,
        name: str | None = None,
    ) -> bool:
        return self.get_service(name).finish_run(run_id, output=output, summary=summary,
                                                 token_usage=token_usage)

    def fail_run(self, run_id: int, error: str, name: str | None = None) -> str:
        return self.get_service(name).fail_run(run_id, error)

    def get_run(self, run_id: int, name: str | None = None) -> AgentRun | None:
        return self.get_service(name).get_run(run_id)

    def list_runs(self, step_id: int, name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_runs(step_id)

    def list_task_runs(self, task_id: int, name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_task_runs(task_id)

    def release_run(self, run_id: int, reason: str = '', name: str | None = None) -> str:
        return self.get_service(name).release_run(run_id, reason=reason)
    # endregion

    # region ======== 恢复 ========
    def sweep(self, heartbeat_timeout_sec: int | None = None,
              name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).sweep(heartbeat_timeout_sec=heartbeat_timeout_sec)

    def verify_task(self, task_id: int, fix: bool = False,
                    name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).verify_task(task_id, fix=fix)
    # endregion

    # region ======== 产物 / 事件 ========
    def add_artifact(
        self,
        task_id: int,
        art_type: str,
        path: str,
        step_id: int | None = None,
        note: str | None = None,
        name: str | None = None,
    ) -> int:
        return self.get_service(name).add_artifact(task_id, art_type, path,
                                                   step_id=step_id, note=note)

    def list_artifacts(self, task_id: int, name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_artifacts(task_id)

    def add_event(
        self,
        task_id: int,
        event_type: str,
        message: str,
        level: str = 'info',
        step_id: int | None = None,
        run_id: int | None = None,
        name: str | None = None,
    ) -> int:
        return self.get_service(name).add_event(task_id, event_type, message, level=level,
                                                step_id=step_id, run_id=run_id)

    def list_events(self, task_id: int, limit: int = 100,
                    name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_events(task_id, limit=limit)
    # endregion

    # region ======== 模板 ========
    def create_template(self, t: TaskTemplate, name: str | None = None) -> int:
        return self.get_service(name).create_template(t)

    def get_template_by_name(self, template_name: str,
                             name: str | None = None) -> TaskTemplate | None:
        return self.get_service(name).get_template_by_name(template_name)

    def list_templates(self, limit: int = 50, name: str | None = None) -> list[dict[str, Any]]:
        return self.get_service(name).list_templates(limit=limit)
    # endregion

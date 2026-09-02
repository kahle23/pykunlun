"""
长任务服务管理器。

:class:`LongTaskManager` 维护 ``name -> LongTaskService`` 注册表，按名称（别名）
管理各后端实例，对外逐方法转发。完全对标
:class:`pykunlun.ai_agent.memory.MemoryManager` 的注册/转发范式（亦同
:class:`pykunlun.ai.ocr.manager.OcrManager`）。

无人值守执行（:meth:`LongTaskManager.run_task`）是本管理器唯一包含本地行为的
组合方法：bash 步骤以子进程执行并自动收口，其余步骤交回 AI 编排层。
"""

import os
import signal
import subprocess
import sys
import time
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

    def run_task(
        self,
        task_id: int,
        session_id: str | None = None,
        agent_name: str | None = None,
        name: str | None = None,
        output_head_chars: int = 32_000,
        output_tail_chars: int = 16_000,
    ) -> list[dict[str, Any]]:
        """
        无人值守执行循环：``run = claim + 执行 + 收口``（headless 执行器）。

        循环 :meth:`claim_next_step` 认领步骤并按 ``step_type`` 分派：

        - ``bash``：把 ``instruction`` 当 shell 命令行以**子进程**执行（stdout/stderr
          合流捕获；超时按步骤 ``timeout_sec``——超时**强杀整棵进程树**，Windows
          ``taskkill /T /F``、POSIX 杀进程组，防 playwright/浏览器等子进程残留）。
          退出码 0 → :meth:`finish_run`；非 0 或超时 → :meth:`fail_run`（重试预算内
          步骤回 pending，但**本轮不自动重跑**——确定性失败会死循环烧预算，重试由
          外部再次 ``run``/``retry`` 触发）。超时未设置（``timeout_sec=None``）则不限时。
        - ``agent`` / ``human_approval`` / ``condition``：headless 不执行——
          :meth:`release_run` 还回队列（不烧预算）并**停止本轮**（这类步骤通常是
          后续 bash 的上游，应交回 AI 编排层处理）。

        claim 返回 None（任务完成/无可认领步骤）时正常结束。幂等：中断后重跑
        ``run`` 即断点续跑（已 succeeded 的步骤不会被再次认领）。

        Args:
            task_id: 任务 id。
            session_id: 执行会话标识（盖章 run）。
            agent_name: agent 外壳标识（盖章 run）。
            name: 服务实例别名；省略用默认实例。
            output_head_chars: 步骤输出保留头部字符数（落库防撑爆，中间省略）。
            output_tail_chars: 步骤输出保留尾部字符数。

        Returns:
            逐步骤结果行列表::

                [{"seq", "name", "step_id", "run_id", "step_type", "status",
                  "exit_code", "duration_sec", "summary"}, ...]

            ``status`` ∈ ``succeeded`` / ``failed`` / ``retried`` / ``released``；
            ``exit_code`` 仅 bash 步骤有（超时为 None）。
        """
        results: list[dict[str, Any]] = []
        while True:
            pkg = self.claim_next_step(task_id, session_id=session_id, agent_name=agent_name,
                                       name=name)
            if pkg is None:
                break
            step = pkg['step']
            run_id = pkg['run_id']
            row: dict[str, Any] = {
                'seq': step.get('seq'), 'name': step.get('name'),
                'step_id': step.get('id'), 'run_id': run_id,
                'step_type': step.get('step_type'), 'exit_code': None,
                'duration_sec': None, 'summary': None,
            }
            if step.get('step_type') != 'bash':
                reason = (f"headless run 不执行 {step.get('step_type')} 步骤"
                          f"「{step.get('name')}」，需 AI 编排层接手")
                self.release_run(run_id, reason=reason, name=name)
                log.warning("%s；本轮停止，步骤已还回队列", reason)
                row['status'] = 'released'
                row['summary'] = reason
                results.append(row)
                break
            row.update(self._run_bash_step(step, run_id, name=name,
                                           output_head_chars=output_head_chars,
                                           output_tail_chars=output_tail_chars))
            results.append(row)
            if row['status'] in ('failed', 'retried'):
                break
        return results

    def _run_bash_step(
        self,
        step: dict[str, Any],
        run_id: int,
        name: str | None,
        output_head_chars: int,
        output_tail_chars: int,
    ) -> dict[str, Any]:
        """执行单个 bash 步骤并自动收口，返回结果行增量（status/exit_code/duration/summary）。"""
        cmd = (step.get('instruction') or '').strip()
        timeout_sec = step.get('timeout_sec')
        short_cmd = cmd[:80] + ('…' if len(cmd) > 80 else '')
        if not cmd:
            self.fail_run(run_id, error='bash 步骤 instruction 为空，无可执行命令', name=name)
            return {'status': 'failed', 'summary': 'bash 步骤 instruction 为空'}
        t0 = time.monotonic()
        try:
            rc, output, timed_out = self._run_shell(cmd, timeout_sec, output_head_chars,
                                                    output_tail_chars)
        except Exception as e:  # noqa: BLE001 — 任何启动异常都按步骤失败收口，不悬 running
            self.fail_run(run_id, error=f'命令启动异常: {e}', name=name)
            return {'status': 'failed',
                    'summary': f'命令启动异常: {e} | {short_cmd}'}
        duration = round(time.monotonic() - t0, 1)
        if timed_out:
            self.fail_run(run_id, error=f'超时（>{timeout_sec}s）已强杀进程树 | {short_cmd}',
                          name=name)
            status, summary = 'failed', f'timeout after {timeout_sec}s | {short_cmd}'
        elif rc == 0:
            self.finish_run(run_id, output=output, summary=f'exit=0 ({duration}s) | {short_cmd}',
                            name=name)
            status, summary = 'succeeded', f'exit=0 ({duration}s) | {short_cmd}'
        else:
            disposition = self.fail_run(run_id, error=f'exit={rc} | {short_cmd}', name=name)
            if disposition == 'retried':
                log.warning("步骤「%s」失败但重试预算未耗尽，已回 pending；"
                            "run 不自动重跑（防确定性失败死循环），请排查后再次 run/retry",
                            step.get('name'))
            status = 'retried' if disposition == 'retried' else 'failed'
            summary = f'exit={rc} ({duration}s) | {short_cmd}'
        return {'status': status, 'exit_code': None if timed_out else rc,
                'duration_sec': duration, 'summary': summary}

    @staticmethod
    def _run_shell(
        cmd: str,
        timeout_sec: int | None,
        output_head_chars: int,
        output_tail_chars: int,
    ) -> tuple[int | None, str, bool]:
        """
        以 shell 执行命令行，返回 ``(exit_code, 合流输出, 是否超时强杀)``。

        超时强杀整棵进程树（Windows ``taskkill /PID <pid> /T /F``；POSIX 杀进程组），
        杀后回收僵尸并返回 ``(None, 已有输出, True)``。输出超长时保头尾、中间省略。
        """
        p = subprocess.Popen(
            cmd, shell=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=(os.name != 'nt'),
        )
        timed_out = False
        try:
            out, _ = p.communicate(timeout=timeout_sec)
            rc: int | None = p.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'],
                               capture_output=True, check=False)
            else:
                import errno
                try:
                    os.killpg(os.getpgid(p.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as e:
                    if e.errno != errno.ESRCH:
                        raise
            try:
                out, _ = p.communicate(timeout=60)
            except Exception:
                # 杀树后仍可能有孤儿孙进程持有输出管道（罕见），强杀本进程后收尾
                p.kill()
                try:
                    out, _ = p.communicate(timeout=10)
                except Exception:
                    out = b''
            rc = None
        text = (out or b'').decode(errors='replace')
        if len(text) > output_head_chars + output_tail_chars:
            text = (text[:output_head_chars]
                    + f'\n…[输出过长，中间省略 {len(text) - output_head_chars - output_tail_chars} 字符]…\n'
                    + text[-output_tail_chars:])
        return rc, text, timed_out
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

"""
长任务服务的内置实现（基于 Python 标准库 sqlite3，无第三方依赖）。

对标 :mod:`pykunlun.ai.ocr.rapidocr_engine`：作为长任务子包内的**轻量本地默认实现**
随 :class:`~pykunlun.ai_agent.long_task.manager.LongTaskManager` 开箱即用，提供
:class:`SqliteLongTaskService`——基于 SQLite 文件的长任务服务，单文件持久化、
跨进程可读、重启不丢失；建 ``ai_task_*`` 六张表，仅依赖标准库 ``sqlite3``。

适用于无需配置 rdb 的轻量部署、单测、快速起步。需要 MySQL，或想复用已注册的
rdb 实例时，使用上层包的 ``MySqlLongTaskService``。

时间一律以 ISO 字符串落 TEXT（ISO 格式按字典序即为时间序，便于比较与排序）；
JSON 字段（params / default_params / step_blueprint）以 ``ensure_ascii=False``
序列化落 TEXT，读取侧容错反序列化（列值可能被人工改过）。
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

from pykunlun.util import logutil

from .model import AgentRun, TaskInstance, TaskStep, TaskTemplate
from .service import LongTaskService
from .state import (
    UPDATABLE_TASK_FIELDS,
    VALID_ART_TYPES,
    VALID_EVENT_LEVELS,
    VALID_EVENT_TYPES,
    VALID_STEP_TYPES,
    deps_satisfied,
    parse_depends_on,
    step_disposition_on_fail,
)

log = logutil.getLogger(__name__)


def _iso(dt: datetime | None) -> str | None:
    """datetime → ISO 字符串（None 直通），供时间列落库。

    Python 3.12 起默认 datetime→SQLite 适配器已弃用，故显式序列化为 ISO 字符串；
    ISO 格式按字典序即为时间序，便于 ``ORDER BY`` 与超时比较。
    """
    return dt.isoformat() if dt is not None else None


def _jdump(obj: Any) -> str | None:
    """对象 → JSON 字符串（ensure_ascii=False；None 直通），供 JSON 列落库。"""
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False)


def _jload(text: Any) -> Any:
    """JSON 字符串 → 对象，容错：非字符串/解析失败返回 None（列值可能被人工改过）。"""
    if text is None or text == '':
        return None
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        log.warning("JSON 列解析失败，按 None 处理: %.100s", text)
        return None


class SqliteLongTaskService(LongTaskService):
    """
    基于 SQLite 的长任务服务（标准库 sqlite3，单文件持久化）。

    占位符为 ``?``，全程参数化，防 SQL 注入。单语句操作每次建立并关闭连接
    （connect-per-call）；复合操作（claim_next_step / finish_run / fail_run /
    sweep / cancel / add_steps 等）在 :meth:`_conn` 提供的单连接事务内完成，
    语义与 MySQL 实现完全一致。

    Args:
        db_path: SQLite 数据库文件路径。**不支持** ``':memory:'``——connect-per-call
            下每次连接彼此隔离、数据不跨连接留存；测试请使用临时文件。
        table_prefix: 表名前缀（默认空）。表名固定 ``ai_task_*``，前缀用于同一库内
            隔离多套任务表。
    """

    service_type = 'sqlite'

    def __init__(self, db_path: str, table_prefix: str = '') -> None:
        if db_path == ':memory:':
            raise ValueError(
                "SqliteLongTaskService 不支持 ':memory:'（connect-per-call 下数据不跨连接留存）；"
                "请使用文件路径（测试可用 tempfile）"
            )
        self._db_path = db_path
        self._prefix = table_prefix or ''

    @property
    def db_path(self) -> str:
        return self._db_path

    def _t(self, base: str) -> str:
        """基名 → 实际表名（拼接前缀）。"""
        return f'{self._prefix}{base}'

    # region ======== 连接与执行 ========
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row  # type: ignore[assignment]
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """事务连接：正常退出 commit，异常回滚后重抛，用毕关闭。"""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                log.warning("事务回滚失败", exc_info=True)
            raise
        finally:
            conn.close()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """只读查询（独立连接，无事务）。"""
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """单语句写（独立连接，自动 commit），返回受影响行数。"""
        with self._conn() as conn:
            return conn.execute(sql, params).rowcount

    @staticmethod
    def _q(conn: sqlite3.Connection, sql: str,
           params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """事务内查询（与未提交写同连接可见），返回行字典列表。"""
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _lastrowid(cur: sqlite3.Cursor) -> int:
        """取 INSERT 回填的主键；lastrowid 为 None 属非预期，直接抛错。"""
        if cur.lastrowid is None:
            raise RuntimeError("INSERT 未返回 lastrowid（非预期，请检查表结构）")
        return cur.lastrowid

    def _event(
        self,
        conn: sqlite3.Connection,
        task_id: int,
        event_type: str,
        message: str,
        level: str = 'info',
        step_id: int | None = None,
        run_id: int | None = None,
    ) -> None:
        """事务内追加事件（append-only 留痕）。"""
        conn.execute(
            f'INSERT INTO {self._t("ai_task_event")} '
            f'(task_id, step_id, run_id, event_type, level, message, created_at) '
            f'VALUES (?,?,?,?,?,?,?)',
            (task_id, step_id, run_id, event_type, level, message, _iso(datetime.now())),
        )
    # endregion

    # region ======== 建表（幂等）与补列迁移 ========
    def setup(self) -> None:
        """幂等建 ``ai_task_*`` 六张表 + 索引，并给老库自动补新增列（幂等可重复执行）。"""
        with self._conn() as conn:
            self._ddl_all(conn)
            self._migrate(conn)
        log.info("SqliteLongTaskService 已初始化 6 张 ai_task_* 表 (db=%s)", self._db_path)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """轻量补列迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的表补新列，
        此处按 ``PRAGMA table_info`` 探测缺列并 ALTER（0.0.5 起的机制，新增列在此登记）。"""
        t = self._t('ai_task_step')
        cols = {r['name'] for r in self._q(conn, f'PRAGMA table_info({t})')}
        if cols and 'depends_on' not in cols:
            conn.execute(f'ALTER TABLE {t} ADD COLUMN depends_on TEXT')
            log.info("已补列 %s.depends_on", t)

    def _ddl_all(self, conn: sqlite3.Connection) -> None:
        p = self._prefix
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_template (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            skill_ref TEXT,
            description TEXT,
            default_params TEXT,
            step_blueprint TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE (name)
        )""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_instance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            parent_task_id INTEGER,
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            params TEXT,
            max_retries INTEGER NOT NULL DEFAULT 1,
            heartbeat_at TEXT,
            heartbeat_timeout_sec INTEGER NOT NULL DEFAULT 1800,
            timeout_sec INTEGER,
            created_by TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_step (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            seq INTEGER NOT NULL,
            name TEXT NOT NULL,
            step_type TEXT NOT NULL DEFAULT 'agent',
            instruction TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 1,
            timeout_sec INTEGER,
            depends_on TEXT,
            result_summary TEXT,
            started_at TEXT,
            finished_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE (task_id, seq)
        )""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_run (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            step_id INTEGER NOT NULL,
            session_id TEXT,
            agent_name TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            input_snapshot TEXT,
            output TEXT,
            error_msg TEXT,
            token_usage INTEGER,
            started_at TEXT NOT NULL,
            finished_at TEXT
        )""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_artifact (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            step_id INTEGER,
            art_type TEXT NOT NULL DEFAULT 'file',
            path TEXT NOT NULL,
            note TEXT,
            created_at TEXT
        )""")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {p}ai_task_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            step_id INTEGER,
            run_id INTEGER,
            event_type TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            message TEXT NOT NULL,
            created_at TEXT
        )""")
        indexes = [
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_inst_status ON {p}ai_task_instance (status)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_inst_zombie ON {p}ai_task_instance (status, heartbeat_at)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_inst_template ON {p}ai_task_instance (template_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_inst_parent ON {p}ai_task_instance (parent_task_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_step_status ON {p}ai_task_step (task_id, status)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_run_step ON {p}ai_task_run (step_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_run_task ON {p}ai_task_run (task_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_run_status ON {p}ai_task_run (status)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_art_task ON {p}ai_task_artifact (task_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_art_step ON {p}ai_task_artifact (step_id)',
            f'CREATE INDEX IF NOT EXISTS idx_{p}ai_task_event_task ON {p}ai_task_event (task_id, id)',
        ]
        for stmt in indexes:
            conn.execute(stmt)
    # endregion

    # region ======== 行转换 ========
    def _task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row['params'] = _jload(row.get('params'))
        return row

    def _template_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row['default_params'] = _jload(row.get('default_params'))
        row['step_blueprint'] = _jload(row.get('step_blueprint'))
        return row

    def _fetch_task(self, conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
        rows = self._q(conn, f'SELECT * FROM {self._t("ai_task_instance")} WHERE id = ?', (task_id,))
        return rows[0] if rows else None

    def _touch_heartbeat(self, conn: sqlite3.Connection, task_id: int) -> None:
        """刷新任务心跳（活动即心跳）。"""
        conn.execute(
            f'UPDATE {self._t("ai_task_instance")} SET heartbeat_at = ?, updated_at = ? WHERE id = ?',
            (_iso(datetime.now()), _iso(datetime.now()), task_id),
        )
    # endregion

    # region ======== 任务 ========
    def create_task(self, inst: TaskInstance) -> int:
        now = datetime.now()
        with self._conn() as conn:
            cur = conn.execute(
                f'INSERT INTO {self._t("ai_task_instance")} '
                f'(template_id, parent_task_id, title, goal, status, params, max_retries, '
                f'heartbeat_timeout_sec, timeout_sec, created_by, created_at, updated_at) '
                f'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (inst.template_id, inst.parent_task_id, inst.title, inst.goal, 'pending',
                 _jdump(inst.params), inst.max_retries, inst.heartbeat_timeout_sec,
                 inst.timeout_sec, inst.created_by, _iso(now), _iso(now)),
            )
            task_id = self._lastrowid(cur)
            self._event(conn, task_id, 'note', f'任务创建: {inst.title}')
        inst.id = task_id
        return task_id

    def get_task(self, id: int) -> TaskInstance | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_instance")} WHERE id = ?', (id,))
        return TaskInstance.from_dict(self._task_row(rows[0])) if rows else None

    def list_tasks(
        self,
        status: str | None = None,
        created_by: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append('t.status = ?')
            params.append(status)
        if created_by is not None:
            where.append('t.created_by = ?')
            params.append(created_by)
        where_clause = f'WHERE {" AND ".join(where)}' if where else ''
        s = self._t('ai_task_step')
        sql = (f'SELECT t.*, '
               f'(SELECT COUNT(*) FROM {s} WHERE {s}.task_id = t.id) AS total, '
               f'(SELECT COUNT(*) FROM {s} WHERE {s}.task_id = t.id AND {s}.status = ?) AS done '
               f'FROM {self._t("ai_task_instance")} t {where_clause} '
               f'ORDER BY t.id DESC LIMIT ?')
        # 子查询的 done 状态参数在最前，其后是 WHERE 参数，最后 LIMIT
        all_params = tuple(['succeeded'] + params + [limit])
        return [self._task_row(r) for r in self._query(sql, all_params)]

    def update_task(self, id: int, fields: dict[str, Any]) -> bool:
        allowed = {k: v for k, v in fields.items() if k in UPDATABLE_TASK_FIELDS}
        if not allowed:
            return False
        allowed = {k: _jdump(v) if k == 'params' else v for k, v in allowed.items()}
        set_parts = [f'{k} = ?' for k in allowed]
        params: list[Any] = list(allowed.values())
        set_parts.append('updated_at = ?')
        params.append(_iso(datetime.now()))
        params.append(id)
        sql = (f'UPDATE {self._t("ai_task_instance")} SET {", ".join(set_parts)} '
               f'WHERE id = ?')
        return self._execute(sql, tuple(params)) > 0

    def heartbeat(self, id: int) -> None:
        self._execute(
            f'UPDATE {self._t("ai_task_instance")} SET heartbeat_at = ?, updated_at = ? WHERE id = ?',
            (_iso(datetime.now()), _iso(datetime.now()), id),
        )

    def pause(self, id: int) -> bool:
        now = _iso(datetime.now())
        affected = self._execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET status = ?, heartbeat_at = ?, updated_at = ? WHERE id = ? AND status = ?',
            ('paused', now, now, id, 'running'),
        )
        if affected:
            self._execute_event(id, 'state_change', 'task: running → paused')
        return affected > 0

    def resume(self, id: int) -> bool:
        now = _iso(datetime.now())
        affected = self._execute(
            f'UPDATE {self._t("ai_task_instance")} '
            f'SET status = ?, heartbeat_at = ?, updated_at = ? WHERE id = ? AND status = ?',
            ('running', now, now, id, 'paused'),
        )
        if affected:
            self._execute_event(id, 'state_change', 'task: paused → running')
        return affected > 0

    def cancel(self, id: int, reason: str = '') -> bool:
        now = datetime.now()
        with self._conn() as conn:
            task = self._fetch_task(conn, id)
            if task is None:
                log.warning("cancel 未找到任务 id=%s", id)
                return False
            if task['status'] in ('completed', 'failed', 'cancelled'):
                log.info("任务 id=%s 已终态(%s)，无需取消", id, task['status'])
                return False
            # 连带处理：running 步骤置 failed（注明取消）、running run 置 cancelled
            steps = self._q(
                conn, f'SELECT * FROM {self._t("ai_task_step")} WHERE task_id = ? AND status = ?',
                (id, 'running'))
            conn.execute(
                f'UPDATE {self._t("ai_task_run")} '
                f'SET status = ?, finished_at = ? WHERE task_id = ? AND status = ?',
                ('cancelled', _iso(now), id, 'running'))
            for s in steps:
                conn.execute(
                    f'UPDATE {self._t("ai_task_step")} '
                    f'SET status = ?, finished_at = ?, updated_at = ? WHERE id = ? AND status = ?',
                    ('failed', _iso(now), _iso(now), s['id'], 'running'))
                self._event(conn, id, 'state_change',
                            f"step {s['id']}: running → failed (cancelled)", step_id=s['id'])
            conn.execute(
                f'UPDATE {self._t("ai_task_instance")} '
                f'SET status = ?, finished_at = ?, updated_at = ? WHERE id = ?',
                ('cancelled', _iso(now), _iso(now), id))
            msg = 'task: → cancelled' + (f' ({reason})' if reason else '')
            self._event(conn, id, 'state_change', msg)
        return True

    def _execute_event(self, task_id: int, event_type: str, message: str) -> None:
        """独立事务追加事件（供单语句状态流转后留痕）。"""
        with self._conn() as conn:
            self._event(conn, task_id, event_type, message)
    # endregion

    # region ======== 步骤 ========
    def _validate_step(self, step: TaskStep) -> None:
        if not step.name or not step.name.strip():
            raise ValueError("步骤 name 不能为空")
        if not step.instruction or not step.instruction.strip():
            raise ValueError(f"步骤 {step.name!r} 的 instruction 不能为空")
        if step.step_type not in VALID_STEP_TYPES:
            raise ValueError(
                f"非法的 step_type: {step.step_type!r}（合法值: {sorted(VALID_STEP_TYPES)}）")
        if step.depends_on is not None:
            deps = step.depends_on
            if not isinstance(deps, list) or any(
                    isinstance(v, bool) or not isinstance(v, int) for v in deps):
                raise ValueError(
                    f"步骤 {step.name!r} 的 depends_on 须为 int 列表（依赖步骤的 seq）")
            if len(set(deps)) != len(deps):
                raise ValueError(f"步骤 {step.name!r} 的 depends_on 含重复 seq")

    def _check_deps_vs_seq(self, step: TaskStep, seq: int) -> None:
        """seq 取号后校验依赖方向：只能依赖同任务更早的 seq（防环/防自依赖）。"""
        if not step.depends_on:
            return
        bad = [d for d in step.depends_on if d <= 0 or d >= seq]
        if bad:
            raise ValueError(
                f"步骤 {step.name!r} 的 depends_on 只能引用同任务更早的 seq "
                f"（非法: {bad}，自身 seq={seq}）")

    def _resolve_step_defaults(self, conn: sqlite3.Connection, step: TaskStep) -> tuple[int, int]:
        """补全 seq（缺省 max+1）与 max_retries（缺省继承任务），返回 (seq, max_retries)。"""
        rows = self._q(conn, f'SELECT MAX(seq) AS mx FROM {self._t("ai_task_step")} '
                             f'WHERE task_id = ?', (step.task_id,))
        base = int(rows[0]['mx']) if rows and rows[0]['mx'] is not None else 0
        seq = step.seq if step.seq is not None else base + 1
        if seq <= base:
            raise ValueError(f"步骤 seq={seq} 与已有步骤冲突（当前最大 seq={base}）")
        if step.max_retries is not None:
            mr = step.max_retries
        else:
            task = self._fetch_task(conn, step.task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={step.task_id}")
            mr = int(task['max_retries'])
        return seq, mr

    def _insert_step(self, conn: sqlite3.Connection, step: TaskStep, seq: int,
                     max_retries: int) -> int:
        now = _iso(datetime.now())
        cur = conn.execute(
            f'INSERT INTO {self._t("ai_task_step")} '
            f'(task_id, seq, name, step_type, instruction, status, retry_count, max_retries, '
            f'timeout_sec, depends_on, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (step.task_id, seq, step.name, step.step_type, step.instruction, 'pending',
             step.retry_count, max_retries, step.timeout_sec, _jdump(step.depends_on),
             now, now))
        return self._lastrowid(cur)

    def add_step(self, step: TaskStep) -> int:
        self._validate_step(step)
        with self._conn() as conn:
            seq, mr = self._resolve_step_defaults(conn, step)
            self._check_deps_vs_seq(step, seq)
            step_id = self._insert_step(conn, step, seq, mr)
            self._event(conn, step.task_id, 'note',
                        f'step added: seq={seq} {step.name} (step_id={step_id})')
        step.seq = seq
        step.max_retries = mr
        step.id = step_id
        return step_id

    def add_steps(self, steps: list[TaskStep]) -> int:
        if not steps:
            raise ValueError("steps 不能为空")
        task_ids = {s.task_id for s in steps}
        if len(task_ids) > 1:
            raise ValueError(f"批量导入的步骤须属于同一任务（发现 {sorted(task_ids)}）")
        for s in steps:
            self._validate_step(s)
        with self._conn() as conn:
            # 先整体校验（任务存在性 + seq 取号），全部通过才插入
            resolved: list[tuple[TaskStep, int, int]] = []
            base = 0
            for s in steps:
                seq = s.seq if s.seq is not None else base + 1
                if seq <= base:
                    raise ValueError(f"步骤 {s.name!r} 的 seq={seq} 与前序冲突（当前最大 {base}）")
                base = seq
                self._check_deps_vs_seq(s, seq)
                mr = s.max_retries
                if mr is None:
                    task = self._fetch_task(conn, s.task_id)
                    if task is None:
                        raise ValueError(f"任务不存在: id={s.task_id}")
                    mr = int(task['max_retries'])
                resolved.append((s, seq, mr))
            for s, seq, mr in resolved:
                sid = self._insert_step(conn, s, seq, mr)
                s.id, s.seq, s.max_retries = sid, seq, mr
            self._event(conn, steps[0].task_id, 'note', f'批量导入 {len(steps)} 个步骤')
        return len(resolved)

    def get_step(self, id: int) -> TaskStep | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_step")} WHERE id = ?', (id,))
        return TaskStep.from_dict(rows[0]) if rows else None

    def list_steps(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_step")} WHERE task_id = ? ORDER BY seq', (task_id,))

    def skip_step(self, id: int, reason: str = '') -> bool:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        now = _iso(datetime.now())
        with self._conn() as conn:
            rows = self._q(conn, f'SELECT * FROM {t_step} WHERE id = ?', (id,))
            if not rows:
                log.warning("skip 未找到步骤 id=%s", id)
                return False
            affected = conn.execute(
                f'UPDATE {t_step} '
                f'SET status = ?, finished_at = ?, updated_at = ? WHERE id = ? AND status = ?',
                ('skipped', now, now, id, 'pending')).rowcount
            if not affected:
                log.warning("步骤 id=%s 非 pending（%s），不可 skip", id, rows[0]['status'])
                return False
            msg = f"step {id}: pending → skipped" + (f' ({reason})' if reason else '')
            self._event(conn, rows[0]['task_id'], 'state_change', msg, step_id=id)
            # 收口判定与 finish_run 一致：skip 视同有意完成，跳过最后剩余步骤时
            # 任务应收口（仅 running 任务；pending 任务跳完全部步骤保持 pending）。
            cnt = self._q(
                conn, f'SELECT status, COUNT(*) AS n FROM {t_step} '
                      f'WHERE task_id = ? AND status IN (?,?,?) GROUP BY status',
                (rows[0]['task_id'], 'pending', 'running', 'failed'))
            counts = {r['status']: r['n'] for r in cnt}
            if not counts.get('pending') and not counts.get('running') and not counts.get('failed'):
                conn.execute(
                    f'UPDATE {t_task} SET status = ?, finished_at = ?, heartbeat_at = ?, '
                    f'updated_at = ? WHERE id = ? AND status = ?',
                    ('completed', now, now, now, rows[0]['task_id'], 'running'))
                self._event(conn, rows[0]['task_id'], 'state_change',
                            'task: running → completed (剩余步骤全部跳过)')
        return True

    def retry_step(self, id: int, force: bool = False) -> bool:
        now = datetime.now()
        with self._conn() as conn:
            rows = self._q(conn, f'SELECT * FROM {self._t("ai_task_step")} WHERE id = ?', (id,))
            if not rows:
                log.warning("retry 未找到步骤 id=%s", id)
                return False
            step = rows[0]
            allowed = ('failed', 'skipped', 'succeeded') if force else ('failed', 'skipped')
            if step['status'] not in allowed:
                log.warning("步骤 id=%s 状态为 %s，仅 failed/skipped 可 retry%s",
                            id, step['status'], "（force 可加 succeeded）" if not force else "")
                return False
            clear_summary = force and step['status'] == 'succeeded'
            sets = ('status = ?, max_retries = max_retries + 1, updated_at = ?'
                    if not clear_summary else
                    'status = ?, max_retries = max_retries + 1, result_summary = NULL, '
                    'finished_at = NULL, updated_at = ?')
            conn.execute(
                f'UPDATE {self._t("ai_task_step")} SET {sets} WHERE id = ?',
                ('pending', _iso(now), id))
            tag = 'manual retry, budget+1' + (', force' if force else '')
            self._event(conn, step['task_id'], 'state_change',
                        f'step {id}: {step["status"]} → pending ({tag})', step_id=id)
            # 任务已终态且可归因于该步骤时一并复活（failed 常规；completed 仅 force——
            # 正常收口不会 failed，被改库伪造的 completed 需随假成功修复一起回 running）
            task = self._fetch_task(conn, step['task_id'])
            revive_from = ('failed', 'completed') if force else ('failed',)
            if task is not None and task['status'] in revive_from:
                conn.execute(
                    f'UPDATE {self._t("ai_task_instance")} '
                    f'SET status = ?, finished_at = NULL, heartbeat_at = ?, updated_at = ? '
                    f'WHERE id = ? AND status = ?',
                    ('running', _iso(now), _iso(now), task['id'], task['status']))
                self._event(conn, task['id'], 'state_change',
                            f'task: {task["status"]} → running (manual retry revive)')
        return True
    # endregion

    # region ======== 执行 ========
    def claim_next_step(
        self,
        task_id: int,
        session_id: str | None = None,
        agent_name: str | None = None,
        ignore_deps: bool = False,
    ) -> dict[str, Any] | None:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        now = datetime.now()
        with self._conn() as conn:
            task = self._fetch_task(conn, task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={task_id}")
            if task['status'] not in ('pending', 'running'):
                log.info("任务 id=%s 状态为 %s，无步骤可认领", task_id, task['status'])
                return None
            # 乐观锁循环：条件 UPDATE 抢占失败（被并发抢走/状态已变）则重选候选
            for _ in range(3):
                cands = self._q(
                    conn, f'SELECT * FROM {t_step} WHERE task_id = ? AND status = ? '
                          f'ORDER BY seq', (task_id, 'pending'))
                if not cands:
                    return None
                # 依赖感知候选选取：任务内任一步骤声明了 depends_on 时启用，
                # 按 seq 升序取第一个依赖就绪的 pending；无声明保持旧行为（seq 最小）
                cand = None
                if not ignore_deps:
                    all_steps = self._q(
                        conn, f'SELECT seq, status, depends_on FROM {t_step} '
                              f'WHERE task_id = ?', (task_id,))
                    dep_aware = any(parse_depends_on(s['depends_on']) for s in all_steps)
                    if dep_aware:
                        status_by_seq = {s['seq']: s['status'] for s in all_steps}
                        cand = next(
                            (c for c in cands
                             if deps_satisfied(parse_depends_on(c['depends_on']),
                                               status_by_seq)), None)
                        if cand is None:
                            log.info("任务 id=%s 的 pending 步骤依赖均未就绪，暂无可认领",
                                     task_id)
                            return None
                if cand is None:
                    cand = cands[0]
                affected = conn.execute(
                    f'UPDATE {t_step} SET status = ?, started_at = COALESCE(started_at, ?), '
                    f'updated_at = ? WHERE id = ? AND status = ?',
                    ('running', _iso(now), _iso(now), cand['id'], 'pending')).rowcount
                if not affected:
                    continue
                # 任务首次 claim：pending → running；否则仅刷心跳
                if task['status'] == 'pending':
                    conn.execute(
                        f'UPDATE {t_task} SET status = ?, started_at = COALESCE(started_at, ?), '
                        f'heartbeat_at = ?, updated_at = ? WHERE id = ? AND status = ?',
                        ('running', _iso(now), _iso(now), _iso(now), task_id, 'pending'))
                    self._event(conn, task_id, 'state_change',
                                'task: pending → running (first claim)')
                else:
                    self._touch_heartbeat(conn, task_id)
                # 续跑上下文包：任务 + 步骤 + run_id + 前序成功步骤摘要
                ctx = self._q(
                    conn, f'SELECT seq, name, result_summary FROM {t_step} '
                          f'WHERE task_id = ? AND status = ? ORDER BY seq',
                    (task_id, 'succeeded'))
                trow = self._task_row(self._q(conn, f'SELECT * FROM {t_task} WHERE id = ?',
                                              (task_id,))[0])
                srow = self._q(conn, f'SELECT * FROM {t_step} WHERE id = ?', (cand['id'],))[0]
                package: dict[str, Any] = {'task': trow, 'step': dict(srow), 'context': ctx}
                cur = conn.execute(
                    f'INSERT INTO {self._t("ai_task_run")} '
                    f'(task_id, step_id, session_id, agent_name, status, input_snapshot, '
                    f'started_at) VALUES (?,?,?,?,?,?,?)',
                    (task_id, cand['id'], session_id, agent_name, 'running',
                     _jdump(package), _iso(now)))
                run_id = self._lastrowid(cur)
                self._event(
                    conn, task_id, 'state_change',
                    f"step {cand['id']}: pending → running "
                    f"(claim by {session_id or 'anonymous'})",
                    step_id=cand['id'], run_id=run_id)
                package['run_id'] = run_id
                return package
        log.warning("claim 连续 3 次抢占失败（task=%s），放弃本次认领", task_id)
        return None

    def finish_run(self, run_id: int, output: str = '', summary: str | None = None,
                   token_usage: int | None = None) -> bool:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        now = datetime.now()
        with self._conn() as conn:
            runs = self._q(conn, f'SELECT * FROM {t_run} WHERE id = ?', (run_id,))
            if not runs:
                log.warning("finish 未找到 run id=%s", run_id)
                return False
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 finish", run_id, run['status'])
                return False
            if summary is None:
                summary = output[:2000] if output else ''
            conn.execute(
                f'UPDATE {t_run} SET status = ?, output = ?, token_usage = ?, finished_at = ? '
                f'WHERE id = ? AND status = ?',
                ('succeeded', output, token_usage, _iso(now), run_id, 'running'))
            conn.execute(
                f'UPDATE {t_step} SET status = ?, result_summary = ?, finished_at = ?, '
                f'updated_at = ? WHERE id = ? AND status = ?',
                ('succeeded', summary, _iso(now), _iso(now), run['step_id'], 'running'))
            self._event(conn, run['task_id'], 'state_change',
                        f'run {run_id}: running → succeeded', step_id=run['step_id'],
                        run_id=run_id)
            self._event(conn, run['task_id'], 'state_change',
                        f"step {run['step_id']}: running → succeeded", step_id=run['step_id'])
            # 收口判定：无 pending/running 且无 failed → 任务完成
            cnt = self._q(
                conn, f'SELECT status, COUNT(*) AS n FROM {t_step} '
                      f'WHERE task_id = ? AND status IN (?,?,?) GROUP BY status',
                (run['task_id'], 'pending', 'running', 'failed'))
            counts = {r['status']: r['n'] for r in cnt}
            if not counts.get('pending') and not counts.get('running') and not counts.get('failed'):
                conn.execute(
                    f'UPDATE {t_task} SET status = ?, finished_at = ?, heartbeat_at = ?, '
                    f'updated_at = ? WHERE id = ? AND status = ?',
                    ('completed', _iso(now), _iso(now), _iso(now), run['task_id'], 'running'))
                self._event(conn, run['task_id'], 'state_change', 'task: running → completed')
            else:
                self._touch_heartbeat(conn, run['task_id'])
        return True

    def fail_run(self, run_id: int, error: str) -> str:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        now = datetime.now()
        with self._conn() as conn:
            runs = self._q(conn, f'SELECT * FROM {t_run} WHERE id = ?', (run_id,))
            if not runs:
                log.warning("fail 未找到 run id=%s", run_id)
                return ''
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 fail", run_id, run['status'])
                return ''
            conn.execute(
                f'UPDATE {t_run} SET status = ?, error_msg = ?, finished_at = ? WHERE id = ?',
                ('failed', error, _iso(now), run_id))
            self._event(conn, run['task_id'], 'error',
                        f'run {run_id} 失败: {error[:500]}', level='warn',
                        step_id=run['step_id'], run_id=run_id)
            self._event(conn, run['task_id'], 'state_change',
                        f'run {run_id}: running → failed', step_id=run['step_id'],
                        run_id=run_id)
            steps = self._q(conn, f'SELECT * FROM {t_step} WHERE id = ?', (run['step_id'],))
            step = steps[0]
            mr = int(step['max_retries']) if step['max_retries'] is not None else 0
            disposition = step_disposition_on_fail(int(step['retry_count']), mr)
            if disposition == 'pending':
                # 预算未耗尽：步骤回 pending（retry_count+1），任务不变，刷心跳
                conn.execute(
                    f'UPDATE {t_step} SET status = ?, retry_count = retry_count + 1, '
                    f'updated_at = ? WHERE id = ? AND status = ?',
                    ('pending', _iso(now), step['id'], 'running'))
                self._event(conn, run['task_id'], 'state_change',
                            f"step {step['id']}: running → pending "
                            f"(retry {step['retry_count'] + 1}/{mr})", step_id=step['id'])
                self._touch_heartbeat(conn, run['task_id'])
            else:
                # 预算耗尽：步骤终败，任务连带失败
                conn.execute(
                    f'UPDATE {t_step} SET status = ?, finished_at = ?, updated_at = ? '
                    f'WHERE id = ? AND status = ?',
                    ('failed', _iso(now), _iso(now), step['id'], 'running'))
                self._event(conn, run['task_id'], 'state_change',
                            f"step {step['id']}: running → failed (预算耗尽)",
                            step_id=step['id'])
                conn.execute(
                    f'UPDATE {t_task} SET status = ?, finished_at = ?, updated_at = ? '
                    f'WHERE id = ? AND status = ?',
                    ('failed', _iso(now), _iso(now), run['task_id'], 'running'))
                self._event(conn, run['task_id'], 'state_change',
                            'task: running → failed (步骤预算耗尽)')
            return 'retried' if disposition == 'pending' else 'step_failed'

    def get_run(self, run_id: int) -> AgentRun | None:
        rows = self._query(f'SELECT * FROM {self._t("ai_task_run")} WHERE id = ?', (run_id,))
        return AgentRun.from_dict(rows[0]) if rows else None

    def list_runs(self, step_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_run")} WHERE step_id = ? ORDER BY id', (step_id,))

    def list_task_runs(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_run")} WHERE task_id = ? ORDER BY id', (task_id,))

    def release_run(self, run_id: int, reason: str = '') -> str:
        now = datetime.now()
        with self._conn() as conn:
            runs = self._q(conn, f'SELECT * FROM {self._t("ai_task_run")} WHERE id = ?', (run_id,))
            if not runs:
                log.warning("release 未找到 run id=%s", run_id)
                return ''
            run = runs[0]
            if run['status'] != 'running':
                log.warning("run id=%s 已终态(%s)，忽略 release", run_id, run['status'])
                return ''
            conn.execute(
                f'UPDATE {self._t("ai_task_run")} SET status = ?, error_msg = ?, finished_at = ? '
                f'WHERE id = ? AND status = ?',
                ('cancelled', 'released' + (f': {reason}' if reason else ''),
                 _iso(now), run_id, 'running'))
            self._event(conn, run['task_id'], 'state_change',
                        f'run {run_id}: running → cancelled '
                        f'(release{": " + reason if reason else ""})',
                        step_id=run['step_id'], run_id=run_id)
            # 步骤还回队列：不加 retry_count（释放 ≠ 失败，不消耗重试预算）
            affected = conn.execute(
                f'UPDATE {self._t("ai_task_step")} SET status = ?, updated_at = ? '
                f'WHERE id = ? AND status = ?',
                ('pending', _iso(now), run['step_id'], 'running')).rowcount
            if affected:
                self._event(conn, run['task_id'], 'state_change',
                            f"step {run['step_id']}: running → pending (released, 预算不变)",
                            step_id=run['step_id'])
            self._touch_heartbeat(conn, run['task_id'])
        return 'released'
    # endregion

    # region ======== 恢复（sweep） ========
    def _reap_step(self, conn: sqlite3.Connection, step: dict[str, Any], reason: str,
                   now: datetime) -> list[dict[str, Any]]:
        """僵尸步骤处理：其 running run 置 timeout，步骤按重试预算回 pending 或终败。

        返回被恢复对象的摘要列表（task 级与 step 级 sweep 共用）。
        """
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        out: list[dict[str, Any]] = []
        runs = self._q(conn, f'SELECT * FROM {t_run} WHERE step_id = ? AND status = ?',
                       (step['id'], 'running'))
        for r in runs:
            conn.execute(
                f'UPDATE {t_run} SET status = ?, error_msg = ?, finished_at = ? '
                f'WHERE id = ? AND status = ?',
                ('timeout', reason, _iso(now), r['id'], 'running'))
            out.append({'task_id': r['task_id'], 'step_id': step['id'], 'run_id': r['id'],
                        'action': 'run_timeout', 'detail': reason})
            self._event(conn, r['task_id'], 'state_change',
                        f"run {r['id']}: running → timeout ({reason})",
                        step_id=step['id'], run_id=r['id'])
        mr = int(step['max_retries']) if step['max_retries'] is not None else 0
        disposition = step_disposition_on_fail(int(step['retry_count']), mr)
        if disposition == 'pending':
            conn.execute(
                f'UPDATE {t_step} SET status = ?, retry_count = retry_count + 1, updated_at = ? '
                f'WHERE id = ? AND status = ?',
                ('pending', _iso(now), step['id'], 'running'))
            out.append({'task_id': step['task_id'], 'step_id': step['id'], 'run_id': None,
                        'action': 'step_retry', 'detail': f'retry {step["retry_count"] + 1}/{mr}'})
            self._event(conn, step['task_id'], 'state_change',
                        f"step {step['id']}: running → pending (sweep retry)", step_id=step['id'])
        else:
            conn.execute(
                f'UPDATE {t_step} SET status = ?, finished_at = ?, updated_at = ? '
                f'WHERE id = ? AND status = ?',
                ('failed', _iso(now), _iso(now), step['id'], 'running'))
            out.append({'task_id': step['task_id'], 'step_id': step['id'], 'run_id': None,
                        'action': 'step_failed', 'detail': reason})
            self._event(conn, step['task_id'], 'state_change',
                        f"step {step['id']}: running → failed (sweep, 预算耗尽)",
                        step_id=step['id'])
            conn.execute(
                f'UPDATE {t_task} SET status = ?, finished_at = ?, updated_at = ? '
                f'WHERE id = ? AND status = ?',
                ('failed', _iso(now), _iso(now), step['task_id'], 'running'))
            self._event(conn, step['task_id'], 'state_change',
                        'task: running → failed (sweep, 步骤预算耗尽)')
        return out

    def _reap_task_timeout(self, conn: sqlite3.Connection, task: dict[str, Any],
                           now: datetime) -> list[dict[str, Any]]:
        """任务总超时终态化：running run 置 timeout、running 步骤置 failed、任务置 failed。

        不走重试预算裁决——任务整体时间预算已尽，重试无意义；
        可用 retry_step 手动复活（预算 +1 且任务回 running）。
        """
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        out: list[dict[str, Any]] = []
        reason = 'task total timeout'
        steps = self._q(conn, f'SELECT * FROM {t_step} WHERE task_id = ? AND status = ?',
                        (task['id'], 'running'))
        for s in steps:
            runs = self._q(conn, f'SELECT * FROM {t_run} WHERE step_id = ? AND status = ?',
                           (s['id'], 'running'))
            for r in runs:
                conn.execute(
                    f'UPDATE {t_run} SET status = ?, error_msg = ?, finished_at = ? '
                    f'WHERE id = ? AND status = ?',
                    ('timeout', reason, _iso(now), r['id'], 'running'))
                out.append({'task_id': task['id'], 'step_id': s['id'], 'run_id': r['id'],
                            'action': 'task_timeout', 'detail': reason})
                self._event(conn, task['id'], 'state_change',
                            f"run {r['id']}: running → timeout ({reason})",
                            step_id=s['id'], run_id=r['id'])
            conn.execute(
                f'UPDATE {t_step} SET status = ?, finished_at = ?, updated_at = ? '
                f'WHERE id = ? AND status = ?',
                ('failed', _iso(now), _iso(now), s['id'], 'running'))
            out.append({'task_id': task['id'], 'step_id': s['id'], 'run_id': None,
                        'action': 'step_failed', 'detail': reason})
            self._event(conn, task['id'], 'state_change',
                        f"step {s['id']}: running → failed ({reason})", step_id=s['id'])
        conn.execute(
            f'UPDATE {t_task} SET status = ?, finished_at = ?, updated_at = ? '
            f'WHERE id = ? AND status = ?',
            ('failed', _iso(now), _iso(now), task['id'], 'running'))
        out.append({'task_id': task['id'], 'step_id': None, 'run_id': None,
                    'action': 'task_failed', 'detail': reason})
        self._event(conn, task['id'], 'state_change',
                    f"task {task['id']}: running → failed (任务总超时)")
        return out

    def sweep(self, heartbeat_timeout_sec: int | None = None) -> list[dict[str, Any]]:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        results: list[dict[str, Any]] = []
        now = datetime.now()
        with self._conn() as conn:
            # ⓪ 任务级：总超时（timeout_sec 非空且 started_at 距今超过它）——任务连同
            # running 步骤直接终败，不走预算裁决。须先于心跳检测执行。
            timed_out = self._q(
                conn, f'SELECT * FROM {t_task} WHERE status = ? AND timeout_sec IS NOT NULL',
                ('running',))
            for t in timed_out:
                if not t['started_at']:
                    continue
                threshold = (now - timedelta(seconds=int(t['timeout_sec']))).isoformat()
                if t['started_at'] >= threshold:
                    continue
                results.extend(self._reap_task_timeout(conn, t, now))
            # ① 任务级：心跳超时的 running 任务，其 running 步骤按僵尸处理
            running_tasks = self._q(conn, f'SELECT * FROM {t_task} WHERE status = ?',
                                    ('running',))
            for t in running_tasks:
                if not t['heartbeat_at']:
                    continue
                timeout = (heartbeat_timeout_sec if heartbeat_timeout_sec is not None
                           else int(t['heartbeat_timeout_sec'])
                           if t['heartbeat_timeout_sec'] is not None else 1800)
                threshold = (now - timedelta(seconds=timeout)).isoformat()
                if t['heartbeat_at'] >= threshold:
                    continue
                zombie_steps = self._q(
                    conn, f'SELECT * FROM {t_step} WHERE task_id = ? AND status = ?',
                    (t['id'], 'running'))
                for s in zombie_steps:
                    results.extend(self._reap_step(conn, s, 'heartbeat timeout', now))
            # ② 步骤级：任务心跳正常，但 run.started_at 超过 step.timeout_sec（单步卡死）。
            # SQLite 不支持按列计算 INTERVAL，先缩小候选集，再在 Python 侧逐行判超时。
            candidates = self._q(
                conn, f'SELECT s.* FROM {t_step} s JOIN {t_run} r ON r.step_id = s.id '
                      f'WHERE r.status = ? AND s.timeout_sec IS NOT NULL AND s.status = ?',
                ('running', 'running'))
            for s in candidates:
                threshold = (now - timedelta(seconds=int(s['timeout_sec']))).isoformat()
                live = self._q(
                    conn, f'SELECT * FROM {t_run} WHERE step_id = ? AND status = ? '
                          f'AND started_at IS NOT NULL AND started_at < ?',
                    (s['id'], 'running', threshold))
                if live:
                    results.extend(self._reap_step(conn, s, 'step timeout', now))
            if results:
                log.info("sweep 恢复了 %d 个对象", len(results))
        return results

    def verify_task(self, task_id: int, fix: bool = False) -> list[dict[str, Any]]:
        t_task = self._t('ai_task_instance')
        t_step = self._t('ai_task_step')
        t_run = self._t('ai_task_run')
        t_event = self._t('ai_task_event')
        now = datetime.now()
        findings: list[dict[str, Any]] = []
        with self._conn() as conn:
            task = self._fetch_task(conn, task_id)
            if task is None:
                raise ValueError(f"任务不存在: id={task_id}")
            steps = self._q(conn, f'SELECT * FROM {t_step} WHERE task_id = ? ORDER BY seq',
                            (task_id,))
            runs = self._q(conn, f'SELECT * FROM {t_run} WHERE task_id = ?', (task_id,))
            events = self._q(conn, f'SELECT message FROM {t_event} WHERE task_id = ?',
                             (task_id,))
            event_msgs = [e['message'] for e in events]
            runs_by_step: dict[int, list[dict[str, Any]]] = {}
            for r in runs:
                runs_by_step.setdefault(r['step_id'], []).append(r)
            step_by_id = {s['id']: s for s in steps}

            def has_event(sid: int, fragment: str) -> bool:
                return any(m.startswith(f'step {sid}: ') and fragment in m
                           for m in event_msgs)

            def running_runs(sid: int) -> list[dict[str, Any]]:
                return [r for r in runs_by_step.get(sid, []) if r['status'] == 'running']

            for s in steps:
                sid = s['id']
                if s['status'] == 'succeeded':
                    # V1 假成功：真完成须同时有 succeeded run 与 finish 事件（SQL 直刷
                    # 不产生二者）。修复取向：有活 run 接管为 running，否则回 pending。
                    ok_run = any(r['status'] == 'succeeded' for r in runs_by_step.get(sid, []))
                    ok_event = has_event(sid, 'running → succeeded')
                    if ok_run and ok_event:
                        continue
                    detail = ('假成功（缺 succeeded run' +
                              ('' if ok_event and ok_run else
                               '与 finish 事件' if ok_run else '，缺 finish 事件' if ok_event
                               else '与 finish 事件') + '）')
                    finding = {'rule': 'V1', 'level': 'error', 'kind': 'step', 'id': sid,
                               'detail': detail, 'fixed': None}
                    findings.append(finding)
                    if fix:
                        live = running_runs(sid)
                        if live:
                            conn.execute(
                                f'UPDATE {t_step} SET status = ?, result_summary = NULL, '
                                f'updated_at = ? WHERE id = ? AND status = ?',
                                ('running', _iso(now), sid, 'succeeded'))
                            self._event(conn, task_id, 'state_change',
                                        f'step {sid}: succeeded → running '
                                        f'(verify fix, 接管活 run)', step_id=sid)
                        else:
                            conn.execute(
                                f'UPDATE {t_step} SET status = ?, result_summary = NULL, '
                                f'finished_at = NULL, updated_at = ? '
                                f'WHERE id = ? AND status = ?',
                                ('pending', _iso(now), sid, 'succeeded'))
                            self._event(conn, task_id, 'state_change',
                                        f'step {sid}: succeeded → pending '
                                        f'(verify fix, 清假摘要)', step_id=sid)
                        finding['fixed'] = True
                elif s['status'] == 'running':
                    # V2 僵尸步骤：状态在跑但没有任何 running run 支撑
                    if not running_runs(sid):
                        finding = {'rule': 'V2', 'level': 'error', 'kind': 'step', 'id': sid,
                                   'detail': 'running 但无任何 running run（无执行支撑）',
                                   'fixed': None}
                        findings.append(finding)
                        if fix:
                            conn.execute(
                                f'UPDATE {t_step} SET status = ?, updated_at = ? '
                                f'WHERE id = ? AND status = ?',
                                ('pending', _iso(now), sid, 'running'))
                            self._event(conn, task_id, 'state_change',
                                        f'step {sid}: running → pending '
                                        f'(verify fix, 清无支撑的 running)', step_id=sid)
                            finding['fixed'] = True
                elif s['status'] == 'skipped':
                    # V6 无 skip 事件的 skipped：来源不明（仅告警，不动状态）
                    if not has_event(sid, '→ skipped'):
                        findings.append({'rule': 'V6', 'level': 'info', 'kind': 'step',
                                         'id': sid, 'detail': 'skipped 但无 skip 事件（来源不明）',
                                         'fixed': None})
            # V1/V2 修复会改动步骤状态，V3/V4/V5 须基于**修复后**的最新状态判定——重查
            steps = self._q(conn, f'SELECT * FROM {t_step} WHERE task_id = ? ORDER BY seq',
                            (task_id,))
            step_by_id = {s['id']: s for s in steps}
            # V3 孤儿 run：仍在 running 但所属步骤已不在 running
            for r in runs:
                if r['status'] != 'running':
                    continue
                st = step_by_id.get(r['step_id'])
                if st is not None and st['status'] == 'running':
                    continue
                finding = {'rule': 'V3', 'level': 'warn', 'kind': 'run', 'id': r['id'],
                           'detail': f'run 仍在 running，但所属步骤状态为 '
                                     f'{st["status"] if st else "不存在"}',
                           'fixed': None}
                findings.append(finding)
                if fix:
                    conn.execute(
                        f'UPDATE {t_run} SET status = ?, error_msg = ?, finished_at = ? '
                        f'WHERE id = ? AND status = ?',
                        ('cancelled', 'verify: 孤儿 run', _iso(now), r['id'], 'running'))
                    self._event(conn, task_id, 'state_change',
                                f'run {r["id"]}: running → cancelled (verify fix, 孤儿 run)',
                                step_id=r['step_id'], run_id=r['id'])
                    finding['fixed'] = True
            # V4 伪造指纹：≥3 个步骤共享同一段非空 result_summary（直刷脚本常粘贴同一段话）
            counter: dict[str, int] = {}
            for s in steps:
                if s['result_summary']:
                    counter[s['result_summary']] = counter.get(s['result_summary'], 0) + 1
            for summ, n in counter.items():
                if n >= 3:
                    findings.append({'rule': 'V4', 'level': 'warn', 'kind': 'task', 'id': task_id,
                                     'detail': f'{n} 个步骤共享同一 result_summary（伪造指纹）'
                                               f'：{summ[:80]}', 'fixed': None})
            # V5 收口失真：任务 completed 但存在未终态步骤
            if task['status'] == 'completed':
                bad = [s['id'] for s in steps if s['status'] in ('pending', 'running', 'failed')]
                if bad:
                    finding = {'rule': 'V5', 'level': 'error', 'kind': 'task', 'id': task_id,
                               'detail': f'task completed 但存在未终态步骤: {bad}', 'fixed': None}
                    findings.append(finding)
                    if fix:
                        conn.execute(
                            f'UPDATE {t_task} SET status = ?, finished_at = NULL, '
                            f'updated_at = ? WHERE id = ? AND status = ?',
                            ('running', _iso(now), task_id, 'completed'))
                        self._event(conn, task_id, 'state_change',
                                    'task: completed → running (verify fix, 收口失真回退)')
                        finding['fixed'] = True
            if fix and any(f['fixed'] for f in findings):
                n_fixed = sum(1 for f in findings if f['fixed'])
                self._event(conn, task_id, 'note', f'verify fix: 修复 {n_fixed} 处不一致',
                            level='warn')
                self._touch_heartbeat(conn, task_id)
        if findings:
            log.info("verify task=%s 发现 %d 处异常%s", task_id, len(findings),
                     '，已修复' if fix else '')
        return findings
    # endregion

    # region ======== 产物 / 事件 ========
    def add_artifact(
        self,
        task_id: int,
        art_type: str,
        path: str,
        step_id: int | None = None,
        note: str | None = None,
    ) -> int:
        if art_type not in VALID_ART_TYPES:
            raise ValueError(f"非法的 art_type: {art_type!r}（合法值: {sorted(VALID_ART_TYPES)}）")
        with self._conn() as conn:
            cur = conn.execute(
                f'INSERT INTO {self._t("ai_task_artifact")} '
                f'(task_id, step_id, art_type, path, note, created_at) VALUES (?,?,?,?,?,?)',
                (task_id, step_id, art_type, path, note, _iso(datetime.now())))
            art_id = self._lastrowid(cur)
            self._event(conn, task_id, 'artifact', f'产物登记: {path} ({art_type})',
                        step_id=step_id)
        return art_id

    def list_artifacts(self, task_id: int) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_artifact")} WHERE task_id = ? ORDER BY id',
            (task_id,))

    def add_event(
        self,
        task_id: int,
        event_type: str,
        message: str,
        level: str = 'info',
        step_id: int | None = None,
        run_id: int | None = None,
    ) -> int:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(
                f"非法的 event_type: {event_type!r}（合法值: {sorted(VALID_EVENT_TYPES)}）")
        if level not in VALID_EVENT_LEVELS:
            raise ValueError(f"非法的 level: {level!r}（合法值: {sorted(VALID_EVENT_LEVELS)}）")
        with self._conn() as conn:
            cur = conn.execute(
                f'INSERT INTO {self._t("ai_task_event")} '
                f'(task_id, step_id, run_id, event_type, level, message, created_at) '
                f'VALUES (?,?,?,?,?,?,?)',
                (task_id, step_id, run_id, event_type, level, message, _iso(datetime.now())))
            return self._lastrowid(cur)

    def list_events(self, task_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return self._query(
            f'SELECT * FROM {self._t("ai_task_event")} WHERE task_id = ? '
            f'ORDER BY id DESC LIMIT ?', (task_id, limit))
    # endregion

    # region ======== 模板 ========
    def create_template(self, t: TaskTemplate) -> int:
        now = datetime.now()
        with self._conn() as conn:
            dup = self._q(conn, f'SELECT id FROM {self._t("ai_task_template")} WHERE name = ?',
                          (t.name,))
            if dup:
                raise ValueError(f"模板名已存在: {t.name!r} (id={dup[0]['id']})")
            cur = conn.execute(
                f'INSERT INTO {self._t("ai_task_template")} '
                f'(name, skill_ref, description, default_params, step_blueprint, '
                f'created_at, updated_at) VALUES (?,?,?,?,?,?,?)',
                (t.name, t.skill_ref, t.description, _jdump(t.default_params),
                 _jdump(t.step_blueprint), _iso(now), _iso(now)))
            t.id = self._lastrowid(cur)
            return t.id

    def get_template_by_name(self, template_name: str) -> TaskTemplate | None:
        rows = self._query(
            f'SELECT * FROM {self._t("ai_task_template")} WHERE name = ?', (template_name,))
        return TaskTemplate.from_dict(self._template_row(rows[0])) if rows else None

    def list_templates(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._query(
            f'SELECT * FROM {self._t("ai_task_template")} ORDER BY id DESC LIMIT ?', (limit,))
        return [self._template_row(r) for r in rows]
    # endregion

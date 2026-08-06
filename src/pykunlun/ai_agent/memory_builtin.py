"""
AI Agent 记忆能力的内置实现（基于 Python 标准库 sqlite3，无第三方依赖）。

对标 :mod:`pykunlun.db.rdb_builtin`：随 :class:`~pykunlun.ai_agent.memory.MemoryManager`
开箱即用，提供：

  - :class:`SqliteMemoryStore`：基于 SQLite 文件的记忆存储，单文件持久化、
    跨进程可读、重启不丢失；仅依赖标准库 ``sqlite3``，无需安装数据库服务。

适用于无需配置 rdb 的轻量部署、单测、快速起步。需要 MySQL/PostgreSQL，或想复用
已注册的 rdb 实例时，使用上层包的 ``RdbMemoryStore``。
"""

import sqlite3
from datetime import datetime
from typing import Any

from pykunlun.util import logutil

from .memory import (
    UPDATABLE_FIELDS,
    MemoryRecord,
    MemoryStore,
    permission_clause,
    tokenize_query,
    visibility_clause,
)

log = logutil.getLogger(__name__)


def _iso(dt) -> str | None:
    """datetime → ISO 字符串（None 直通）。

    Python 3.12 起默认 datetime→SQLite 适配器已弃用，故显式序列化为 ISO 字符串；
    ISO 格式按字典序即为时间序，便于 ``ORDER BY last_used_at``。
    """
    return dt.isoformat() if dt is not None else None


class SqliteMemoryStore(MemoryStore):
    """
    基于 SQLite 的记忆存储（标准库 sqlite3，单文件持久化）。

    数据写入由 ``db_path`` 指定的 SQLite 文件，跨进程可读、重启不丢失。
    占位符为 ``?``，全程参数化，防 SQL 注入。每次操作建立并关闭连接
    （connect-per-call，对标 :class:`pykunlun.db.SqliteClient`）。

    Args:
        db_path: SQLite 数据库文件路径。**不支持** ``':memory:'``——connect-per-call
            下每次连接彼此隔离、数据不跨连接留存；测试请使用临时文件。
        owner: 当前身份（用户标识）；None 表示无身份。正常角色下仅可读/写自己的 + 读共享。
        owner_group: 当前团队/组（标签，仅用于 remember 盖章，不参与鉴权）。
        table: 记忆表名（默认 ``ai_memory``）。
    """

    backend_type = 'sqlite'

    def __init__(
        self,
        db_path: str,
        owner: str | None = None,
        owner_group: str | None = None,
        table: str = 'ai_memory',
    ) -> None:
        if db_path == ':memory:':
            raise ValueError(
                "SqliteMemoryStore 不支持 ':memory:'（connect-per-call 下数据不跨连接留存）；"
                "请使用文件路径（测试可用 tempfile）"
            )
        self._db_path = db_path
        self._owner = owner if (owner is not None and owner.strip()) else None
        self._owner_group = owner_group
        self._table = table

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def owner_group(self) -> str | None:
        return self._owner_group

    @property
    def table(self) -> str:
        return self._table

    # region ======== 连接与执行 ========

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row  # type: ignore[assignment]
        return conn

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()

    # endregion

    # region ======== 建表（幂等）======

    def init_store(self) -> None:
        t = self._table
        self._execute(f"""CREATE TABLE IF NOT EXISTS {t} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT,
            owner TEXT,
            owner_group TEXT,
            keywords TEXT DEFAULT '',
            source TEXT DEFAULT 'user-told',
            confidence INTEGER DEFAULT 80,
            pinned INTEGER DEFAULT 0,
            use_count INTEGER DEFAULT 0,
            last_used_at TEXT,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )""")
        self._execute(f'CREATE INDEX IF NOT EXISTS idx_{t}_scope_del ON {t} (scope, is_deleted)')
        self._execute(f'CREATE INDEX IF NOT EXISTS idx_{t}_scope_cat ON {t} (scope, category)')
        self._execute(f'CREATE INDEX IF NOT EXISTS idx_{t}_owner_del ON {t} (owner, is_deleted)')
        log.info("SqliteMemoryStore 已初始化表 %s (db=%s)", t, self._db_path)

    # endregion

    # region ======== 写操作 ========

    def remember(self, record: MemoryRecord, shared_mode: bool = False) -> int:
        now = datetime.now()
        t = self._table
        # owner_group：盖当前组（标签），除非 record 显式给了
        if record.owner_group is None:
            record.owner_group = self._owner_group
        # owner：共享角色置空（→ 共享记忆）；正常角色盖当前身份
        if shared_mode:
            record.owner = None
        elif record.owner is None:
            record.owner = self._owner
        sql = (f'INSERT INTO {t} (scope, category, title, content, owner, owner_group, '
               f'keywords, source, confidence, pinned, use_count, last_used_at, is_deleted, '
               f'created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
        params = (
            record.scope, record.category, record.title, record.content,
            record.owner, record.owner_group, record.keywords, record.source,
            record.confidence, record.pinned, record.use_count,
            _iso(record.last_used_at or now), record.is_deleted, _iso(now), _iso(now),
        )
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            lastrowid = cur.lastrowid
            if lastrowid is None:
                raise RuntimeError("INSERT 未返回 lastrowid（非预期，请检查表结构）")
            return int(lastrowid)
        finally:
            conn.close()

    def update(self, id: int, fields: dict[str, Any], shared_mode: bool = False) -> bool:
        allowed = {k: v for k, v in fields.items() if k in UPDATABLE_FIELDS}
        if not allowed:
            return False
        perm_sql, perm_params = permission_clause(shared_mode, self._owner, '?')
        set_parts = [f'{k} = ?' for k in allowed]
        params: list[Any] = list(allowed.values())
        set_parts.append('updated_at = ?')
        params.append(_iso(datetime.now()))
        # WHERE id = ? ... {perm_sql}：id 在前、perm 参数在后
        params.append(id)
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET {", ".join(set_parts)} '
               f'WHERE id = ? AND is_deleted = 0 AND {perm_sql}')
        return self._execute(sql, tuple(params)) > 0

    def forget(self, id: int, shared_mode: bool = False) -> bool:
        perm_sql, perm_params = permission_clause(shared_mode, self._owner, '?')
        params: list[Any] = [_iso(datetime.now()), id]
        params.extend(perm_params)
        sql = (f'UPDATE {self._table} SET is_deleted = 1, updated_at = ? '
               f'WHERE id = ? AND is_deleted = 0 AND {perm_sql}')
        return self._execute(sql, tuple(params)) > 0

    def touch(self, id: int) -> None:
        # recall 命中副作用：仅对已召回（即可见）的 id 累加，不做鉴权
        sql = (f'UPDATE {self._table} SET use_count = use_count + 1, last_used_at = ? '
               f'WHERE id = ?')
        self._execute(sql, (_iso(datetime.now()), id))

    # endregion

    # region ======== 读操作 ========

    def get(self, id: int, shared_mode: bool = False) -> MemoryRecord | None:
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, '?')
        params: list[Any] = [id]
        params.extend(vis_params)
        sql = (f'SELECT * FROM {self._table} '
               f'WHERE id = ? AND is_deleted = 0 AND {vis_sql}')
        rows = self._query(sql, tuple(params))
        return MemoryRecord.from_dict(rows[0]) if rows else None

    def find_by_scope_title(
        self,
        scope: str,
        title: str,
        include_deleted: bool = False,
        shared_mode: bool = False,
    ) -> list[MemoryRecord]:
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, '?')
        params: list[Any] = [scope, title]
        params.extend(vis_params)
        where = f'scope = ? AND title = ? AND {vis_sql}'
        if not include_deleted:
            where += ' AND is_deleted = 0'
        rows = self._query(f'SELECT * FROM {self._table} WHERE {where}', tuple(params))
        return [MemoryRecord.from_dict(r) for r in rows]

    def count(self, include_deleted: bool = False, shared_mode: bool = False) -> int:
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, '?')
        params: list[Any] = list(vis_params)
        clauses = [vis_sql]
        if not include_deleted:
            clauses.append('is_deleted = 0')
        where = ' AND '.join(clauses)
        rows = self._query(f'SELECT COUNT(*) AS cnt FROM {self._table} WHERE {where}', tuple(params))
        return int(rows[0]['cnt']) if rows else 0

    def recall(
        self,
        query: str,
        scope: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_deleted: bool = False,
        shared_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """模糊检索（参数化 ``?``，多关键词多字段加权，按角色过滤可见范围）。"""
        tokens = tokenize_query(query)
        params: list[Any] = []

        if tokens:
            score_terms: list[str] = []
            for tk in tokens:
                score_terms.append(
                    '(CASE WHEN title LIKE ? THEN 3 ELSE 0 END'
                    ' + CASE WHEN keywords LIKE ? THEN 2 ELSE 0 END'
                    ' + CASE WHEN content LIKE ? THEN 1 ELSE 0 END)'
                )
                like = f'%{tk}%'
                params.extend([like, like, like])
            score_expr = '(' + ' + '.join(score_terms) + ')'
        else:
            score_expr = '0'

        where_parts: list[str] = []
        if tokens:
            or_groups: list[str] = []
            for tk in tokens:
                or_groups.append('(title LIKE ? OR keywords LIKE ? OR content LIKE ?)')
                like = f'%{tk}%'
                params.extend([like, like, like])
            where_parts.append('(' + ' OR '.join(or_groups) + ')')
        if not include_deleted:
            where_parts.append('is_deleted = 0')
        # 可见性（按角色）
        vis_sql, vis_params = visibility_clause(shared_mode, self._owner, '?')
        where_parts.append(vis_sql)
        params.extend(vis_params)
        if scope is not None:
            where_parts.append('scope = ?')
            params.append(scope)
        if category is not None:
            where_parts.append('category = ?')
            params.append(category)
        where_clause = ' AND '.join(where_parts)

        sql = (f'SELECT *, {score_expr} AS _score FROM {self._table}'
               f' WHERE {where_clause}'
               f' ORDER BY pinned DESC, _score DESC, last_used_at DESC'
               f' LIMIT ?')
        params.append(limit)
        return self._query(sql, tuple(params))

    # endregion

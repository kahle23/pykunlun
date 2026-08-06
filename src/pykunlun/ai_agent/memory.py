"""
AI Agent 记忆能力抽象层。

采用策略模式（同 :mod:`pykunlun.db.rdb`）：

  - :class:`MemoryRecord` 为一条记忆的数据类，字段对齐上层 RDB 实现的 ``ai_memory`` 表；
  - :class:`MemoryStore` 为存储策略抽象基类，定义跨后端的统一接口
    （remember / recall / update / forget 等）；
  - :class:`MemoryManager` 维护 ``name -> MemoryStore`` 注册表，对外转发调用，
    并在 recall 命中后自动累加命中计数（``touch``）作为副作用。

仅依赖 Python 标准库与 pykunlun 自身工具模块。具体后端实现（如基于 rdb 的
``RdbMemoryStore``）由上层包提供；基于标准库的简单实现（如
:class:`~pykunlun.ai_agent.memory_builtin.DictMemoryStore`）可直接置于本包内。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pykunlun.util import logutil

log = logutil.getLogger(__name__)


# region ======== 记忆记录 ========

#: update() 允许修改的字段白名单（除 id/计数/软删除标记/时间戳外的业务字段）
UPDATABLE_FIELDS: frozenset[str] = frozenset({
    'scope', 'category', 'title', 'content', 'keywords',
    'source', 'confidence', 'pinned',
})

#: 合法的 source 取值
VALID_SOURCES: frozenset[str] = frozenset({'user-told', 'code-derived', 'inferred'})

#: 合法的 category 取值（仅作建议性校验依据，不强制穷举）
VALID_CATEGORIES: frozenset[str] = frozenset({
    'file-path', 'convention', 'decision', 'quirk', 'no-go', 'history', 'other',
})

#: recall 分词时的停用词（中英文虚词/高频噪声词）
_STOPWORDS: frozenset[str] = frozenset({
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
    '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有',
    'the', 'a', 'an', 'of', 'to', 'in', 'on', 'and', 'or', 'for', 'is', 'are',
})


# region ======== 查询分词 ========

def tokenize_query(query: str) -> list[str]:
    r"""
    将查询文本拆分为关键词列表（用于 recall 的多关键词 OR+LIKE 检索）。

    策略：按空格与常见标点（中英文）切分，去停用词与空串，全部小写。
    中文连续字符作为一个 token（无分词器依赖）；空查询返回空列表。

    示例::

        >>> tokenize_query('th-core-web 路由 权限')
        ['th', 'core', 'web', '路由', '权限']
        >>> tokenize_query('')
        []
    """
    import re
    if not query:
        return []
    raw = re.split(r'[\s,.;:!?，。；：！？、/\\|()\[\]{}"\'<>\-]+', query)
    tokens: list[str] = []
    for t in raw:
        t = t.strip().lower()
        if t and t not in _STOPWORDS:
            tokens.append(t)
    return tokens

# endregion


# region ======== 所有权鉴权子句 ========
#
# 鉴权模型（仅认 owner，owner_group 为标签不参与鉴权）：
#   - 正常角色（shared_mode=False，owner 有值）：读 = 自己的 + 共享；写 = 仅自己的。
#   - 共享角色（shared_mode=True）：owner 被忽略；读/写 = 仅共享（owner IS NULL OR owner = ''）。
#   - 无身份（owner 为 None 或空白）：天然处于共享域，读/写 = 仅共享。
# 共享数据（owner IS NULL 或 owner = ''）在正常角色下人人可读；改/删共享数据须切到共享角色。
# 无论哪种情况，别人的个人数据（owner = 他）始终既不可见、也不可改。

def _normalize_owner(owner: str | None) -> str | None:
    """空字符串/纯空白 → None（视为无身份/共享域）。"""
    if owner is not None and not owner.strip():
        return None
    return owner


def visibility_clause(shared_mode: bool, owner: str | None, ph: str) -> tuple[str, list[Any]]:
    """
    读可见性 SQL 片段（不含 is_deleted 等），返回 ``(sql, params)``。

    - 共享角色或无身份（owner 为 None/空白）：仅共享 ``owner IS NULL OR owner = ''``
    - 正常角色：自己的 + 共享 ``owner = :ph OR owner IS NULL OR owner = ''``

    Args:
        shared_mode: 是否为共享角色。
        owner: 当前身份（用户标识）；None/空白 表示无身份。
        ph: SQL 占位符（sqlite 用 ``?``、mysql/postgres 用 ``%s``）。
    """
    owner = _normalize_owner(owner)
    if shared_mode or owner is None:
        return f'(owner IS NULL OR owner = {ph})', ['']
    return f'(owner = {ph} OR owner IS NULL OR owner = {ph})', [owner, '']


def permission_clause(shared_mode: bool, owner: str | None, ph: str) -> tuple[str, list[Any]]:
    """
    写权限 SQL 片段，返回 ``(sql, params)``。

    - 共享角色或无身份（owner 为 None/空白）：仅共享 ``owner IS NULL OR owner = ''``
    - 正常角色（owner 有值）：仅自己的 ``owner = :ph``
    """
    owner = _normalize_owner(owner)
    if shared_mode or owner is None:
        return f'(owner IS NULL OR owner = {ph})', ['']
    return f'owner = {ph}', [owner]

# endregion


@dataclass
class MemoryRecord:
    """
    一条 AI 记忆。

    字段与上层 RDB 实现（``ai_memory`` 表）一一对应。新建记忆时 ``id`` 留空，
    由 :meth:`MemoryStore.remember` 插入后回填并返回。

    Attributes:
        id: 主键；新建时为 None，插入后由存储回填。
        scope: 作用域，通常为项目名/模块名（如 ``th-core-web``），便于按项目隔离。
        category: 事实类型，见 :data:`VALID_CATEGORIES`（file-path/convention/decision 等）。
        title: 一句话摘要，模糊检索的主命中点。
        content: 完整内容。
        owner: 所有者（用户标识）。**鉴权字段**——有人则仅本人可改（正常角色下仅本人可见自己的）；
            无人（None）则为共享数据。由 :meth:`MemoryStore.remember` 按角色盖章。
        owner_group: 所有者所属团队/组（标签字段，**不参与鉴权**），用于团队归类与筛选。
            例如共享记忆可标 ``owner=None, owner_group='backend'`` 表示 backend 团队的共享池。
        keywords: 逗号分隔的关键词/标签，用于模糊检索与加权。
        source: 来源（user-told / code-derived / inferred），见 :data:`VALID_SOURCES`。
        confidence: 置信度 0~100，默认 80。
        pinned: 是否置顶（1/0）；置顶项召回时永远排在最前，默认 0。
        use_count: 被命中次数，用于排序衰减，默认 0。
        last_used_at: 最近一次命中时间；新建时为 None。
        is_deleted: 软删除标记（1/0），默认 0；forget 仅置 1，不物理删除。
        created_at: 创建时间；新建时为 None，由存储回填。
        updated_at: 更新时间；新建时为 None，由存储回填。
    """

    scope: str
    category: str
    title: str
    content: str
    owner: str | None = None
    owner_group: str | None = None
    keywords: str = ''
    source: str = 'user-told'
    confidence: int = 80
    pinned: int = 0
    id: int | None = field(default=None)
    use_count: int = 0
    last_used_at: datetime | None = None
    is_deleted: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """转为字典（含全部字段，便于序列化输出）。"""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'MemoryRecord':
        """从字典构造，忽略未知键，便于从行字典还原。"""
        import dataclasses
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

# endregion


# region ======== 存储策略抽象基类 ========

class MemoryStore(ABC):
    """
    记忆存储策略抽象基类。

    各后端实现（sqlite / rdb / 未来 vector 等）继承本类，对外提供统一的高层操作
    记住/回忆/更新/遗忘（remember / recall / update / forget）。
    调用方无需关心底层存储——SQL 由各实现内部封装，对调用方透明。

    **所有权与角色**：实现需在构造时绑定当前身份 ``owner``（及标签 ``owner_group``），
    并接受 ``shared_mode`` 切换「正常角色 / 共享角色」。读写鉴权由模块级函数
    :func:`visibility_clause` / :func:`permission_clause` 统一描述，各实现复用之，
    避免策略漂移。详见这两个函数的说明。
    """

    @property
    @abstractmethod
    def backend_type(self) -> str:
        """本实现的后端类型标识（如 'sqlite'、'rdb'），对标 RdbClient.db_type。"""

    @property
    def owner(self) -> str | None:
        """当前绑定的身份（用户标识）；None 表示无身份。"""
        return None

    @property
    def owner_group(self) -> str | None:
        """当前绑定的团队/组（标签）；None 表示未设置。"""
        return None

    @abstractmethod
    def init_store(self) -> None:
        """初始化存储（幂等）：RDB 后端建表，内存后端为 noop。首次使用前调用。"""

    @abstractmethod
    def remember(self, record: MemoryRecord, shared_mode: bool = False) -> int:
        """
        插入一条记忆。

        - 正常角色：盖 ``owner`` = 当前身份，``owner_group`` = 当前组（标签）。
        - 共享角色：``owner`` 置空（→ 共享记忆），``owner_group`` 仍盖当前组。

        Args:
            record: 待记的记忆（id 通常为 None；owner/owner_group 留空时由本方法盖章）。
            shared_mode: 是否为共享角色（共享角色下生成共享记忆）。

        Returns:
            新插入记录的主键 id。
        """

    @abstractmethod
    def recall(
        self,
        query: str,
        scope: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_deleted: bool = False,
        shared_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """
        模糊检索相关记忆（按 :func:`visibility_clause` 过滤当前角色可见范围）。

        底层无向量语义，采用「多关键词 × 多字段 OR+LIKE + 标签加权 + 置顶优先」
        的近似策略。命中排序：pinned DESC → 相关度 DESC → last_used_at DESC。

        Args:
            query: 查询文本（自动分词）。
            scope: 限定作用域；None 表示不限。
            category: 限定事实类型；None 表示不限。
            limit: 最多返回条数（默认 20）。
            include_deleted: 是否包含软删除项（默认 False）。
            shared_mode: 是否为共享角色（仅查共享数据）。

        Returns:
            字典列表，每个字典含记录全部字段并附加 ``_score`` 相关度分。
            本方法**不**更新命中计数——计数副作用由 :meth:`MemoryManager.recall`
            统一在查询后通过 :meth:`touch` 落实。
        """

    @abstractmethod
    def get(self, id: int, shared_mode: bool = False) -> MemoryRecord | None:
        """按 id 取单条（须在当前角色可见范围内；不可见或已软删除返回 None）。"""

    @abstractmethod
    def find_by_scope_title(
        self,
        scope: str,
        title: str,
        include_deleted: bool = False,
        shared_mode: bool = False,
    ) -> list[MemoryRecord]:
        """按 scope+title 精确查找（在当前角色可见范围内；remember 去重用）。"""

    @abstractmethod
    def update(self, id: int, fields: dict[str, Any], shared_mode: bool = False) -> bool:
        """
        按 id 部分更新（须通过 :func:`permission_clause` 权限校验）。

        - 正常角色（owner 有值）：仅可改自己的。
        - 共享角色或无身份：仅可改共享数据（owner IS NULL）。
        - 别人的个人数据始终不可改。

        Args:
            id: 记录主键。
            fields: 待更新字段（仅 :data:`UPDATABLE_FIELDS` 白名单生效）。
            shared_mode: 是否为共享角色。

        Returns:
            是否命中并更新（id 不存在或无权限返回 False）。
        """

    @abstractmethod
    def forget(self, id: int, shared_mode: bool = False) -> bool:
        """软删除（is_deleted=1），权限规则同 :meth:`update`。返回是否命中。"""

    @abstractmethod
    def touch(self, id: int) -> None:
        """命中计数 +1、刷新 last_used_at（recall 命中副作用；按 id 原子累加，不做鉴权）。"""

    @abstractmethod
    def count(self, include_deleted: bool = False, shared_mode: bool = False) -> int:
        """返回当前角色可见范围内的记录总数（默认排除软删除）。"""

# endregion


# region ======== 管理器 ========

class MemoryManager:
    """
    记忆存储管理器。

    维护 ``name -> MemoryStore`` 注册表，按名称（别名）管理各后端实例，
    对外转发调用。对标 :class:`pykunlun.db.rdb.RdbManager` 的注册/转发范式。

    典型用法::

        from pykunlun.ai_agent import MemoryManager, MemoryRecord

        mgr = MemoryManager()
        mgr.register('default', DictMemoryStore())
        mgr.remember(MemoryRecord(scope='app', category='decision',
                                  title='用 Hutool', content='...'))
        hits = mgr.recall('Hutool 工具', scope='app')

    recall 在透传 :meth:`MemoryStore.recall` 之上叠加「命中计数」副作用：
    对每条命中行调用 :meth:`MemoryStore.touch`，实现 use_count 累加与
    last_used_at 刷新，体现「越用越靠前」的排序衰减。
    """

    #: 默认实例名称（省略 name 时使用）
    DEFAULT_NAME = 'default'

    def __init__(self) -> None:
        self._stores: dict[str, MemoryStore] = {}

    def _resolve_name(self, name: str | None) -> str:
        """将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。"""
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, store: MemoryStore) -> None:
        """
        注册一个记忆存储实例。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            store: :class:`MemoryStore` 实例。
        """
        key = self._resolve_name(name)
        self._stores[key] = store
        log.debug("已注册记忆存储实例: %s (backend=%s)", key, store.backend_type)

    def unregister(self, name: str | None = None) -> bool:
        """注销实例；返回是否曾存在。"""
        key = self._resolve_name(name)
        return self._stores.pop(key, None) is not None

    def get_store(self, name: str | None = None) -> MemoryStore:
        """
        按名称获取实例；不存在则抛出 :class:`KeyError`。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。
        """
        key = self._resolve_name(name)
        if key not in self._stores:
            registered = sorted(self._stores.keys())
            raise KeyError(
                f"未注册的记忆存储实例: '{key}'；已注册的实例: {registered}；"
                f"请先通过 register() 注册"
            )
        return self._stores[key]

    def get_registered_names(self) -> list[str]:
        """返回已注册的全部实例名称（排序）。"""
        return sorted(self._stores.keys())

    # region ======== 转发接口 ========

    def init_store(self, name: str | None = None) -> None:
        self.get_store(name).init_store()

    def remember(
        self,
        record: MemoryRecord,
        name: str | None = None,
        shared_mode: bool = False,
    ) -> int:
        return self.get_store(name).remember(record, shared_mode=shared_mode)

    def recall(
        self,
        query: str,
        scope: str | None = None,
        category: str | None = None,
        limit: int = 20,
        include_deleted: bool = False,
        name: str | None = None,
        touch: bool = True,
        shared_mode: bool = False,
    ) -> list[dict[str, Any]]:
        """
        模糊检索并（默认）叠加命中计数副作用。

        Args:
            touch: 是否对命中行累加 use_count/刷新 last_used_at（默认 True）。
                纯只读浏览（如 list 场景）可置 False。
            shared_mode: 是否为共享角色（仅查/操作共享数据）。
        """
        store = self.get_store(name)
        results = store.recall(query, scope=scope, category=category,
                               limit=limit, include_deleted=include_deleted,
                               shared_mode=shared_mode)
        if touch:
            for row in results:
                rid = row.get('id')
                if rid is not None:
                    try:
                        store.touch(int(rid))
                    except Exception:
                        log.warning("touch 记忆 %s 失败（忽略，不影响召回）", rid, exc_info=True)
        return results

    def get(self, id: int, name: str | None = None, shared_mode: bool = False) -> MemoryRecord | None:
        return self.get_store(name).get(id, shared_mode=shared_mode)

    def find_by_scope_title(
        self,
        scope: str,
        title: str,
        include_deleted: bool = False,
        name: str | None = None,
        shared_mode: bool = False,
    ) -> list[MemoryRecord]:
        return self.get_store(name).find_by_scope_title(
            scope, title, include_deleted=include_deleted, shared_mode=shared_mode)

    def update(
        self,
        id: int,
        fields: dict[str, Any],
        name: str | None = None,
        shared_mode: bool = False,
    ) -> bool:
        return self.get_store(name).update(id, fields, shared_mode=shared_mode)

    def forget(self, id: int, name: str | None = None, shared_mode: bool = False) -> bool:
        return self.get_store(name).forget(id, shared_mode=shared_mode)

    def count(
        self,
        include_deleted: bool = False,
        name: str | None = None,
        shared_mode: bool = False,
    ) -> int:
        return self.get_store(name).count(include_deleted=include_deleted, shared_mode=shared_mode)

    # endregion

# endregion

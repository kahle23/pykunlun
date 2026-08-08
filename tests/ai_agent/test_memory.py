"""
pykunlun.ai_agent 记忆能力的单元测试。

覆盖：
  - :func:`tokenize_query` 分词
  - :class:`SqliteMemoryStore` 全套 CRUD + 模糊检索 + 计分排序 + 软删除
  - :class:`MemoryManager` recall 的命中计数（touch）副作用
  - **跨实例持久化**（SqliteMemoryStore 相对纯内存实现的核心价值）
"""

import os
import tempfile

import pytest

from pykunlun.ai_agent import (
    MemoryManager,
    MemoryRecord,
    SqliteMemoryStore,
    tokenize_query,
)


# region ======== fixture ========

@pytest.fixture
def store_path():
    """提供一个临时 sqlite 文件路径，测试结束自动清理。"""
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.close()
    path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def store(store_path):
    """已初始化的 SqliteMemoryStore。"""
    s = SqliteMemoryStore(store_path)
    s.init_store()
    return s


@pytest.fixture
def mgr(store):
    """已注册默认 store 的 MemoryManager。"""
    m = MemoryManager()
    m.register(MemoryManager.DEFAULT_NAME, store)
    return m

# endregion


# region ======== 分词 ========

def test_tokenize_query_basic():
    assert tokenize_query('th-core-web 路由 权限') == ['th', 'core', 'web', '路由', '权限']


def test_tokenize_query_empty():
    assert tokenize_query('') == []
    assert tokenize_query(None) == []  # type: ignore[arg-type]


def test_tokenize_query_strips_stopwords_and_punct():
    # 标点切分 + 停用词过滤
    tokens = tokenize_query('the, 路由；了 web！')
    assert tokens == ['路由', 'web']

# endregion


# region ======== 建表 ========

def test_rejects_memory_path():
    with pytest.raises(ValueError, match=":memory:"):
        SqliteMemoryStore(':memory:')


def test_init_is_idempotent(store):
    # 重复初始化不应报错、不丢数据
    store.remember(MemoryRecord(scope='a', category='other', title='t', content='c'))
    store.init_store()
    store.init_store()
    assert store.count() == 1

# endregion


# region ======== remember / get ========

def test_remember_returns_id_and_get_roundtrip(store):
    rid = store.remember(MemoryRecord(
        scope='app', category='decision', title='用 Hutool',
        content='字符串用 StrUtil', keywords='hutool,工具'))
    assert rid >= 1
    rec = store.get(rid)
    assert rec is not None
    assert rec.scope == 'app'
    assert rec.title == '用 Hutool'
    assert rec.is_deleted == 0


def test_get_missing_returns_none(store):
    assert store.get(99999) is None

# endregion


# region ======== recall 计分与排序 ========

def _seed(store):
    store.remember(MemoryRecord(scope='app', category='decision', title='用 Hutool',
                                content='字符串用 StrUtil', keywords='hutool,工具'))
    store.remember(MemoryRecord(scope='app', category='no-go', title='别动 auth',
                                content='auth 模块禁区', keywords='auth,禁区', pinned=1))
    store.remember(MemoryRecord(scope='pykunlun', category='file-path',
                                title='抽象层路径', content='ai_agent/memory.py', keywords=''))


def test_recall_scoring(store):
    _seed(store)
    rows = store.recall('hutool', scope='app')
    # title(3) + keywords(2) = 5
    assert len(rows) == 1
    assert rows[0]['_score'] == 5
    assert rows[0]['title'] == '用 Hutool'


def test_recall_pinned_sorts_first(store):
    """置顶项即便相关度低也排在最前。"""
    _seed(store)
    rows = store.recall('auth')  # 命中 title=3
    assert rows[0]['pinned'] == 1
    assert rows[0]['title'] == '别动 auth'


def test_recall_scope_filter(store):
    _seed(store)
    assert len(store.recall('路径', scope='app')) == 0
    assert len(store.recall('路径', scope='pykunlun')) == 1


def test_recall_category_filter(store):
    _seed(store)
    rows = store.recall('', scope='app', category='no-go')  # 空查询=浏览
    assert len(rows) == 1
    assert rows[0]['category'] == 'no-go'


def test_recall_empty_query_browses_all(store):
    _seed(store)
    rows = store.recall('')
    assert len(rows) == 3


def test_recall_limit(store):
    for i in range(5):
        store.remember(MemoryRecord(scope='app', category='other', title=f't{i}', content='x'))
    assert len(store.recall('', scope='app', limit=3)) == 3

# endregion


# region ======== 去重查找 / update / forget / touch ========

def test_find_by_scope_title(store):
    store.remember(MemoryRecord(scope='app', category='other', title='T', content='c1'))
    dups = store.find_by_scope_title('app', 'T')
    assert len(dups) == 1
    assert store.find_by_scope_title('app', '其他') == []


def test_update_whitelist(store):
    rid = store.remember(MemoryRecord(scope='app', category='other', title='T',
                                      content='c', confidence=70, use_count=5))
    ok = store.update(rid, {'content': 'new', 'confidence': 95, 'pinned': 1})
    assert ok is True
    rec = store.get(rid)
    assert rec.content == 'new'
    assert rec.confidence == 95
    assert rec.pinned == 1


def test_update_ignores_non_updatable(store):
    """id/use_count/is_deleted/时间戳不在白名单内，应被忽略。"""
    rid = store.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    # 这些字段不在 UPDATABLE_FIELDS 中
    ok = store.update(rid, {'id': 999, 'use_count': 100, 'is_deleted': 1})
    assert ok is False
    rec = store.get(rid)
    assert rec.id == rid
    assert rec.use_count == 0
    assert rec.is_deleted == 0


def test_update_missing_returns_false(store):
    assert store.update(88888, {'content': 'x'}) is False


def test_forget_soft_delete(store):
    rid = store.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    assert store.forget(rid) is True
    assert store.get(rid) is None  # get 默认排除软删除
    assert store.count() == 0
    assert store.count(include_deleted=True) == 1
    assert store.forget(rid) is False  # 已删除，再次 forget 不命中


def test_touch_increments(store):
    rid = store.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    store.touch(rid)
    store.touch(rid)
    assert store.get(rid).use_count == 2


def test_manager_recall_auto_touches(mgr, store):
    """MemoryManager.recall 默认对命中行累加 use_count。"""
    _seed(store)
    mgr.recall('hutool', scope='app')
    rows = mgr.recall('hutool', scope='app', touch=False)  # 第二次只读
    # 第一次 recall 应已 touch，use_count>=1
    assert rows[0]['use_count'] >= 1

# endregion


# region ======== 持久化（SqliteMemoryStore 的核心价值）======

def test_persistence_across_instances(store_path):
    """新实例打开同一文件，应读到之前写入的数据——这才是「记忆」而非「内存」。"""
    s1 = SqliteMemoryStore(store_path)
    s1.init_store()
    rid = s1.remember(MemoryRecord(scope='app', category='decision',
                                   title='跨重启存活', content='持久化验证'))
    # 模拟进程重启：丢弃旧实例，新建一个指向同一文件的实例
    del s1
    s2 = SqliteMemoryStore(store_path)
    # 注意：新实例未调用 init_store 也能读（表已存在）
    rec = s2.get(rid)
    assert rec is not None
    assert rec.title == '跨重启存活'
    assert s2.count() == 1

# endregion


# region ======== 所有权与角色隔离（owner / shared_mode）======

def test_remember_stamps_owner_normal(store_path):
    """正常角色 remember 盖当前 owner 与 owner_group。"""
    s = SqliteMemoryStore(store_path, owner='kahle', owner_group='backend')
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    rec = s.get(rid)
    assert rec.owner == 'kahle'
    assert rec.owner_group == 'backend'


def test_remember_shared_mode_stamps_null_owner(store_path):
    """共享角色 remember：owner 置空（→ 共享记忆），owner_group 仍盖当前组。"""
    s = SqliteMemoryStore(store_path, owner='kahle', owner_group='backend')
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c'),
                     shared_mode=True)
    rec = s.get(rid, shared_mode=True)
    assert rec.owner is None
    assert rec.owner_group == 'backend'


def test_normal_role_sees_own_plus_shared(store_path):
    """正常角色：读可见自己的 + 共享；看不见别人的个人。"""
    alice = SqliteMemoryStore(store_path, owner='alice', owner_group='team-a')
    alice.init_store()
    a_own = alice.remember(MemoryRecord(scope='app', category='other', title='alice私', content='x'))
    shared = alice.remember(MemoryRecord(scope='app', category='other', title='共享项', content='x'),
                            shared_mode=True)
    # bob 建立自己的私人记忆
    bob = SqliteMemoryStore(store_path, owner='bob', owner_group='team-a')
    bob_own = bob.remember(MemoryRecord(scope='app', category='other', title='bob私', content='x'))

    # alice 正常角色能看到 a_own、shared，看不到 bob_own
    rows = alice.recall('', scope='app')
    titles = {r['title'] for r in rows}
    assert 'alice私' in titles
    assert '共享项' in titles
    assert 'bob私' not in titles
    assert alice.get(a_own) is not None
    assert alice.get(bob_own) is None  # 看不见别人的


def test_normal_role_cannot_modify_shared(store_path):
    """正常角色可改自己的，但改不了共享的（须切共享角色）。"""
    s = SqliteMemoryStore(store_path, owner='alice', owner_group='g')
    s.init_store()
    own = s.remember(MemoryRecord(scope='app', category='other', title='我的', content='x'))
    shared = s.remember(MemoryRecord(scope='app', category='other', title='共享', content='x'),
                        shared_mode=True)
    assert s.update(own, {'content': '改了'}) is True        # 自己的可改
    assert s.update(shared, {'content': '改了'}) is False    # 共享的正常角色改不了
    assert s.forget(shared) is False                         # 也删不了


def test_shared_role_can_modify_shared_only(store_path):
    """共享角色：可改/删共享，但看不见、动不了别人的个人。"""
    alice = SqliteMemoryStore(store_path, owner='alice', owner_group='g')
    alice.init_store()
    own = alice.remember(MemoryRecord(scope='app', category='other', title='我的', content='x'))
    shared = alice.remember(MemoryRecord(scope='app', category='other', title='共享', content='x'),
                            shared_mode=True)

    # 共享角色：个人不可见、不可改；共享可改可删
    assert alice.get(own, shared_mode=True) is None
    assert alice.update(own, {'content': 'y'}, shared_mode=True) is False
    assert alice.update(shared, {'content': '改共享'}, shared_mode=True) is True
    assert alice.forget(shared, shared_mode=True) is True
    # 共享角色下 recall 只看到共享
    rows = alice.recall('', scope='app', shared_mode=True)
    assert all(r['owner'] is None for r in rows)


def test_cross_user_cannot_modify_others_personal(store_path):
    """bob 改不了 alice 的个人记忆（owner 不匹配）。"""
    alice = SqliteMemoryStore(store_path, owner='alice', owner_group='g')
    alice.init_store()
    a_own = alice.remember(MemoryRecord(scope='app', category='other', title='alice私', content='x'))

    bob = SqliteMemoryStore(store_path, owner='bob', owner_group='g')
    # bob 正常角色：看不见、改不了 alice 的
    assert bob.get(a_own) is None
    assert bob.update(a_own, {'content': '篡改'}) is False
    assert bob.forget(a_own) is False
    # bob 用共享角色也改不了（那是个人数据，非共享）
    assert bob.update(a_own, {'content': '篡改'}, shared_mode=True) is False


def test_no_identity_behaves_as_shared(store_path):
    """无身份(owner=None)：读写仅共享域——单用户 sqlite 全是共享 = 全可读写。"""
    s = SqliteMemoryStore(store_path)  # 无 owner
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='x', content='y'))
    # 无身份记住的行 owner=NULL（共享），自己可读可改
    assert s.get(rid) is not None
    assert s.update(rid, {'content': 'z'}) is True
    assert s.forget(rid) is True


def test_no_identity_cannot_touch_others_personal(store_path):
    """无身份调用者改不了别人(owner=alice)的个人数据（owner IS NULL 匹配不上）。"""
    alice = SqliteMemoryStore(store_path, owner='alice', owner_group='g')
    alice.init_store()
    a_own = alice.remember(MemoryRecord(scope='app', category='other', title='alice私', content='x'))

    anon = SqliteMemoryStore(store_path)  # 无身份
    assert anon.get(a_own) is None                       # 个人数据不可见
    assert anon.update(a_own, {'content': 'x'}) is False  # 也改不了
    assert anon.forget(a_own) is False

# endregion


# region ======== machine / agent_name 盖章 ========

def test_remember_stamps_machine_and_agent(store_path):
    """remember 自动盖 machine/agent_name 章（构造时绑定）。"""
    s = SqliteMemoryStore(store_path, owner='alice', machine='pc-a', agent_name='opencode')
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    rec = s.get(rid)
    assert rec.machine == 'pc-a'
    assert rec.agent_name == 'opencode'


def test_remember_keeps_explicit_machine_agent(store_path):
    """record 显式给 machine/agent_name 时保留之，不被构造绑定值覆盖。"""
    s = SqliteMemoryStore(store_path, owner='alice', machine='pc-a', agent_name='opencode')
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c',
                                  machine='pc-b', agent_name='codex'))
    rec = s.get(rid)
    assert rec.machine == 'pc-b'
    assert rec.agent_name == 'codex'


def test_remember_no_machine_no_agent(store_path):
    """未绑定 machine/agent_name 时，盖章为 None。"""
    s = SqliteMemoryStore(store_path)
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    rec = s.get(rid)
    assert rec.machine is None
    assert rec.agent_name is None


def test_remember_strips_blank_machine(store_path):
    """纯空白 machine/agent_name 归一为 None（避免 ' ' 残留）。"""
    s = SqliteMemoryStore(store_path, machine='   ', agent_name='')
    s.init_store()
    rid = s.remember(MemoryRecord(scope='app', category='other', title='T', content='c'))
    rec = s.get(rid)
    assert rec.machine is None
    assert rec.agent_name is None

# endregion


# region ======== find_by_scope_title 去重三态（machine_bound）======

def test_dedup_machine_bound_isolates_by_machine(store_path):
    """machine_bound=True：同 scope+title 跨 machine 不判重（路径类语义）。"""
    a = SqliteMemoryStore(store_path, owner='alice', machine='pc-a')
    a.init_store()
    a.remember(MemoryRecord(scope='app', category='file-path', title='项目根', content='E:\\mine'))
    # pc-b 查同样 scope+title，machine_bound=True 应不命中 pc-a 的记录
    b = SqliteMemoryStore(store_path, owner='alice', machine='pc-b')
    assert b.find_by_scope_title('app', '项目根', machine_bound=True) == []


def test_dedup_machine_bound_matches_same_machine(store_path):
    """machine_bound=True：同 machine 下同 scope+title 判重。"""
    a = SqliteMemoryStore(store_path, owner='alice', machine='pc-a')
    a.init_store()
    a.remember(MemoryRecord(scope='app', category='file-path', title='项目根', content='E:\\mine'))
    dups = a.find_by_scope_title('app', '项目根', machine_bound=True)
    assert len(dups) == 1


def test_dedup_global_shared_across_machines(store_path):
    """machine_bound=False（默认）：同 scope+title 跨 machine 判重（通用知识语义）。"""
    a = SqliteMemoryStore(store_path, owner='alice', machine='pc-a')
    a.init_store()
    a.remember(MemoryRecord(scope='app', category='convention', title='用 Hutool', content='...'))
    b = SqliteMemoryStore(store_path, owner='alice', machine='pc-b')
    # 默认全局判重，能查到 pc-a 那条
    dups = b.find_by_scope_title('app', '用 Hutool')
    assert len(dups) == 1
    assert dups[0].machine == 'pc-a'


def test_dedup_machine_bound_falls_back_when_no_machine(store_path):
    """machine_bound=True 但当前 store 未绑定 machine：退化为全局判重。"""
    a = SqliteMemoryStore(store_path, owner='alice')  # 无 machine
    a.init_store()
    a.remember(MemoryRecord(scope='app', category='file-path', title='T', content='x'))
    dups = a.find_by_scope_title('app', 'T', machine_bound=True)
    assert len(dups) == 1  # 退化：能查到自己刚记的

# endregion


# region ======== 老库迁移（_migrate 补列）======

def test_migrate_adds_columns_to_legacy_table(store_path):
    """建一个老式（无 machine/agent_name 列）的表，init_store 应补齐列且不丢老数据。"""
    import sqlite3
    conn = sqlite3.connect(store_path)
    conn.execute("""CREATE TABLE ai_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scope TEXT NOT NULL, category TEXT NOT NULL, title TEXT NOT NULL, content TEXT,
        owner TEXT, owner_group TEXT, keywords TEXT DEFAULT '',
        source TEXT DEFAULT 'user-told', confidence INTEGER DEFAULT 80, pinned INTEGER DEFAULT 0,
        use_count INTEGER DEFAULT 0, last_used_at TEXT, is_deleted INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT
    )""")
    conn.execute("INSERT INTO ai_memory (scope, category, title, content) "
                 "VALUES ('app','other','old','x')")
    conn.commit()
    conn.close()

    s = SqliteMemoryStore(store_path)
    s.init_store()  # 应触发 _migrate 补列

    cols = {r['name'] for r in s._query('PRAGMA table_info(ai_memory)')}
    assert 'machine' in cols
    assert 'agent_name' in cols
    # 老数据未丢，machine 为 NULL
    dups = s.find_by_scope_title('app', 'old')
    assert len(dups) == 1
    assert dups[0].machine is None
    # 新写入正常（含 machine 盖章）
    s2 = SqliteMemoryStore(store_path, machine='pc-a', agent_name='opencode')
    rid = s2.remember(MemoryRecord(scope='app', category='other', title='new', content='y'))
    assert s2.get(rid).machine == 'pc-a'


def test_migrate_is_idempotent(store_path):
    """多次 init_store 不重复加列、不报错（ALTER COLUMN 已存在会抛错，幂等性靠检测列名）。"""
    s = SqliteMemoryStore(store_path)
    s.init_store()
    s.init_store()  # 二次不应抛 "duplicate column name"
    s.init_store()  # 三次
    cols = [r['name'] for r in s._query('PRAGMA table_info(ai_memory)')]
    assert cols.count('machine') == 1
    assert cols.count('agent_name') == 1

# endregion

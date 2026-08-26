"""
pykunlun.ai_agent 长任务能力的单元测试。

覆盖：
  - 状态机纯函数（:func:`assert_task_transition` / :func:`assert_step_transition` /
    :func:`step_disposition_on_fail`）
  - 数据类 to_dict / from_dict 往返
  - :class:`LongTaskManager` 注册 / 转发 / 未注册 KeyError
  - :class:`SqliteLongTaskService` 全生命周期：建表幂等、任务/步骤/执行 CRUD、
    claim 续跑上下文包、finish 自动收口、fail 重试预算、sweep 僵尸恢复、
    pause/resume/cancel/skip/retry、事件留痕、产物、模板
"""

import os
import tempfile

import pytest

from pykunlun.ai_agent import (
    STEP_TRANSITIONS,
    TASK_TRANSITIONS,
    LongTaskManager,
    SqliteLongTaskService,
    TaskInstance,
    TaskStep,
    TaskTemplate,
    assert_step_transition,
    assert_task_transition,
    step_disposition_on_fail,
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
    """已初始化的 SqliteLongTaskService。"""
    s = SqliteLongTaskService(store_path)
    s.setup()
    return s


@pytest.fixture
def mgr(store):
    """已注册默认 store 的 LongTaskManager。"""
    m = LongTaskManager()
    m.register(LongTaskManager.DEFAULT_NAME, store)
    return m


def _mk_task(store, title='示例任务', max_retries=1, heartbeat_timeout_sec=1800,
             timeout_sec=None):
    """建一个任务并返回 id。"""
    return store.create_task(TaskInstance(
        title=title, goal='完成示例目标', max_retries=max_retries,
        heartbeat_timeout_sec=heartbeat_timeout_sec, timeout_sec=timeout_sec))


def _mk_steps(store, task_id, names=('第一步', '第二步', '第三步')):
    """给任务按顺序加 N 个步骤，返回 (step_id 列表, 序号)。"""
    ids = []
    for i, name in enumerate(names, start=1):
        ids.append(store.add_step(TaskStep(
            task_id=task_id, name=name, instruction=f'执行 {name} 的指令', seq=i)))
    return ids

# endregion


# region ======== 状态机纯函数 ========

@pytest.mark.parametrize('old,new', [(o, n) for o, targets in TASK_TRANSITIONS.items()
                                     for n in targets])
def test_task_legal_transitions(old, new):
    assert_task_transition(old, new)


@pytest.mark.parametrize('old,new', [
    ('pending', 'paused'), ('pending', 'completed'), ('pending', 'failed'),
    ('running', 'pending'),
    ('paused', 'pending'), ('paused', 'completed'), ('paused', 'failed'),
    ('completed', 'running'), ('failed', 'running'), ('cancelled', 'running'),
    ('completed', 'cancelled'), ('failed', 'pending'),
    ('bogus', 'running'),
])
def test_task_illegal_transitions(old, new):
    with pytest.raises(ValueError, match='非法|未知'):
        assert_task_transition(old, new)


@pytest.mark.parametrize('old,new', [(o, n) for o, targets in STEP_TRANSITIONS.items()
                                     for n in targets])
def test_step_legal_transitions(old, new):
    assert_step_transition(old, new)


@pytest.mark.parametrize('old,new', [
    ('pending', 'succeeded'), ('pending', 'failed'),
    ('succeeded', 'running'), ('succeeded', 'pending'),
    ('failed', 'running'), ('failed', 'pending'),
    ('skipped', 'pending'), ('skipped', 'running'),
    ('bogus', 'running'),
])
def test_step_illegal_transitions(old, new):
    with pytest.raises(ValueError, match='非法|未知'):
        assert_step_transition(old, new)


@pytest.mark.parametrize('retry_count,max_retries,expected', [
    (0, 1, 'pending'),   # 首次失败，预算剩 1 → 重试
    (1, 1, 'failed'),    # 重试后仍失败，预算耗尽 → 终败
    (0, 0, 'failed'),    # 无重试预算
    (2, 5, 'pending'),   # 多次预算
    (4, 5, 'pending'),
    (5, 5, 'failed'),
])
def test_step_disposition_on_fail(retry_count, max_retries, expected):
    assert step_disposition_on_fail(retry_count, max_retries) == expected

# endregion


# region ======== 数据类 ========

def test_task_instance_dict_roundtrip():
    inst = TaskInstance(title='T', goal='G', params={'a': 1}, max_retries=3)
    d = inst.to_dict()
    assert d['title'] == 'T' and d['params'] == {'a': 1}
    r = TaskInstance.from_dict({**d, 'unknown_field': 'x'})  # 未知键被忽略
    assert r.title == 'T' and r.params == {'a': 1} and r.max_retries == 3


def test_task_step_defaults():
    s = TaskStep(task_id=1, name='n', instruction='i')
    assert s.status == 'pending' and s.step_type == 'agent'
    assert s.max_retries is None and s.seq is None

# endregion


# region ======== 管理器 ========

def test_manager_unregistered_raises():
    m = LongTaskManager()
    with pytest.raises(KeyError, match='未注册'):
        m.get_service('nope')


def test_manager_forward(mgr, store):
    """Manager 转发到默认实例（含 name 参数插位）。"""
    tid = mgr.create_task(TaskInstance(title='T', goal='G'))
    assert mgr.get_task(tid).title == 'T'
    assert store.get_task(tid) is not None  # 同一底层 store
    assert mgr.get_registered_names() == ['default']

# endregion


# region ======== 建表 / 任务 / 步骤基础 ========

def test_rejects_memory_path():
    with pytest.raises(ValueError, match=':memory:'):
        SqliteLongTaskService(':memory:')


def test_init_is_idempotent(store):
    tid = _mk_task(store)
    store.setup()
    store.setup()
    assert store.get_task(tid) is not None


def test_create_and_get_task(store):
    tid = store.create_task(TaskInstance(
        title='补文档', goal='补全 README', params={'repo': 'x'}, created_by='kahle'))
    assert tid >= 1
    inst = store.get_task(tid)
    assert inst.status == 'pending'
    assert inst.params == {'repo': 'x'}   # JSON 列往返
    assert inst.created_by == 'kahle'
    assert store.get_task(99999) is None


def test_list_tasks_progress(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('a', 'b'))
    pkg = store.claim_next_step(tid)
    store.finish_run(pkg['run_id'], output='out', summary='ok')
    rows = store.list_tasks(status='pending')  # 任务已 running，pending 过滤应为空
    assert rows == []
    rows = store.list_tasks(status='running')
    assert len(rows) == 1
    assert rows[0]['total'] == 2 and rows[0]['done'] == 1


def test_update_task_whitelist(store):
    tid = _mk_task(store)
    assert store.update_task(tid, {'title': '新标题', 'params': {'k': 'v'}}) is True
    assert store.get_task(tid).title == '新标题'
    assert store.get_task(tid).params == {'k': 'v'}
    assert store.update_task(tid, {'id': 999, 'status': 'completed'}) is False  # 白名单外
    assert store.get_task(tid).status == 'pending'


def test_add_step_seq_and_inheritance(store):
    tid = _mk_task(store, max_retries=3)
    s1 = store.add_step(TaskStep(task_id=tid, name='s1', instruction='i1'))   # seq 自动 1
    s2 = store.add_step(TaskStep(task_id=tid, name='s2', instruction='i2'))   # seq 自动 2
    assert store.get_step(s1).seq == 1
    assert store.get_step(s2).seq == 2
    assert store.get_step(s1).max_retries == 3   # 继承任务级预算
    with pytest.raises(ValueError, match='step_type'):
        store.add_step(TaskStep(task_id=tid, name='s3', instruction='i', step_type='magic'))
    with pytest.raises(ValueError, match='不存在'):
        store.add_step(TaskStep(task_id=999, name='s', instruction='i'))


def test_add_steps_batch(store):
    tid = _mk_task(store)
    n = store.add_steps([
        TaskStep(task_id=tid, name='a', instruction='ia'),
        TaskStep(task_id=tid, name='b', instruction='ib'),
    ])
    assert n == 2
    steps = store.list_steps(tid)
    assert [s['seq'] for s in steps] == [1, 2]
    with pytest.raises(ValueError, match='同一任务'):
        store.add_steps([
            TaskStep(task_id=tid, name='x', instruction='i'),
            TaskStep(task_id=tid + 100, name='y', instruction='i'),
        ])

# endregion


# region ======== claim / finish / fail 主循环 ========

def test_claim_returns_context_package(store):
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    pkg = store.claim_next_step(tid, session_id='sess-1', agent_name='zcode')

    assert pkg is not None
    assert pkg['task']['id'] == tid
    assert pkg['task']['status'] == 'running'          # 首次 claim：pending → running
    assert pkg['step']['id'] == sids[0]                # seq 最小的 pending 步骤
    assert pkg['step']['status'] == 'running'
    assert pkg['context'] == []                        # 尚无前序成功步骤
    assert pkg['run_id'] >= 1
    # 任务心跳已刷
    assert store.get_task(tid).heartbeat_at is not None
    # input_snapshot 已存续跑上下文
    run = store.get_run(pkg['run_id'])
    assert run.session_id == 'sess-1' and run.agent_name == 'zcode'
    assert run.input_snapshot and 'step' in run.input_snapshot


def test_claim_context_carries_prior_summaries(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one', 'two'))
    p1 = store.claim_next_step(tid)
    store.finish_run(p1['run_id'], output='长输出' * 100, summary='第一步完成：索引就绪')
    p2 = store.claim_next_step(tid)
    assert p2['step']['name'] == 'two'
    assert p2['context'] == [
        {'seq': 1, 'name': 'one', 'result_summary': '第一步完成：索引就绪'}]


def test_claim_no_pending_returns_none(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='done')
    assert store.claim_next_step(tid) is None          # 无 pending 步骤
    assert store.get_task(tid).status == 'completed'   # 末步自动收口


def test_finish_defaults_summary_from_output(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    long_output = 'x' * 3000
    assert store.finish_run(p['run_id'], output=long_output) is True
    step = store.get_step(p['step']['id'])
    assert step.result_summary == 'x' * 2000           # 缺省截取 output 前 2000 字
    run = store.get_run(p['run_id'])
    assert run.status == 'succeeded' and run.output == long_output


def test_finish_terminal_run_returns_false(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='ok')
    assert store.finish_run(p['run_id'], output='again') is False   # 重复 finish 不流转


def test_finish_records_token_usage(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    assert store.finish_run(p['run_id'], output='ok', summary='完成',
                            token_usage=12345) is True
    assert store.get_run(p['run_id']).token_usage == 12345


def test_finish_without_token_usage_keeps_null(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='ok', summary='完成')
    assert store.get_run(p['run_id']).token_usage is None


def test_fail_within_budget_retries(store):
    tid = _mk_task(store, max_retries=1)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    p1 = store.claim_next_step(tid)
    assert store.fail_run(p1['run_id'], '第一轮失败') == 'retried'
    step = store.get_step(sids[0])
    assert step.status == 'pending' and step.retry_count == 1
    assert store.get_task(tid).status == 'running'     # 任务不变
    # 再 claim 应回到同一步骤，且是新的 run
    p2 = store.claim_next_step(tid)
    assert p2['step']['id'] == sids[0]
    assert p2['run_id'] != p1['run_id']
    assert len(store.list_runs(sids[0])) == 2


def test_fail_exhausted_fails_task(store):
    tid = _mk_task(store, max_retries=1)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    p1 = store.claim_next_step(tid)
    store.fail_run(p1['run_id'], 'fail-1')
    p2 = store.claim_next_step(tid)
    assert store.fail_run(p2['run_id'], 'fail-2') == 'step_failed'
    assert store.get_step(sids[0]).status == 'failed'
    assert store.get_task(tid).status == 'failed'      # 预算耗尽，任务连带失败
    assert store.claim_next_step(tid) is None          # failed 任务不再派发


def test_fail_terminal_run_returns_empty(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='ok')
    assert store.fail_run(p['run_id'], 'late error') == ''

# endregion


# region ======== skip / retry / pause / resume / cancel ========

def test_skip_pending_step(store):
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    assert store.skip_step(sids[0], reason='不需要') is True
    assert store.get_step(sids[0]).status == 'skipped'
    assert store.get_task(tid).status == 'pending'     # 任务状态不变
    p = store.claim_next_step(tid)
    assert p['step']['id'] == sids[1]                  # 跳过后认领下一步
    p2 = store.claim_next_step(tid)                    # 无 pending
    assert p2 is None


def test_skip_non_pending_fails(store):
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)                         # step → running
    assert store.skip_step(sids[0]) is False


def test_skip_last_remaining_step_completes_task(store):
    """skip 视同有意完成：跳过最后剩余步骤时 running 任务自动收口。"""
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='ok', summary='一完成')
    assert store.get_task(tid).status == 'running'     # two 还 pending，未收口
    assert store.skip_step(sids[1], reason='不需要') is True
    assert store.get_task(tid).status == 'completed'   # 跳过最后剩余步骤 → 收口


def test_finish_completes_when_rest_skipped(store):
    """先跳过后续步骤，最后一个实际执行步骤 finish 时收口（skipped 不阻断判定）。"""
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    assert store.skip_step(sids[1], reason='砍需求') is True
    p = store.claim_next_step(tid)
    store.finish_run(p['run_id'], output='ok', summary='一完成')
    assert store.get_task(tid).status == 'completed'


def test_skip_all_steps_keeps_task_pending(store):
    """从未 claim 的任务跳完全部步骤保持 pending（pending→completed 不合法）。"""
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one',))
    assert store.skip_step(sids[0]) is True
    assert store.get_task(tid).status == 'pending'


def test_retry_step_revives_failed(store):
    tid = _mk_task(store, max_retries=0)
    sids = _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    store.fail_run(p['run_id'], 'boom')                # 预算 0，直接终败
    assert store.get_task(tid).status == 'failed'
    assert store.retry_step(sids[0]) is True
    step = store.get_step(sids[0])
    assert step.status == 'pending' and step.max_retries == 1   # 预算 +1
    assert store.get_task(tid).status == 'running'     # 任务复活
    p2 = store.claim_next_step(tid)
    assert p2['step']['id'] == sids[0]


def test_retry_only_failed_or_skipped(store):
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one',))
    assert store.retry_step(sids[0]) is False          # pending 不可 retry


def test_pause_resume(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    assert store.pause(tid) is True
    assert store.get_task(tid).status == 'paused'
    assert store.claim_next_step(tid) is None          # 暂停后不派发新步骤
    assert store.pause(tid) is False                   # 非 running 不可再 pause
    assert store.resume(tid) is True
    assert store.get_task(tid).status == 'running'
    # 恢复后继续认领剩余步骤
    store.finish_run(p['run_id'], output='ok')
    assert store.get_task(tid).status == 'completed'


def test_cancel_connected_effects(store):
    tid = _mk_task(store)
    sids = _mk_steps(store, tid, names=('one', 'two'))
    p = store.claim_next_step(tid)
    assert store.cancel(tid, reason='不要了') is True
    assert store.get_task(tid).status == 'cancelled'
    assert store.get_step(sids[0]).status == 'failed'          # running 步骤连带 failed
    assert store.get_run(p['run_id']).status == 'cancelled'    # running run 置 cancelled
    assert store.get_step(sids[1]).status == 'pending'         # 未动
    assert store.cancel(tid) is False                          # 已终态不可再 cancel


def test_cancel_pending_task(store):
    tid = _mk_task(store)
    assert store.cancel(tid) is True
    assert store.get_task(tid).status == 'cancelled'

# endregion


# region ======== sweep 僵尸恢复 ========

def test_sweep_task_level_heartbeat_timeout(store):
    """任务心跳超时：running run 置 timeout，步骤按预算回 pending，任务保持 running。"""
    tid = _mk_task(store, max_retries=1, heartbeat_timeout_sec=0)   # 阈值 0 → 立即超时
    _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    results = store.sweep()
    assert any(r['action'] == 'run_timeout' and r['run_id'] == p['run_id'] for r in results)
    assert store.get_run(p['run_id']).status == 'timeout'
    assert store.get_run(p['run_id']).error_msg == 'heartbeat timeout'
    assert store.get_step(p['step']['id']).status == 'pending'      # 预算未耗尽 → 回 pending
    assert store.get_task(tid).status == 'running'                  # 任务保持 running
    # 断点续跑：sweep 后可重新 claim 同一步骤
    p2 = store.claim_next_step(tid)
    assert p2['step']['id'] == p['step']['id']


def test_sweep_task_level_budget_exhausted(store):
    tid = _mk_task(store, max_retries=0, heartbeat_timeout_sec=0)
    sids = _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    store.sweep()
    # 预算 0：直接 sweep 僵尸时步骤终败、任务失败
    assert store.get_step(sids[0]).status == 'failed'
    assert store.get_task(tid).status == 'failed'


def test_sweep_step_level_timeout(store):
    """任务心跳正常（阈值大），但单步超时：仅该步骤按超时处理。"""
    tid = _mk_task(store, max_retries=1, heartbeat_timeout_sec=9999)
    sid = store.add_step(TaskStep(task_id=tid, name='慢步骤', instruction='i', timeout_sec=0))
    p = store.claim_next_step(tid)
    results = store.sweep()
    assert any(r['action'] == 'run_timeout' and r['run_id'] == p['run_id'] for r in results)
    assert store.get_step(sid).status == 'pending'   # 预算未耗尽回 pending


def test_sweep_healthy_task_untouched(store):
    """心跳与步骤都正常时 sweep 无动作，且幂等（重复执行无副作用）。"""
    tid = _mk_task(store, max_retries=1, heartbeat_timeout_sec=9999)
    _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    assert store.sweep() == []
    assert store.sweep() == []
    assert store.get_task(tid).status == 'running'


def test_sweep_idempotent_on_zombie(store):
    """对同一僵尸重复 sweep 不重复流转（run 已 timeout，第二次为 noop）。"""
    tid = _mk_task(store, max_retries=1, heartbeat_timeout_sec=0)
    _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    first = store.sweep()
    assert first
    assert store.sweep() == []


def test_sweep_task_total_timeout(store):
    """任务总超时：run 置 timeout、running 步骤直接终败（不走预算）、任务 failed。"""
    tid = _mk_task(store, max_retries=5, heartbeat_timeout_sec=9999, timeout_sec=0)
    sids = _mk_steps(store, tid, names=('one',))
    p = store.claim_next_step(tid)
    results = store.sweep()
    assert any(r['action'] == 'task_timeout' and r['run_id'] == p['run_id'] for r in results)
    assert any(r['action'] == 'task_failed' for r in results)
    assert store.get_run(p['run_id']).status == 'timeout'
    assert store.get_run(p['run_id']).error_msg == 'task total timeout'
    assert store.get_step(sids[0]).status == 'failed'  # 预算 5 也直接终败
    assert store.get_task(tid).status == 'failed'
    assert store.sweep() == []                          # 幂等


def test_sweep_task_total_timeout_revive_by_retry(store):
    """任务总超时终败后可用 retry_step 手动复活（预算 +1、任务回 running）。"""
    tid = _mk_task(store, heartbeat_timeout_sec=9999, timeout_sec=0)
    sids = _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    store.sweep()
    assert store.retry_step(sids[0]) is True
    assert store.get_step(sids[0]).status == 'pending'
    assert store.get_task(tid).status == 'running'


def test_sweep_total_timeout_ignores_unset(store):
    """未设 timeout_sec 的任务不参与总超时检测（心跳正常则 sweep 无动作）。"""
    tid = _mk_task(store, heartbeat_timeout_sec=9999)   # timeout_sec 默认 None
    _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    assert store.sweep() == []

# endregion


# region ======== 事件 / 产物 / 模板 ========

def test_events_automatic_and_manual(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one',))
    store.claim_next_step(tid)
    store.add_event(tid, 'checkpoint', '人工检查点', level='warn')
    events = store.list_events(tid, limit=100)
    # id 倒序：最新在前
    assert events[0]['event_type'] == 'checkpoint' and events[0]['level'] == 'warn'
    messages = [e['message'] for e in events]
    assert any('pending → running' in m for m in messages)   # 状态流转自动留痕


def test_event_validates_types(store):
    tid = _mk_task(store)
    with pytest.raises(ValueError, match='event_type'):
        store.add_event(tid, 'bogus', 'x')
    with pytest.raises(ValueError, match='level'):
        store.add_event(tid, 'note', 'x', level='fatal')


def test_artifacts(store):
    tid = _mk_task(store)
    aid = store.add_artifact(tid, 'report', 'docs/report.md', note='最终报告')
    assert aid >= 1
    arts = store.list_artifacts(tid)
    assert len(arts) == 1 and arts[0]['path'] == 'docs/report.md'
    with pytest.raises(ValueError, match='art_type'):
        store.add_artifact(tid, 'movie', 'x.mp4')


def test_templates(store):
    tid = _mk_task(store)
    _mk_steps(store, tid, names=('one', 'two'))
    t = TaskTemplate(
        name='文档任务', description='标准文档流程', skill_ref='agent-long-task',
        default_params={'repo': 'x'},
        step_blueprint=[{'name': 'one', 'instruction': 'i1', 'step_type': 'agent'}],
    )
    tpl_id = store.create_template(t)
    assert tpl_id >= 1
    got = store.get_template_by_name('文档任务')
    assert got.step_blueprint[0]['name'] == 'one'      # JSON 列往返
    assert got.default_params == {'repo': 'x'}
    assert got.skill_ref == 'agent-long-task'          # skill_ref 往返
    assert store.get_template_by_name('不存在') is None
    assert len(store.list_templates()) == 1
    with pytest.raises(ValueError, match='已存在'):
        store.create_template(TaskTemplate(name='文档任务'))

# endregion

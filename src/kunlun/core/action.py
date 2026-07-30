"""
动作管理模块。

提供动作（Action）的注册、取消注册、获取和执行能力。
每个动作由 name 唯一标识，支持按通配符模式查询动作名称。
注册允许覆盖同名动作。

动作为任意可调用对象（Callable[..., Any]），由调用方自行约定入参与返回值。

线程安全：内部使用 threading.Lock 保护全局动作表，所有公开方法均可
在多线程环境下安全调用。
"""

import fnmatch
import threading
from typing import Any, Callable, Dict, List, Optional

# region ======== 动作管理 ========
_actions: Dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def _resolve_name(name: Optional[str]) -> str:
    """
    解析动作名称：去除前后空格，且不能为空。

    Args:
        name: 原始名称。

    Returns:
        strip 后的名称。

    Raises:
        ValueError: name 为 None 或空白字符串时抛出。
    """
    if name is None:
        raise ValueError("动作名称 name 不能为空")
    stripped = name.strip()
    if not stripped:
        raise ValueError("动作名称 name 不能为空")
    return stripped


def _match_names_unlocked(name_pattern: Optional[str]) -> List[str]:
    """
    无锁版名称匹配（调用方必须已持有 _lock）。

    Args:
        name_pattern: 名称匹配模式，支持通配符（* ?）；为 None 时匹配全部。

    Returns:
        匹配到的动作名称列表（按名称升序排序）。
    """
    if name_pattern is None:
        return sorted(_actions.keys())
    return sorted(fnmatch.filter(_actions.keys(), name_pattern))


def register(
    name: str,
    action_obj: Callable[..., Any],
) -> Optional[Callable[..., Any]]:
    """
    注册动作（允许覆盖同名动作）。

    Args:
        name: 动作名称；不能为空，前后空格会被去除。
        action_obj: 可调用对象。

    Returns:
        被覆盖的旧动作；无旧值时为 None。

    Raises:
        TypeError: action_obj 不可调用时抛出。
        ValueError: name 为空时抛出。
    """
    if not callable(action_obj):
        raise TypeError(f"action 必须是可调用对象，实际类型: {type(action_obj)}")
    name = _resolve_name(name)
    with _lock:
        old = _actions.get(name)
        _actions[name] = action_obj
        return old


def unregister(name: str) -> Optional[Callable[..., Any]]:
    """
    取消注册动作。

    Args:
        name: 动作名称；不能为空，前后空格会被去除。

    Returns:
        被移除的动作；不存在时为 None。

    Raises:
        ValueError: name 为空时抛出。
    """
    name = _resolve_name(name)
    with _lock:
        return _actions.pop(name, None)


def get_action(name: str) -> Optional[Callable[..., Any]]:
    """
    获取指定动作。

    Args:
        name: 动作名称；不能为空，前后空格会被去除。

    Returns:
        动作对象；不存在时为 None。

    Raises:
        ValueError: name 为空时抛出。
    """
    name = _resolve_name(name)
    with _lock:
        return _actions.get(name)


def has_action(name: str) -> bool:
    """
    判断动作是否存在。

    Args:
        name: 动作名称；不能为空，前后空格会被去除。

    Returns:
        存在返回 True，否则 False。

    Raises:
        ValueError: name 为空时抛出。
    """
    name = _resolve_name(name)
    with _lock:
        return name in _actions


def get_names(name_pattern: Optional[str] = None) -> List[str]:
    """
    获取匹配的动作名称列表。

    Args:
        name_pattern: 名称匹配模式（支持通配符 * ?）。若为 None，返回全部。

    Returns:
        匹配的动作名称列表（按名称排序）。
    """
    with _lock:
        return _match_names_unlocked(name_pattern)


def clear(name_pattern: Optional[str] = None) -> int:
    """
    清空动作。

    Args:
        name_pattern: 名称匹配模式。若为 None，清空全部。

    Returns:
        实际清除的动作数量。
    """
    with _lock:
        if name_pattern is None:
            count = len(_actions)
            _actions.clear()
            return count
        names = _match_names_unlocked(name_pattern)
        for name in names:
            _actions.pop(name, None)
        return len(names)


def execute(
    name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """
    执行单个动作。

    执行时不在锁内调用动作本体（避免业务回调中再次访问动作表造成死锁），
    仅在锁内完成动作对象的查找。

    Args:
        name: 动作名称；不能为空，前后空格会被去除。
        *args: 透传给动作的位置参数。
        **kwargs: 透传给动作的关键字参数。

    Returns:
        执行结果。

    Raises:
        ValueError: name 为空时抛出。
        KeyError: 动作不存在时抛出。
    """
    name = _resolve_name(name)
    with _lock:
        action_obj = _actions.get(name)
    if action_obj is None:
        raise KeyError(name)
    return action_obj(*args, **kwargs)

# endregion

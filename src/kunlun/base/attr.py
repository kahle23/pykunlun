"""
属性操作工具函数。

提供统一的接口来操作对象或字典的属性，适用于 JSON 反序列化后
可能是 dict 也可能是对象的场景。
"""

from typing import Any


def get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """
    从对象或字典中获取属性值。

    Args:
        obj: 数据源，可以是任意对象或字典。
        attr: 属性名称。
        default: 属性不存在时的默认值。

    Returns:
        属性值，不存在时返回 default。
    """
    # 处理 None 值
    if obj is None:
        return default
    # 处理字典
    if isinstance(obj, dict):
        return obj.get(attr, default)
    # 处理对象
    return getattr(obj, attr, default)


def set_attr(obj: Any, attr: str, value: Any) -> None:
    """
    设置对象或字典的属性值。

    Args:
        obj: 数据源，可以是任意对象或字典。
        attr: 属性名称。
        value: 属性值。

    Raises:
        TypeError: 当 obj 为 None 时抛出。
    """
    # 处理 None 值
    if obj is None:
        raise TypeError("无法对 None 设置属性")
    if isinstance(obj, dict):
        # 处理字典
        obj[attr] = value
    else:
        # 处理对象
        setattr(obj, attr, value)


def del_attr(obj: Any, attr: str) -> None:
    """
    删除对象或字典的属性。

    Args:
        obj: 数据源，可以是任意对象或字典。
        attr: 属性名称。

    Raises:
        TypeError: 当 obj 为 None 时抛出。
        KeyError: 字典中属性不存在时抛出。
        AttributeError: 对象中属性不存在时抛出。
    """
    # 处理 None 值
    if obj is None:
        raise TypeError("无法对 None 删除属性")
    if isinstance(obj, dict):
        # 处理字典
        del obj[attr]
    else:
        # 处理对象
        delattr(obj, attr)

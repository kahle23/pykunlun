"""
数据加载工具函数。

提供从 JSON 文件加载 dataclass 实例的便捷方法，
适用于配置文件读取和数据对象构建场景。
"""

import json
from pathlib import Path
from typing import Any, TypeVar

from . import logutil
from .validation import check_dataclass_required_fields

log = logutil.getLogger(__name__)

# 定义类型变量 T，用于表示 dataclass 配置类的实例类型
T = TypeVar('T')


def load_dataclass_from_json_file(file_path: str | Path, data_class: type[T]) -> T:
    """
    从 JSON 文件加载 dataclass 实例对象。

    读取指定路径的 JSON 文件，自动校验 dataclass 的必填字段后返回实例对象。
    适用于所有使用 dataclass 定义的类。

    Args:
        file_path: 配置文件路径，可以是字符串或 Path 对象。
        data_class: dataclass 类类型。

    Returns:
        dataclass 实例对象。

    Raises:
        FileNotFoundError: 文件不存在时抛出。
        ValueError: 文件缺少必填字段时抛出。
        json.JSONDecodeError: JSON 格式解析失败时抛出。
    """
    # 确保 file_path 是 Path 对象，检查文件是否存在
    log.info(f"加载文件: {file_path}")
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")
    # 读取 JSON 文件内容
    with open(file_path, 'r', encoding='utf-8') as f:
        data: dict[str, Any] = json.load(f)
    # 校验必填字段
    check_dataclass_required_fields(data, data_class)
    return data_class(**data)

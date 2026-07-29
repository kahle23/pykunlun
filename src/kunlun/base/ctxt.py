# """
# 上下文模块

# 提供命令行上下文的抽象定义和基于命令行参数的上下文实现。
# """

# from abc import ABC, abstractmethod
# from typing import Any, Dict, List, Optional


# class Context(ABC):
#     """
#     上下文抽象基类

#     定义上下文对象的核心接口，提供统一的存储访问能力。
#     """

#     @abstractmethod
#     def get_storage(self) -> Dict[str, Any]:
#         """
#         获取上下文存储

#         Returns:
#             上下文存储的键值对映射
#         """
#         pass


# class CliContext(Context):
#     """
#     命令行上下文抽象类

#     基于命令行参数的上下文实现，提供初始化和销毁的生命周期管理。
#     """

#     def __init__(self) -> None:
#         self._storage: Dict[str, Any] = {}

#     def get_storage(self) -> Dict[str, Any]:
#         return self._storage

#     @abstractmethod
#     def init(self, args: List[str]) -> None:
#         """
#         初始化上下文

#         Args:
#             args: 命令行的所有入参
#         """
#         pass

#     @abstractmethod
#     def destroy(self, args: List[str], ex: Optional[Exception] = None) -> None:
#         """
#         销毁上下文

#         Args:
#             args: 命令行的入参数组
#             ex: 错误信息，可以为空
#         """
#         pass

# context 包使用指南

> 本文档详细记录 `pykunlun.context` 包的使用方法。

## 目录

- [Context - 上下文抽象基类](#1-context---上下文抽象基类)
- [ContextHolder - 当前值容器](#2-contextholder---当前值容器)

---

## 1. Context - 上下文抽象基类

上下文抽象基类（`pykunlun.context.base`），只约定一个通用键值存储 `get_storage()`，供跨切面状态挂载（如结果分隔符、输出编码等），具体键由上层约定。

具体形态（如命令行上下文 `CliContext`）由上层包继承提供；本包不持有「当前上下文」全局态。

```python
from collections.abc import MutableMapping
from typing import Any

from pykunlun.context import Context

class MyContext(Context):
    def __init__(self) -> None:
        self._storage: dict[str, Any] = {}

    def get_storage(self) -> MutableMapping[str, Any]:
        return self._storage

ctx = MyContext()
ctx.get_storage()["sep"] = "\n"
```

> 抽象基类不可直接实例化（`Context()` 抛 `TypeError`），实现 `get_storage` 即可作为具体上下文使用。

---

## 2. ContextHolder - 当前值容器

泛型「当前值」容器（`pykunlun.context.holder`）：封装一个 `ContextVar[T | None]`，提供 `get` / `set` / `reset` / `using`。

每个实例对应一个独立的「当前 X」槽位，按线程、asyncio Task 各自隔离、互不串扰（线程/异步安全由 `contextvars` 保证）。上层按需 new 出自己的 holder——例如 `pykunlun.cli` 用它承载「当前 CliContext」。

### 2.1 API 一览

| 方法/属性 | 说明 |
|------|------|
| `ContextHolder(name, default=None)` | 构造容器；`name` 为内部 `ContextVar` 名（调试用），`default` 为未 set 时的默认值 |
| `name` | 内部 `ContextVar` 的名字（只读属性） |
| `get()` | 获取当前值；未 set（或已 reset）时返回 `default`（默认 `None`） |
| `set(value)` | 设置当前值，返回 Token（需原样交给 `reset`） |
| `reset(token)` | 恢复到对应 `set` 之前的状态 |
| `using(value)` | 上下文管理器：with 块内生效、退出（含异常）自动还原 |

### 2.2 基本用法

```python
from pykunlun.context import ContextHolder

holder = ContextHolder[str]("my_name", default=None)

# 未 set 时返回默认值
holder.get()  # None

# set / reset 成对使用（token 原样回传）
token = holder.set("v1")
holder.get()  # "v1"
holder.reset(token)
holder.get()  # None
```

### 2.3 using 上下文管理器（推荐）

```python
from pykunlun.context import ContextHolder

holder = ContextHolder[str]("my_name")

with holder.using("v") as x:
    x == "v"          # yields 传入值本身，方便 as 写法
    holder.get()      # "v"
# 出了 with 自动还原（异常时同样还原）
holder.get()          # None
```

### 2.4 嵌套与隔离

```python
from pykunlun.context import ContextHolder

holder = ContextHolder[str]("my_name")

with holder.using("outer"):
    with holder.using("inner"):
        holder.get()  # "inner"
    holder.get()      # "outer"
holder.get()          # None
```

跨线程/协程天然隔离（`contextvars` 语义）：子线程、新 asyncio Task 看到的是各自上下文里的值，互不串扰。

---

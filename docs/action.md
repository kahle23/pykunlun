# action 包使用指南

> 本文档详细记录 `pykunlun.action` 包的使用方法。

## 目录

- [ActionManager - 动作管理器](#1-actionmanager---动作管理器)

---

## 1. ActionManager - 动作管理器

提供动作（Action）的注册、取消注册、获取和执行能力。动作为任意可调用对象，由 `name` 唯一标识，支持通配符查询名称；注册允许覆盖同名动作（返回被覆盖的旧动作，可用于包装增强后回注册）。

管理器为普通类、可多实例（各自持有独立的动作表），内部通过 `threading.Lock` 保证线程安全；`execute` 仅在锁内完成动作查找、在锁外调用动作本体（避免业务回调中再次访问动作表造成死锁）。

### 1.1 API 一览

| 方法 | 说明 |
|------|------|
| `register(name, action_obj)` | 注册动作，返回被覆盖的旧动作（无则为 `None`） |
| `unregister(name)` | 取消注册，返回被移除的动作（不存在则为 `None`） |
| `get_action(name)` | 获取单个动作，不存在返回 `None` |
| `has_action(name)` | 判断动作是否存在 |
| `get_names(pattern=None)` | 获取匹配的动作名称列表（按名称升序排序） |
| `clear(pattern=None)` | 清空动作，返回实际清除的数量 |
| `execute(name, *args, **kwargs)` | 执行单个动作，参数透传给底层可调用对象 |

### 1.2 注册与取消注册

```python
from pykunlun.action import ActionManager

manager = ActionManager()

def greet(name: str) -> str:
    return f"Hello, {name}!"

# 注册动作（允许覆盖同名，返回被覆盖的旧动作）
old = manager.register("greet", greet)

# 重复注册同名动作会覆盖前一次
previous = manager.register("greet", lambda n: f"Hi, {n}")
print(previous is greet)  # True

# 取消注册（返回被移除的动作，不存在时返回 None）
removed = manager.unregister("greet")
```

### 1.3 查询动作

```python
from pykunlun.action import ActionManager

manager = ActionManager()
# ... 注册动作 ...

# 判断动作是否存在
manager.has_action("greet")  # True / False

# 获取单个动作
fn = manager.get_action("greet")

# 获取全部动作名称（按名称升序排序）
all_names = manager.get_names()

# 按通配符查询动作名称（支持 * ?）
names = manager.get_names("gre*")   # ["greet"]
names = manager.get_names("*ea*")   # ["greet"]
```

### 1.4 执行动作

```python
from pykunlun.action import ActionManager

manager = ActionManager()

# 注册并执行
manager.register("add", lambda a, b: a + b)
result = manager.execute("add", 1, 2)
print(result)  # 3

# 动作不存在时抛出 KeyError
try:
    manager.execute("unknown")
except KeyError as e:
    print(e)
```

> 执行过程不在锁内调用动作本体，避免业务回调中再次访问动作表造成死锁；仅在锁内完成动作对象的查找。

### 1.5 清空动作

```python
# 清空匹配的动作，返回实际清除的数量
count = manager.clear("gre*")

# 清空全部动作
total = manager.clear()
```

### 1.6 实现替换与增强

业务代码只依赖动作名与 `ActionManager` 门面，不依赖具体实现模块；换实现、增强实现均不动业务代码：

```python
from pykunlun.action import ActionManager

manager = ActionManager()

def query_ip_v1(ip: str) -> str:
    return f"v1:{ip}"

manager.register("ip.query", query_ip_v1)

# 增强实现：覆盖注册拿回旧实现，包装后回注册（热替换链条成立）
old = manager.register("ip.query", lambda ip: f"v2:{ip}")  # old 即 query_ip_v1
manager.register("ip.query", lambda ip: f"{old(ip)} + cache")

# 业务代码不变，始终按名执行
manager.execute("ip.query", "1.2.3.4")
```

---

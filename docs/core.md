# core 模块使用指南

> 本文档详细记录 `kunlun.core` 包中各抽象模块的使用方法。

## 目录

- [action - 动作模块](#1-action---动作模块)
- [cli - 命令模块](#2-cli---命令模块)

---

## 1. action - 动作模块

提供动作（Action）的注册、取消注册、获取和执行能力。动作为任意可调用对象，由 `name` 唯一标识，支持通配符查询名称；注册允许覆盖同名动作。模块级单例，内部通过 `threading.Lock` 保证线程安全。

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
from kunlun.core import action

def greet(name: str) -> str:
    return f"Hello, {name}!"

# 注册动作（允许覆盖同名，返回被覆盖的旧动作）
old = action.register("greet", greet)

# 重复注册同名动作会覆盖前一次
previous = action.register("greet", lambda n: f"Hi, {n}")
print(previous is greet)  # True

# 取消注册（返回被移除的动作，不存在时返回 None）
removed = action.unregister("greet")
```

### 1.3 查询动作

```python
from kunlun.core import action

# 判断动作是否存在
action.has_action("greet")  # True / False

# 获取单个动作
fn = action.get_action("greet")

# 获取全部动作名称（按名称升序排序）
all_names = action.get_names()

# 按通配符查询动作名称（支持 * ?）
names = action.get_names("gre*")   # ["greet"]
names = action.get_names("*ea*")   # ["greet"]
```

### 1.4 执行动作

```python
from kunlun.core import action

# 注册并执行
action.register("add", lambda a, b: a + b)
result = action.execute("add", 1, 2)
print(result)  # 3

# 动作不存在时抛出 KeyError
try:
    action.execute("unknown")
except KeyError as e:
    print(e)
```

> 执行过程不在锁内调用动作本体，避免业务回调中再次访问动作表造成死锁；仅在锁内完成动作对象的查找。

### 1.5 清空动作

```python
from kunlun.core import action

# 清空匹配的动作，返回实际清除的数量
count = action.clear("gre*")

# 清空全部动作
total = action.clear()
```

---

## 2. cli - 命令模块

提供可扩展的命令系统，支持命令注册、查找、执行和帮助信息生成。

### 2.1 核心组件

| 组件 | 说明 |
|------|------|
| `Command` | 命令抽象基类，所有命令需继承此类 |
| `HelpCommand` | 帮助命令默认实现 |
| `CommandManager` | 命令注册、查找和执行管理器 |
| `CommandNotFoundError` | 命令未找到异常 |

### 2.2 定义自定义命令

```python
from kunlun.core.cli import Command, CommandManager

class GreetCommand(Command):
    @property
    def name(self) -> str:
        return "--greet"

    @property
    def abbr(self) -> str:
        return "-g"

    @property
    def description(self) -> str:
        return "打招呼"

    @property
    def usage(self) -> str:
        return "--greet [名字]"

    def execute(self, args: list[str]) -> str:
        name = args[0] if args else "World"
        return f"Hello, {name}!"

# 注册命令
manager = CommandManager()
manager.register(GreetCommand())
```

### 2.3 执行命令

```python
from kunlun.core.cli import CommandManager, CommandNotFoundError

manager = CommandManager()
# ... 注册命令 ...

# 通过名称执行
result = manager.execute_command("--greet", ["Alice"])

# 通过缩写执行
result = manager.execute_command("-g", ["Bob"])

# 命令不存在时抛出 CommandNotFoundError
try:
    manager.execute_command("--unknown", [])
except CommandNotFoundError as e:
    print(e)  # "未知命令: --unknown"
```

### 2.4 查询命令

```python
# 获取单个命令
cmd = manager.get_command("--greet")

# 获取所有命令（包括帮助命令）
all_cmds = manager.get_all_commands()

# 获取帮助命令实例
help_cmd = manager.get_help_command()
```

### 2.5 管理命令

```python
# 取消注册命令
manager.unregister("--greet")

# 清空所有命令
manager.clear()

# 设置自定义帮助命令
manager.set_help_command(MyHelpCommand(manager))
```

### 2.6 帮助命令

`CommandManager` 自动内置 `help` / `h` 命令。

```python
# 查看所有命令
manager.execute_command("help", [])

# 查看单个命令的帮助
manager.execute_command("help", ["--greet"])
```

### 2.7 命令行入口

`CommandManager.main_cli` 解析 `sys.argv` 并执行对应命令，支持启动/关闭回调。

```python
from kunlun.core.cli import CommandManager

manager = CommandManager()
# ... 注册命令 ...

# on_startup 在命令执行前调用（可抛异常中断）；on_shutdown 在 finally 中调用（不可抛异常）
manager.main_cli(on_startup=None, on_shutdown=None)
```

---
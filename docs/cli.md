# cli 包使用指南

> 本文档详细记录 `pykunlun.cli` 包的使用方法。

## 目录

- [cli - 命令模块](#1-cli---命令模块)

---

## 1. cli - 命令模块

提供可扩展的命令系统，支持命令注册、查找、执行和帮助信息生成。

### 1.1 核心组件

| 组件 | 说明 |
|------|------|
| `Command` | 命令抽象基类，所有命令需继承此类 |
| `HelpCommand` | 帮助命令默认实现 |
| `CommandManager` | 命令注册、查找和执行管理器 |
| `CommandNotFoundError` | 命令未找到异常 |

### 1.2 定义自定义命令

```python
from pykunlun.cli import Command, CommandManager

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

### 1.3 执行命令

```python
from pykunlun.cli import CommandManager, CommandNotFoundError

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

### 1.4 查询命令

```python
# 获取单个命令
cmd = manager.get_command("--greet")

# 获取所有命令（包括帮助命令）
all_cmds = manager.get_all_commands()

# 获取帮助命令实例
help_cmd = manager.get_help_command()
```

### 1.5 管理命令

```python
# 取消注册命令
manager.unregister("--greet")

# 清空所有命令
manager.clear()

# 设置自定义帮助命令
manager.set_help_command(MyHelpCommand(manager))
```

### 1.6 帮助命令

`CommandManager` 自动内置 `help` / `h` 命令。

```python
# 查看所有命令
manager.execute_command("help", [])

# 查看单个命令的帮助
manager.execute_command("help", ["--greet"])
```

### 1.7 命令行入口

`CommandManager.main_cli` 解析 `sys.argv` 并执行对应命令，支持启动/关闭回调。

```python
from pykunlun.cli import CommandManager

manager = CommandManager()
# ... 注册命令 ...

# on_startup 在命令执行前调用（可抛异常中断）；on_shutdown 在 finally 中调用（不可抛异常）
manager.main_cli(on_startup=None, on_shutdown=None)
```

---

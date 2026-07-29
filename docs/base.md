# base 模块使用指南

> 本文档详细记录 `kunlun.base` 包中各工具模块的使用方法。

## 目录

- [attr - 属性操作模块](#1-attr---属性操作模块)
- [util - 工具模块](#2-util---工具模块)
- [validate - 验证模块](#3-validate---验证模块)
- [log - 日志模块](#4-log---日志模块)
- [time - 时间模块](#5-time---时间模块)
- [action - 动作模块](#6-action---动作模块)

---

## 1. attr - 属性操作模块

提供统一的接口来操作对象或字典的属性，适用于 JSON 反序列化后可能是 dict 也可能是对象的场景。

### 1.1 API 一览

| 方法 | 说明 |
|------|------|
| `get_attr(obj, attr, default=None)` | 从对象或字典获取属性值，不存在时返回 `default` |
| `set_attr(obj, attr, value)` | 设置对象或字典的属性值 |
| `del_attr(obj, attr)` | 删除对象或字典的属性 |

### 1.2 读取属性

```python
from kunlun.base.attr import get_attr

# 对字典取值
d = {"name": "张三"}
get_attr(d, "name")           # "张三"
get_attr(d, "age", 0)         # 0（不存在时返回默认值）

# 对对象取值
class User:
    def __init__(self, name):
        self.name = name
get_attr(User("李四"), "name")  # "李四"

# obj 为 None 时返回默认值
get_attr(None, "name", "未知")  # "未知"
```

### 1.3 设置与删除属性

```python
from kunlun.base.attr import set_attr, del_attr

# 字典
d = {}
set_attr(d, "name", "张三")    # d == {"name": "张三"}
del_attr(d, "name")           # d == {}

# 对象
class User:
    pass
u = User()
set_attr(u, "name", "李四")
del_attr(u, "name")

# 对 None 操作会抛出 TypeError
# set_attr(None, "name", "x")  # TypeError: 无法对 None 设置属性
```

---

## 2. util - 工具模块

提供通用的工具方法，包括动态导入和配置加载。

### 2.1 动态导入模块

自动检测并安装缺失的 Python 包，安装失败时抛出 `ImportError`。

```python
from kunlun.base import util

# 导入标准库（已存在，直接返回）
json = util.import_module("json")

# 导入第三方包（未安装时自动安装）
numpy = util.import_module("numpy")

# 模块名和安装包名不同时，分别指定
cv2 = util.import_module("cv2", install_name="opencv-python")
```

### 2.2 从 JSON 文件加载 dataclass 配置

通用方法，适用于任何 `dataclass` 定义的配置类。自动校验必填字段，缺少字段时抛出 `ValueError`。

```python
from dataclasses import dataclass
from kunlun.base import util

@dataclass
class AppConfig:
    app_name: str
    debug: bool
    port: int = 8080

# 从 JSON 文件加载，自动校验 app_name、debug 是否存在
config = util.load_dataclass_from_json_file("config.json", AppConfig)
print(config.app_name, config.port)

# 也支持 Path 对象
from pathlib import Path
config = util.load_dataclass_from_json_file(Path("config.json"), AppConfig)
```

对应的 `config.json`：

```json
{
    "app_name": "my-app",
    "debug": true
}
```

### 2.3 创建模块懒加载器

生成一个 `__getattr__` 函数，用于实现模块属性的延迟导入。首次访问属性时才导入对应模块，导入后缓存到全局变量中，有效提升大型项目的启动性能。

```python
from kunlun.base import util

# 定义懒加载映射
_LAZY_IMPORTS = {
    "test": "mypkg.test.t1",
    "test1": "mypkg.test.t2",
}

# 创建懒加载函数
__getattr__ = util.create_lazy_loader(_LAZY_IMPORTS)

# 访问属性时才实际导入模块
# 例如：mypkg.test 访问时才会导入 mypkg.test.t1
```

---

## 3. validate - 验证模块

提供通用的数据验证方法，适用于 dataclass 等数据结构。

### 3.1 检查 dataclass 必填字段

检查数据字典是否包含构造 dataclass 所需的必填字段。

```python
from dataclasses import dataclass
from kunlun.base import validate

@dataclass
class UserConfig:
    name: str
    email: str
    age: int = 0

# 自动检测必填字段（name, email）
data = {"name": "张三"}
try:
    validate.check_dataclass_required_fields(data, UserConfig)
except ValueError as e:
    print(e)  # "UserConfig 缺少字段: email"

# 手动指定必填字段
data = {"name": "张三", "email": "test@example.com"}
validate.check_dataclass_required_fields(data, UserConfig, required_fields=["name"])
```

### 3.2 检查字段非空

检查对象的必填字段是否有值（非 None、非空字符串）。

```python
from kunlun.base import validate

class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

user = User("张三", "")

try:
    validate.check_required_fields_not_empty(user, ["name", "email"])
except ValueError as e:
    print(e)  # "User 字段 'email' 不能为空"

# 自定义错误提示上下文
try:
    validate.check_required_fields_not_empty(user, ["email"], context="用户信息")
except ValueError as e:
    print(e)  # "用户信息 字段 'email' 不能为空"
```

---

## 4. log - 日志模块

提供带颜色和级别的日志输出，支持 DEBUG / INFO / WARN / ERROR 四个级别，以及始终输出的 USAGE 级别。

### 4.1 日志级别管理

```python
from kunlun.base import log

# 查看当前日志级别（默认 "INFO"）
current_level = log.get_log_level()

# 设置日志级别
log.set_log_level("DEBUG")
log.set_log_level("WARN")
```

### 4.2 各级别日志输出

```python
from kunlun.base import log

log.debug("调试信息")      # 灰色，级别低于当前设置时不输出
log.info("普通信息")       # 绿色
log.warn("警告信息")       # 黄色
log.error("错误信息")      # 红色
log.usage("用法说明")      # 蓝色，始终输出，不受 LOG_LEVEL 限制
```

### 4.3 输出格式示例

```
[INFO ] 2024-01-15 14:30:00 - 普通信息
[WARN ] 2024-01-15 14:30:00 - 警告信息
[ERROR] 2024-01-15 14:30:00 - 错误信息
```

---

## 5. time - 时间模块

提供日期时间相关的工具方法，支持多种格式自动识别和转换。

### 5.1 解析时间字符串

自动识别多种日期时间格式，返回 `datetime` 对象。

```python
from kunlun.base import time

# 解析标准格式
dt = time.parse("2024-01-15 14:30:00")

# 解析 ISO 8601 格式
dt = time.parse("2024-01-15T14:30:00Z")

# 解析中文格式
dt = time.parse("2024年01月15日 14时30分00秒")

# 解析英文格式
dt = time.parse("January 15, 2024 14:30:00")

# 解析失败返回 None
dt = time.parse("invalid date")
```

### 5.2 解析日期和时间

```python
from kunlun.base import time

# 提取日期部分
d = time.parse_date("2024-01-15 14:30:00")  # 返回 date 对象

# 提取时间部分
t = time.parse_time("2024-01-15 14:30:00")  # 返回 time 对象
```

### 5.3 格式化时间对象

```python
from kunlun.base import time
from datetime import datetime, date

# 格式化 datetime 对象
dt = datetime.now()
result = time.format(dt)  # 默认格式: "%Y-%m-%d %H:%M:%S"
result = time.format(dt, fmt="%Y/%m/%d %H:%M")

# 格式化 date 对象
d = date.today()
result = time.format(d, fmt="%Y年%m月%d日")

# 格式化 None 返回 None
result = time.format(None)  # None
```

### 5.4 解析并格式化字符串

```python
from kunlun.base import time

# 解析后按指定格式输出
result = time.format_str("2024-01-15 14:30:00", fmt="%Y/%m/%d")
# 输出: "2024/01/15"

result = time.format_str("January 15, 2024", fmt="%Y-%m-%d")
# 输出: "2024-01-15"
```

### 5.5 管理时间格式

```python
from kunlun.base import time

# 获取所有支持的格式
formats = time.get_formats()

# 添加自定义格式
time.add_format("%Y年%m月%d日")

# 删除格式
time.remove_format("%Y年%m月%d日")

# 获取格式解析统计
stats = time.get_format_stats()

# 重置统计
time.reset_format_stats()

# 根据使用频率重新排序格式（提高解析效率）
time.reorder_formats()
```

### 5.6 支持的格式示例

| 类型 | 格式示例 |
|------|----------|
| ISO 8601 | `2024-01-15T14:30:00Z` |
| 标准格式 | `2024-01-15 14:30:00` |
| 中文格式 | `2024年01月15日 14时30分00秒` |
| 英文格式 | `January 15, 2024 14:30:00` |
| 美式格式 | `01/15/2024 02:30:00 PM` |
| 紧凑格式 | `20240115143000` |

---

## 6. action - 动作模块

提供动作（Action）的注册、取消注册、获取和执行能力。动作为任意可调用对象，由 `name` 唯一标识，支持通配符查询名称；注册允许覆盖同名动作。模块级单例，内部通过 `threading.Lock` 保证线程安全。

### 6.1 API 一览

| 方法 | 说明 |
|------|------|
| `register(name, action_obj)` | 注册动作，返回被覆盖的旧动作（无则为 `None`） |
| `unregister(name)` | 取消注册，返回被移除的动作（不存在则为 `None`） |
| `get_action(name)` | 获取单个动作，不存在返回 `None` |
| `has_action(name)` | 判断动作是否存在 |
| `get_names(pattern=None)` | 获取匹配的动作名称列表（按名称升序排序） |
| `clear(pattern=None)` | 清空动作，返回实际清除的数量 |
| `execute(name, *args, **kwargs)` | 执行单个动作，参数透传给底层可调用对象 |

### 6.2 注册与取消注册

```python
from kunlun.base import action

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

### 6.3 查询动作

```python
from kunlun.base import action

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

### 6.4 执行动作

```python
from kunlun.base import action

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

### 6.5 清空动作

```python
from kunlun.base import action

# 清空匹配的动作，返回实际清除的数量
count = action.clear("gre*")

# 清空全部动作
total = action.clear()
```

---

## 综合示例

### 示例 1：配置加载与验证

```python
from dataclasses import dataclass
from kunlun.base import util, validate

@dataclass
class DatabaseConfig:
    host: str
    port: int
    username: str
    password: str
    database: str

# 从 JSON 加载配置
config = util.load_dataclass_from_json_file("db_config.json", DatabaseConfig)

# 额外验证：检查字段非空
validate.check_required_fields_not_empty(
    config,
    ["host", "username", "password", "database"],
    context="数据库配置"
)
```

### 示例 2：动态导入并处理时间

```python
from kunlun.base import util, time

# 动态导入 pandas
pd = util.import_module("pandas")

# 解析时间字符串
date_str = "2024年01月15日"
dt = time.parse(date_str)

# 转换为 pandas Timestamp
timestamp = pd.Timestamp(dt)
print(timestamp)
```

### 示例 3：日志记录工具使用

```python
from kunlun.base import log
from kunlun.system import pip

log.info("开始安装依赖包")

packages = ["requests", "numpy", "pandas"]
success_list, fail_list = pip.install(packages)

log.info(f"成功安装 {len(success_list)} 个包")

if fail_list:
    log.warn(f"以下包安装失败:")
    for fail in fail_list:
        log.error(f"  - {fail}")
else:
    log.info("所有包安装成功")
```

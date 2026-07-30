# util 模块使用指南

> 本文档详细记录 `kunlun.util` 包中各工具模块的使用方法。

## 目录

- [obj_ops - 对象操作模块](#1-obj_ops---对象操作模块)
- [modutil 与 loadutil - 模块导入与数据加载工具](#2-modutil-与-loadutil---模块导入与数据加载工具)
- [validation - 验证模块](#3-validation---验证模块)
- [logutil - 日志工具模块](#4-logutil---日志工具模块)
- [time_ops - 时间模块](#5-time_ops---时间模块)
- [file_ops - 文件操作模块](#6-file_ops---文件操作模块)

---

## 1. obj_ops - 对象操作模块

提供统一的接口来操作对象或字典的属性，适用于 JSON 反序列化后可能是 dict 也可能是对象的场景。

### 1.1 API 一览

| 方法 | 说明 |
|------|------|
| `get_attr(obj, attr, default=None)` | 从对象或字典获取属性值，不存在时返回 `default` |
| `set_attr(obj, attr, value)` | 设置对象或字典的属性值 |
| `del_attr(obj, attr)` | 删除对象或字典的属性 |

### 1.2 读取属性

```python
from kunlun.util.obj_ops import get_attr

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
from kunlun.util.obj_ops import set_attr, del_attr

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

## 2. modutil 与 loadutil - 模块导入与数据加载工具

由原 miscutil 拆分而来：`modutil` 承接模块动态导入与懒加载，`loadutil` 承接从 JSON 文件加载 dataclass 配置。

### 2.1 动态导入模块（modutil.import_module）

自动检测并安装缺失的 Python 包，安装失败时抛出 `ImportError`。

```python
from kunlun.util import modutil

# 导入标准库（已存在，直接返回）
json = modutil.import_module("json")

# 导入第三方包（未安装时自动安装）
numpy = modutil.import_module("numpy")

# 模块名和安装包名不同时，分别指定
cv2 = modutil.import_module("cv2", install_name="opencv-python")
```

### 2.2 从 JSON 文件加载 dataclass 配置（loadutil.load_dataclass_from_json_file）

通用方法，适用于任何 `dataclass` 定义的配置类。自动校验必填字段，缺少字段时抛出 `ValueError`。

```python
from dataclasses import dataclass
from kunlun.util import loadutil

@dataclass
class AppConfig:
    app_name: str
    debug: bool
    port: int = 8080

# 从 JSON 文件加载，自动校验 app_name、debug 是否存在
config = loadutil.load_dataclass_from_json_file("config.json", AppConfig)
print(config.app_name, config.port)

# 也支持 Path 对象
from pathlib import Path
config = loadutil.load_dataclass_from_json_file(Path("config.json"), AppConfig)
```

对应的 `config.json`：

```json
{
    "app_name": "my-app",
    "debug": true
}
```

### 2.3 创建模块懒加载器（modutil.create_lazy_loader）

生成一个 `__getattr__` 函数，用于实现模块属性的延迟导入。首次访问属性时才导入对应模块，导入后缓存到全局变量中，有效提升大型项目的启动性能。

```python
from kunlun.util import modutil

# 定义懒加载映射
_LAZY_IMPORTS = {
    "test": "mypkg.test.t1",
    "test1": "mypkg.test.t2",
}

# 创建懒加载函数
__getattr__ = modutil.create_lazy_loader(_LAZY_IMPORTS)

# 访问属性时才实际导入模块
# 例如：mypkg.test 访问时才会导入 mypkg.test.t1
```

---

## 3. validation - 验证模块

提供通用的数据验证方法，适用于 dataclass 等数据结构。

### 3.1 检查 dataclass 必填字段

检查数据字典是否包含构造 dataclass 所需的必填字段。

```python
from dataclasses import dataclass
from kunlun.util import validation

@dataclass
class UserConfig:
    name: str
    email: str
    age: int = 0

# 自动检测必填字段（name, email）
data = {"name": "张三"}
try:
    validation.check_dataclass_required_fields(data, UserConfig)
except ValueError as e:
    print(e)  # "UserConfig 缺少字段: email"

# 手动指定必填字段
data = {"name": "张三", "email": "test@example.com"}
validation.check_dataclass_required_fields(data, UserConfig, required_fields=["name"])
```

### 3.2 检查字段非空

检查对象的必填字段是否有值（非 None、非空字符串）。

```python
from kunlun.util import validation

class User:
    def __init__(self, name, email):
        self.name = name
        self.email = email

user = User("张三", "")

try:
    validation.check_required_fields_not_empty(user, ["name", "email"])
except ValueError as e:
    print(e)  # "User 字段 'email' 不能为空"

# 自定义错误提示上下文
try:
    validation.check_required_fields_not_empty(user, ["email"], context="用户信息")
except ValueError as e:
    print(e)  # "用户信息 字段 'email' 不能为空"
```

---

## 4. logutil - 日志工具模块

基于标准库 `logging` 的薄封装，提供默认配置和懒加载：首次调用 `getLogger` 时若尚未配置，自动套用默认配置（控制台输出 / INFO 级别 / 含时间和模块名的简洁格式）。应用入口可通过 `setup()` 传入自定义 dictConfig 覆盖默认行为。

### 4.1 获取 logger 并输出日志

每个模块通过 `getLogger(__name__)` 取得独立 logger，自动获得模块层级名称。

```python
from kunlun.util import logutil

log = logutil.getLogger(__name__)

log.debug("调试信息")       # 级别低于当前设置时不输出
log.info("普通信息")
log.warning("警告信息")
log.error("错误信息")
try:
    1 / 0
except ZeroDivisionError:
    log.exception("除零异常")  # 自动带完整堆栈
```

### 4.2 自定义配置（可选）

默认配置已能满足大多数场景；如需文件输出、按模块分级等，可在程序入口传入 dictConfig：

```python
from kunlun.util import logutil

config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"detailed": {
        "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    }},
    "handlers": {"console": {
        "class": "logging.StreamHandler", "formatter": "detailed",
    }},
    "root": {"level": "DEBUG", "handlers": ["console"]},
}

logutil.setup(config)   # 应早于任何 getLogger 调用
```

### 4.3 默认输出格式示例

```
[INFO] 2026-07-30 14:30:00 - myapp.db - 普通信息
[WARN] 2026-07-30 14:30:00 - myapp.db - 警告信息
[ERROR] 2026-07-30 14:30:00 - myapp.db - 错误信息
```

---

## 5. time_ops - 时间模块

提供日期时间相关的工具方法，支持多种格式自动识别和转换。

### 5.1 解析时间字符串

自动识别多种日期时间格式，返回 `datetime` 对象。

```python
from kunlun.util import time_ops

# 解析标准格式
dt = time_ops.parse("2024-01-15 14:30:00")

# 解析 ISO 8601 格式
dt = time_ops.parse("2024-01-15T14:30:00Z")

# 解析中文格式
dt = time_ops.parse("2024年01月15日 14时30分00秒")

# 解析英文格式
dt = time_ops.parse("January 15, 2024 14:30:00")

# 解析失败返回 None
dt = time_ops.parse("invalid date")
```

### 5.2 解析日期和时间

```python
from kunlun.util import time_ops

# 提取日期部分
d = time_ops.parse_date("2024-01-15 14:30:00")  # 返回 date 对象

# 提取时间部分
t = time_ops.parse_time("2024-01-15 14:30:00")  # 返回 time 对象
```

### 5.3 格式化时间对象

```python
from kunlun.util import time_ops
from datetime import datetime, date

# 格式化 datetime 对象
dt = datetime.now()
result = time_ops.format(dt)  # 默认格式: "%Y-%m-%d %H:%M:%S"
result = time_ops.format(dt, fmt="%Y/%m/%d %H:%M")

# 格式化 date 对象
d = date.today()
result = time_ops.format(d, fmt="%Y年%m月%d日")

# 格式化 None 返回 None
result = time_ops.format(None)  # None
```

### 5.4 解析并格式化字符串

```python
from kunlun.util import time_ops

# 解析后按指定格式输出
result = time_ops.format_str("2024-01-15 14:30:00", fmt="%Y/%m/%d")
# 输出: "2024/01/15"

result = time_ops.format_str("January 15, 2024", fmt="%Y-%m-%d")
# 输出: "2024-01-15"
```

### 5.5 管理时间格式

```python
from kunlun.util import time_ops

# 获取所有支持的格式
formats = time_ops.get_formats()

# 添加自定义格式
time_ops.add_format("%Y年%m月%d日")

# 删除格式
time_ops.remove_format("%Y年%m月%d日")

# 获取格式解析统计
stats = time_ops.get_format_stats()

# 重置统计
time_ops.reset_format_stats()

# 根据使用频率重新排序格式（提高解析效率）
time_ops.reorder_formats()
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

---

## 6. file_ops - 文件操作模块

提供目录清理、路径转换和文件读取三类能力。

### 6.1 API 一览

| 方法 / 常量 | 说明 |
|------|------|
| `RESOLVE_TYPE_CURRENT` (1) | 解析类型：当前工作目录 |
| `RESOLVE_TYPE_USER` (2) | 解析类型：用户主目录 |
| `RESOLVE_TYPE_APP_DATA` (3) | 解析类型：应用数据目录 |
| `remove_target_dirs(base_dir, name, recursive=False)` | 删除匹配名称的目录，返回删除数量 |
| `remove_suffix_dirs(base_dir, suffix, recursive=False)` | 删除匹配后缀的目录，返回删除数量 |
| `resolve_path(relative_path, resolve_type=1, app_name=None)` | 相对路径按解析类型转换为绝对路径 |
| `read_file_stream(file_path)` | 读取文件返回二进制流（调用方负责关闭） |
| `read_file_text(file_path, encoding='utf-8')` | 读取文件返回字符串 |

### 6.2 清理构建产物

按目录名或后缀删除目录，可选递归。常用于清理 `__pycache__`、`build`、`*.egg-info`。

```python
from kunlun.util.file_ops import remove_target_dirs, remove_suffix_dirs

# 删除顶层的 build 目录
remove_target_dirs("/path/to/project", "build")

# 递归删除所有 __pycache__ 目录
remove_target_dirs("/path/to/project", "__pycache__", recursive=True)

# 删除顶层的 .egg-info 目录
remove_suffix_dirs("/path/to/project", ".egg-info")
```

> 目录不存在抛出 `FileNotFoundError`；权限不足仅告警不抛出。

### 6.3 路径转换

将相对路径按解析类型拼接到对应基准目录，返回规范化的绝对路径（仅转换，不校验存在性）。

```python
from kunlun.util.file_ops import resolve_path, RESOLVE_TYPE_USER, RESOLVE_TYPE_APP_DATA

# 相对当前工作目录（默认）
resolve_path("config.ini")
# 'C:\\Proj\\config.ini'

# 相对用户主目录
resolve_path("aa/test.cfg", RESOLVE_TYPE_USER)
# '/home/user/aa/test.cfg'

# 相对应用数据目录（跨平台），app_name 缺省时自动取调用者顶级包名
resolve_path("config.ini", RESOLVE_TYPE_APP_DATA, app_name="myapp")
# 'C:\\Users\\xxx\\AppData\\Roaming\\myapp\\config.ini'
```

> 传入绝对路径或 `resolve_type` 非法时抛出 `ValueError`。

### 6.4 读取文件

自动判断绝对/相对路径（相对路径基于当前工作目录解析）。

```python
from kunlun.util.file_ops import read_file_stream, read_file_text

# 读取为字符串（自动关闭句柄）
content = read_file_text("config.ini")
content = read_file_text("/etc/hosts", encoding="utf-8")

# 读取为字节流（调用方负责关闭，推荐配合 with）
with read_file_stream("config.ini") as f:
    data = f.read()
```

> 文件不存在抛出 `FileNotFoundError`；解码失败抛出 `UnicodeDecodeError`。

---

## 综合示例

### 示例 1：配置加载与验证

```python
from dataclasses import dataclass
from kunlun.util import loadutil, validation

@dataclass
class DatabaseConfig:
    host: str
    port: int
    username: str
    password: str
    database: str

# 从 JSON 加载配置
config = loadutil.load_dataclass_from_json_file("db_config.json", DatabaseConfig)

# 额外验证：检查字段非空
validation.check_required_fields_not_empty(
    config,
    ["host", "username", "password", "database"],
    context="数据库配置"
)
```

### 示例 2：动态导入并处理时间

```python
from kunlun.util import modutil, time_ops

# 动态导入 pandas
pd = modutil.import_module("pandas")

# 解析时间字符串
date_str = "2024年01月15日"
dt = time_ops.parse(date_str)

# 转换为 pandas Timestamp
timestamp = pd.Timestamp(dt)
print(timestamp)
```

### 示例 3：日志记录工具使用

```python
from kunlun.util import logutil
from kunlun.system import pip

log = logutil.getLogger(__name__)

log.info("开始安装依赖包")

packages = ["requests", "numpy", "pandas"]
success_list, fail_list = pip.install(packages)

log.info(f"成功安装 {len(success_list)} 个包")

if fail_list:
    log.warning(f"以下包安装失败:")
    for fail in fail_list:
        log.error(f"  - {fail}")
else:
    log.info("所有包安装成功")
```

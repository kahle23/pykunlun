"""
时间处理模块，提供日期时间字符串的自动解析和格式化功能。

内置丰富的格式模式库（支持 ISO 8601、中文、英文等多种格式），
具备自适应排序优化机制，可根据使用频率提升解析效率。
"""

from datetime import date, datetime, time

# 时间格式列表，按优先级排序（datetime 格式在前）
# 优先级：明确性 > 常用性 > 标准性
# 格式：{格式字符串: 解析成功次数}
_TIME_FORMATS: dict[str, int] = {
    # ===== 日期时间格式 =====
    # ISO 8601 标准（优先级最高，国际通用）
    "%Y-%m-%dT%H:%M:%S.%fZ": 0,          # ISO 8601 带微秒 UTC
    "%Y-%m-%dT%H:%M:%SZ": 0,             # ISO 8601 UTC
    "%Y-%m-%dT%H:%M:%S.%f%z": 0,         # ISO 8601 带微秒和时区偏移
    "%Y-%m-%dT%H:%M:%S%z": 0,            # ISO 8601 带时区偏移
    "%Y-%m-%dT%H:%M:%S.%f": 0,           # ISO 8601 带微秒
    "%Y-%m-%dT%H:%M:%S": 0,              # ISO 8601 基本格式
    # 常见标准格式
    "%Y-%m-%d %H:%M:%S": 0,              # 最常见的日期时间格式
    "%Y-%m-%d %H:%M": 0,                 # 常见格式（无秒）
    "%Y/%m/%d %H:%M:%S": 0,              # 斜杠分隔
    "%Y/%m/%d %H:%M": 0,                 # 斜杠分隔（无秒）
    "%Y.%m.%d %H:%M:%S": 0,              # 点号分隔（欧洲常见）
    "%Y.%m.%d %H:%M": 0,                 # 点号分隔（无秒）
    "%Y%m%d%H%M%S": 0,                   # 紧凑格式
    "%Y%m%dT%H%M%S": 0,                  # 紧凑 ISO 风格
    # 中文格式
    "%Y年%m月%d日 %H时%M分%S秒": 0,        # 中文完整日期时间
    "%Y年%m月%d日 %H时%M分": 0,            # 中文日期时间（无秒）
    "%Y年%m月%d日 %H:%M:%S": 0,           # 中文日期时间（冒号分隔）
    "%Y年%m月%d日 %H:%M": 0,              # 中文日期时间（无秒）
    "%Y年%m月%d日": 0,                    # 中文日期
    "%m月%d日 %H:%M:%S": 0,               # 中文短日期时间
    "%m月%d日 %H:%M": 0,                  # 中文短日期时间（无秒）
    "%m月%d日": 0,                        # 中文短日期
    # 英文自然语言格式
    "%B %d, %Y %H:%M:%S": 0,             # 完整月份名
    "%B %d, %Y %H:%M": 0,                # 完整月份名（无秒）
    "%b %d, %Y %H:%M:%S": 0,             # 缩写月份名
    "%b %d, %Y %H:%M": 0,                # 缩写月份名（无秒）
    "%d %B %Y %H:%M:%S": 0,              # 欧洲风格完整月份
    "%d %b %Y %H:%M:%S": 0,              # 欧洲风格缩写月份
    # 12小时制 AM/PM 格式
    "%Y-%m-%d %I:%M:%S %p": 0,           # 12小时制
    "%Y-%m-%d %I:%M %p": 0,              # 12小时制（无秒）
    "%Y/%m/%d %I:%M:%S %p": 0,           # 斜杠分隔 12小时制
    "%Y/%m/%d %I:%M %p": 0,              # 斜杠分隔 12小时制（无秒）
    "%m/%d/%Y %I:%M:%S %p": 0,           # 美式 12小时制
    "%m/%d/%Y %I:%M %p": 0,              # 美式 12小时制（无秒）
    # 欧洲日期时间格式（日在前）
    "%d/%m/%Y %H:%M:%S": 0,              # 欧式日期时间
    "%d/%m/%Y %H:%M": 0,                 # 欧式日期时间（无秒）
    "%d.%m.%Y %H:%M:%S": 0,              # 德式日期时间
    "%d.%m.%Y %H:%M": 0,                 # 德式日期时间（无秒）
    # 美式日期时间格式（月在前）
    "%m/%d/%Y %H:%M:%S": 0,              # 美式日期时间
    "%m/%d/%Y %H:%M": 0,                 # 美式日期时间（无秒）

    # ===== 日期格式 =====
    # ISO 日期
    "%Y-%m-%d": 0,                       # ISO 日期（最常用）
    "%Y/%m/%d": 0,                       # 斜杠分隔
    "%Y.%m.%d": 0,                       # 点号分隔
    "%Y%m%d": 0,                         # 紧凑日期
    # 英文自然语言日期
    "%B %d, %Y": 0,                      # 完整月份名 "January 15, 2024"
    "%B %d %Y": 0,                       # 完整月份名（无逗号）
    "%b %d, %Y": 0,                      # 缩写月份名 "Jan 15, 2024"
    "%b %d %Y": 0,                       # 缩写月份名（无逗号）
    "%d %B %Y": 0,                       # 欧洲风格 "15 January 2024"
    "%d %b %Y": 0,                       # 欧洲风格缩写 "15 Jan 2024"
    # 数字日期（歧义格式放后面）
    "%m/%d/%Y": 0,                       # 美式日期（月/日/年）
    "%d/%m/%Y": 0,                       # 欧式日期（日/月/年）
    "%d.%m.%Y": 0,                       # 德式日期（日.月.年）
    "%m-%d-%Y": 0,                       # 美式横杠分隔
    "%d-%m-%Y": 0,                       # 欧式横杠分隔
    "%d-%b-%y": 0,                       # 日-缩写月-两位年 "15-Jan-24"
    "%m/%d/%y": 0,                       # 美式两位年
    "%d/%m/%y": 0,                       # 欧式两位年
    "%y-%m-%d": 0,                       # 两位年 ISO 风格

    # ===== 时间格式 =====
    "%H:%M:%S": 0,                       # 24小时制（最常用）
    "%H:%M": 0,                          # 24小时制（无秒）
    "%H:%M:%S.%f": 0,                    # 24小时制带微秒
    "%H时%M分%S秒": 0,                    # 中文时间格式
    "%H时%M分": 0,                        # 中文时间格式（无秒）
    "%I:%M:%S %p": 0,                    # 12小时制 "02:30:00 PM"
    "%I:%M %p": 0,                       # 12小时制（无秒）"2:30 PM"
    "%H%M%S": 0,                         # 紧凑时间
}


def add_format(fmt: str, count: int = 0) -> None:
    """
    添加时间格式到格式列表。

    Args:
        fmt: 时间格式字符串，如 "%Y-%m-%d %H:%M:%S"。
        count: 解析成功次数，默认 0。
    """
    if fmt not in _TIME_FORMATS:
        _TIME_FORMATS[fmt] = count


def remove_format(fmt: str) -> bool:
    """
    从格式列表中删除指定格式。

    Args:
        fmt: 要删除的时间格式字符串。

    Returns:
        是否删除成功。
    """
    if fmt in _TIME_FORMATS:
        del _TIME_FORMATS[fmt]
        return True
    return False


def get_formats() -> list[str]:
    """
    获取时间格式列表。

    Returns:
        时间格式列表。
    """
    return list(_TIME_FORMATS.keys())


def get_format_stats() -> dict[str, int]:
    """
    获取格式解析成功次数统计。

    Returns:
        格式解析成功次数字典。
    """
    return _TIME_FORMATS.copy()


def reset_format_stats() -> None:
    """
    重置格式解析成功次数统计。
    """
    for fmt in _TIME_FORMATS:
        _TIME_FORMATS[fmt] = 0


def reorder_formats() -> None:
    """
    根据解析成功次数重新排序格式列表。

    将解析成功次数多的格式移到前面，提高解析效率。
    """
    # 按解析成功次数降序排序，次数相同的保持原有顺序
    sorted_items = sorted(_TIME_FORMATS.items(), key=lambda x: x[1], reverse=True)
    _TIME_FORMATS.clear()
    _TIME_FORMATS.update(sorted_items)


def parse(time_str: str) -> datetime | None:
    """
    解析日期/时间字符串，自动识别格式并返回 datetime。

    Args:
        time_str: 日期或时间字符串。
            日期示例: "2024-01-15", "01/15/2024", "January 15, 2024"
            时间示例: "14:30:00", "2:30 PM"
            日期时间示例: "2024-01-15 14:30:00", "2024-01-15T14:30:00Z"

    Returns:
        datetime 对象，解析失败返回 None。
    """
    # 空字符串返回 None
    if not time_str:
        return None
    # 去掉首尾空格
    time_str = time_str.strip()
    # 尝试解析
    for fmt, count in _TIME_FORMATS.items():
        try:
            result = datetime.strptime(time_str, fmt)
            # 解析成功，更新统计
            _TIME_FORMATS[fmt] = count + 1
            # 定期重新排序格式列表（每 100 次成功解析后）
            if _TIME_FORMATS[fmt] % 100 == 0:
                reorder_formats()
            return result
        except ValueError:
            continue
    # 解析失败返回 None
    return None


def parse_date(time_str: str) -> date | None:
    """
    解析日期/时间字符串并提取 date 部分。

    Args:
        time_str: 日期或时间字符串。
            日期示例: "2024-01-15", "01/15/2024", "January 15, 2024"
            日期时间示例: "2024-01-15 14:30:00"

    Returns:
        date 对象，解析失败返回 None。
    """
    dt = parse(time_str)
    return dt.date() if dt else None


def parse_time(time_str: str) -> time | None:
    """
    解析日期/时间字符串并提取 time 部分。

    Args:
        time_str: 日期或时间字符串。
            时间示例: "14:30:00", "2:30 PM"
            日期时间示例: "2024-01-15 14:30:00"

    Returns:
        time 对象，解析失败返回 None。
    """
    dt = parse(time_str)
    return dt.time() if dt else None


def format(time_obj: date | time | datetime | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
    """
    格式化日期/时间对象为字符串。

    Args:
        time_obj: 日期时间对象，支持 date、time、datetime 三种类型。
        fmt: 格式字符串，如 "%Y-%m-%d %H:%M:%S"。

    Returns:
        格式化后的字符串，time_obj 为 None 时返回 None。
    """
    # 空对象返回 None
    if time_obj is None:
        return None
    # 格式化对象
    return time_obj.strftime(fmt)


def format_str(time_str: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str | None:
    """
    解析日期/时间字符串后按指定格式输出。

    Args:
        time_str: 日期或时间字符串。
            日期示例: "2024-01-15", "01/15/2024", "January 15, 2024"
            时间示例: "14:30:00", "2:30 PM"
            日期时间示例: "2024-01-15 14:30:00", "2024-01-15T14:30:00Z"
        fmt: 输出格式字符串，如 "%Y-%m-%d %H:%M:%S"。

    Returns:
        格式化后的字符串，解析失败返回 None。
    """
    # 解析字符串
    if not time_str:
        return None
    # 去掉首尾空格
    time_str = time_str.strip()
    # 解析字符串
    dt = parse(time_str)
    # 解析失败返回 None
    if dt is None:
        return None
    # 格式化对象
    return dt.strftime(fmt)

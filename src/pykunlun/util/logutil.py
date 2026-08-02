"""
基于标准库 logging 的日志工具。

提供默认配置和懒加载：首次调用 getLogger 时若尚未配置，自动套用
默认配置（控制台输出 / INFO 级别 / 含时间和模块名的简洁格式）。
应用入口可通过 setup() 传入自定义 dictConfig 覆盖默认行为。

用法：
    from pykunlun.util import logutil
    log = logutil.getLogger(__name__)
    log.info("...")
"""
import logging
import logging.config

# 默认配置：控制台 + 简洁格式（带时间和模块名）
_DEFAULT_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[%(levelname)s] %(asctime)s - %(name)s - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "stream": "ext://sys.stderr",
        },
    },
    "root": {
        "level": "INFO",
        "handlers": ["console"],
    },
}

# 是否已应用过配置
_configured = False


def setup(config=None):
    """
    应用日志配置。

    传入自定义 dictConfig；传 None 则使用内置默认配置。
    应在程序入口调用一次，且早于任何 getLogger 调用。

    Args:
        config: dictConfig 字典，为 None 时使用默认配置。
    """
    global _configured
    # 让 WARNING 显示为 WARN，与 INFO 长度对齐（数字级别不变，不影响 setLevel("WARNING")）
    logging.addLevelName(logging.WARNING, "WARN")
    logging.config.dictConfig(config if config is not None else _DEFAULT_CONFIG)
    _configured = True


def getLogger(name=None):
    """
    获取 logger，首次调用且未配置时自动套用默认配置。

    Args:
        name: logger 名称，通常传 __name__ 以获得模块层级。

    Returns:
        logging.Logger 实例。
    """
    if not _configured:
        setup()
    return logging.getLogger(name)

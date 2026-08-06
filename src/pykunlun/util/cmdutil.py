"""
命令行执行辅助：安全打印（脱敏）命令与环境变量。

本模块收口"与外部命令交互"的通用辅助，避免各业务模块重复实现"脱敏后打印命令"的样板。
脱敏逻辑委托 :mod:`pykunlun.util.maskutil`，日志走 :mod:`pykunlun.util.logutil`。

:func:`log_command` 的 ``logger`` 参数由调用方按需传入，便于日志归属到业务模块
（缺省用本模块 logger）。
"""

import logging

from pykunlun.util import logutil, maskutil

log = logutil.getLogger(__name__)


def log_command(cmd: list[str], env: dict[str, str] | None = None,
                logger: logging.Logger | None = None) -> None:
    """
    打印脱敏后的命令与环境变量（用于 verbose 模式）。

    实际脱敏逻辑委托 :mod:`pykunlun.util.maskutil`：命令（``List[str]``）由命令脱敏
    策略屏蔽 ``--password=`` / ``-pXXX`` 等密码参数，环境变量（``Dict[str, str]``）
    由环境变量脱敏策略屏蔽敏感键的值。

    Args:
        cmd: 命令参数列表。
        env: 环境变量字典，``None`` 或空字典表示无环境变量。
        logger: 日志对象，``None`` 时用本模块 logger。调用方可传入业务模块自身的
            logger 使日志归属到该模块。
    """
    lg = logger or log
    lg.info(f"执行命令: {' '.join(maskutil.mask(cmd))}")
    masked_env = maskutil.mask(env)
    if masked_env:
        lg.info("环境变量: " + ", ".join(f"{k}={v}" for k, v in masked_env.items()))

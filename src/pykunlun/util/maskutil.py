"""
脱敏门面模块：默认管理器 + 命令行/环境变量脱敏策略与统一门面。

调用方只需 ``from pykunlun.util import maskutil``，一切脱敏统一走 :func:`mask` 自动探测：

  - **字符串值**：``mask('13812345678')`` → ``'138****5678'``；
  - **命令**（``List[str]``）：``mask(['mysqldump', '-psecret'])`` → ``['mysqldump', '-p***']``；
  - **环境变量**（``Dict[str, str]``）：``mask({'PGPASSWORD': 'pw', 'FOO': 'bar'})``
    → ``{'PGPASSWORD': '***', 'FOO': 'bar'}``；
  - **按名显式脱敏**：``mask_by_name('email', 'a@b.com')`` → ``'a****@b.com'``。

扩展点：

  - ``register_masker(...)`` 登记自定义 :class:`Masker`；
  - 取已注册实例后按需扩展，例如 ``get_masker('cmd_password').register_flag(...)``
    追加命令行工具的紧凑密码短标志，``get_masker('env').register_sensitive_key(...)``
    追加敏感环境变量键名。

抽象定义与内置数据策略（``Masker`` / ``MaskManager`` / ``PhoneMasker`` 等）见
:mod:`pykunlun.data.mask`；命令行/环境变量脱敏策略（:class:`CommandPasswordMasker` /
:class:`EnvMasker`）定义在本模块并注册到默认 :data:`mask_manager`。:func:`mask` 等门面
函数均转发到 :data:`mask_manager`。
"""

import os
from collections.abc import Iterable
from typing import Any

from pykunlun.data.mask import Masker, MaskManager

# region ======== 命令行密码脱敏策略 ========

class CommandPasswordMasker(Masker[list[str]]):
    """
    命令行密码脱敏策略（``Masker[List[str]]``）。

    把一条命令（参数列表）中携带密码的参数屏蔽为占位符（占位符 = 实例
    :attr:`~pykunlun.data.mask.Masker.mask_placeholder` 重复 3 次，默认 ``'***'``）：

      - ``--password=SECRET``（GNU 长形式，跨工具通用）**始终**屏蔽；
      - 紧凑短形式（如 mysqldump 的 ``-pSECRET``）按命令首段识别的工具名，命中本实例
        的紧凑密码短标志表时才屏蔽。

    之所以按工具名判定短形式：``-p<值>`` 在不同工具语义不同（mysqldump 是密码、
    psql 是端口），统一屏蔽会误伤端口等参数。按工具名查表可精准区分。

    本策略注册到 :data:`mask_manager`（名 ``'cmd_password'``）后，:func:`mask` 传入
    命令列表即可自动脱敏。需要换占位符时，实例化时传 ``mask_placeholder``（单字符，
    占位符为其重复 3 次）。
    """

    def __init__(self, name: str = 'cmd_password', priority: int = 300,
                 mask_placeholder: str | None = None) -> None:
        """
        Args:
            name: 策略名，默认 ``'cmd_password'``。
            priority: 优先级，默认 ``300``（高于所有内置 ``Masker[str]``，使命令这类
                非字符串值在自动探测时被本策略优先认领）。
            mask_placeholder: 单字符占位符（默认 ``'*'``），屏蔽值为其重复 3 次（``'***'``）。
        """
        super().__init__(name, priority, mask_placeholder)
        # 内置的"用紧凑短形式传密码"的工具名（规范化后）→ 标志前缀集合。
        # 仅登记确认用紧凑短形式传密码的工具；pg 家族（``-p`` 为端口）不登记。
        # 可通过 :meth:`register_flag` 扩展。
        self._tool_flags: dict[str, frozenset[str]] = {
            'mysqldump': frozenset({'-p'}),
            'mysql': frozenset({'-p'}),
            'mariadb-dump': frozenset({'-p'}),
            'mariadb': frozenset({'-p'}),
        }
        # ``--password=`` 长形式密码参数前缀集合（跨工具通用，命中即始终屏蔽）。
        # 可通过 :meth:`register_password_prefix` 扩展。
        self._password_long_prefixes: list[str] = ['--password=']

    def _normalize_tool_name(self, cmd0: str) -> str:
        """
        规范化命令首段为工具名：取 basename、去 ``.exe`` 后缀、转小写。

        Args:
            cmd0: 命令首段，可为纯工具名或绝对/相对路径。

        Returns:
            规范化后的工具名（如 ``mysqldump``）。
        """
        name = os.path.basename(cmd0)
        if name.lower().endswith('.exe'):
            name = name[:-len('.exe')]
        return name.lower()

    def register_flag(self, tool: str, flags: Iterable[str]) -> None:
        """
        登记某命令行工具的紧凑密码短标志前缀（重复登记覆盖旧值）。

        用于扩展内置未覆盖的工具（如某 CLI 用 ``-W<密码>``）。工具名经
        :meth:`_normalize_tool_name` 规范化（大小写不敏感、自动去路径与 ``.exe`` 后缀）。
        传空集合等价于取消该工具的登记。

        Args:
            tool: 工具名或可执行路径（如 ``'mysqldump'`` 或 ``'/usr/bin/mysqldump'``）。
            flags: 紧凑短形式标志前缀集合（如 ``{'-p'}``）。
        """
        self._tool_flags[self._normalize_tool_name(tool)] = frozenset(flags)

    def register_password_prefix(self, *prefixes: str) -> None:
        """
        追加长形式密码参数前缀（跨工具通用，命中即屏蔽，与工具名无关）。

        用于扩展内置未覆盖的长形式（如某 CLI 用 ``--passwd=``）。

        Args:
            *prefixes: 一个或多个长形式前缀（如 ``'--passwd='``）。
        """
        self._password_long_prefixes.extend(prefixes)

    def support(self, value: list[str]) -> bool:
        """
        仅认领"确有密码参数"的命令列表（类型 + 内容探测；且为 :meth:`apply` 屏蔽目标
        的超集，避免自动探测漏脱敏）。

        命中以下任一即认领：

          - 任一段以已登记的长形式前缀（:attr:`_password_long_prefixes`）开头；
          - 命令首段识别的工具名在 :attr:`_tool_flags` 中，且任一段以其某紧凑短标志开头
            （不校验 ``len(part) > len(flag)``：裸标志 apply 本就不屏蔽，认领后只走
            passthrough，绝对安全）。

        空列表、无密码参数的列表返回 ``False``，让出给其他策略或由管理器原样返回。
        """
        if not isinstance(value, list) or not value:
            return False
        # 长形式前缀命中（跨工具通用）
        if any(part.startswith(p) for part in value
               for p in self._password_long_prefixes):
            return True
        # 紧凑短形式：工具已登记且任一段命中其某 flag
        flags = self._tool_flags.get(self._normalize_tool_name(value[0]), frozenset())
        return any(part.startswith(f) for part in value for f in flags)

    def apply(self, cmd: list[str]) -> list[str]:
        """
        屏蔽命令中的密码参数，返回脱敏后的新命令列表（原输入不被修改）。

        屏蔽值统一为本实例占位符（``mask_placeholder`` 重复 3 次，默认 ``'***'``）。
        规则：``--password=`` 等已登记长形式前缀始终屏蔽；紧凑短形式按 ``cmd[0]`` 识别
        的工具名命中 :attr:`_tool_flags` 时屏蔽（且其后须有附加值，即
        ``len(part) > len(flag)``）；``cmd`` 为空时返回空列表。
        """
        placeholder = self.mask_placeholder * 3
        parts = list(cmd)
        flags = (self._tool_flags.get(self._normalize_tool_name(parts[0]), frozenset())
                 if parts else frozenset())

        masked: list[str] = []
        for part in parts:
            long_hit = next((p for p in self._password_long_prefixes
                             if part.startswith(p)), None)
            if long_hit is not None:
                masked.append(f'{long_hit}{placeholder}')
                continue
            # 紧凑短形式：命中已注册前缀且其后有附加值（len>len(flag)）才屏蔽
            hit = next((f for f in flags if part.startswith(f) and len(part) > len(f)), None)
            if hit is not None:
                masked.append(f'{hit}{placeholder}')
            else:
                masked.append(part)
        return masked

# endregion


# region ======== 环境变量脱敏策略 ========

class EnvMasker(Masker[dict[str, str]]):
    """
    环境变量脱敏策略（``Masker[Dict[str, str]]``）。

    将环境变量字典中**敏感键**的值替换为占位符（= 实例
    :attr:`~pykunlun.data.mask.Masker.mask_placeholder` 重复 3 次，默认 ``'***'``），其余键
    原样保留，返回新字典。键名匹配大小写不敏感。

    内置敏感键：``PGPASSWORD`` / ``MYSQL_PWD`` / ``PGPASSFILE``，
    可通过 :meth:`register_sensitive_key` 追加。本策略注册到 :data:`mask_manager`
    （名 ``'env'``）后，:func:`mask` 传入环境变量字典即可自动脱敏。
    """

    def __init__(self, name: str = 'env', priority: int = 300,
                 sensitive_keys: Iterable[str] | None = None,
                 mask_placeholder: str | None = None) -> None:
        """
        Args:
            name: 策略名，默认 ``'env'``。
            priority: 优先级，默认 ``300``（与 :class:`CommandPasswordMasker` 同级；二者
                按值类型——dict / list——互不冲突）。
            sensitive_keys: 视为敏感的键名集合（大小写不敏感），默认为
                ``PGPASSWORD`` / ``MYSQL_PWD`` / ``PGPASSFILE``。
            mask_placeholder: 单字符占位符（默认 ``'*'``），敏感值为其重复 3 次（``'***'``）。
        """
        super().__init__(name, priority, mask_placeholder)
        if sensitive_keys is None:
            sensitive_keys = {'PGPASSWORD', 'MYSQL_PWD', 'PGPASSFILE'}
        self._sensitive_upper: frozenset[str] = frozenset(k.upper() for k in sensitive_keys)

    def register_sensitive_key(self, *keys: str) -> None:
        """
        追加敏感环境变量键名（大小写不敏感，重复登记自动去重）。

        Args:
            *keys: 一个或多个敏感键名（如 ``'TOKEN'``、``'API_KEY'``）。
        """
        self._sensitive_upper = self._sensitive_upper | frozenset(k.upper() for k in keys)

    def support(self, value: dict[str, str]) -> bool:
        """
        仅认领"确含敏感键"的字典（类型 + 内容探测，键名大小写不敏感）。

        任一键名大写后命中 :attr:`_sensitive_upper` 即认领；空字典或无敏感键的字典
        返回 ``False``，让出给其他策略或由管理器原样返回。
        """
        if not isinstance(value, dict):
            return False
        return any(k.upper() in self._sensitive_upper for k in value)

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """
        返回脱敏后的环境变量副本：敏感键（大小写不敏感）的值替换为占位符，其余原样保留。

        输入为空字典时返回空字典；不修改原字典。
        """
        if not env:
            return {}
        placeholder = self.mask_placeholder * 3
        return {k: (placeholder if k.upper() in self._sensitive_upper else v)
                for k, v in env.items()}

# endregion


# region ======== 默认管理器 + 脱敏门面 ========

#: 默认脱敏管理器。``MaskManager()`` 已自动注册全部内置数据策略（phone/idcard/bankcard/
#: email/name/default），此处追加命令行与环境变量两个工具型策略。
mask_manager = MaskManager()
mask_manager.register_masker(CommandPasswordMasker())
mask_manager.register_masker(EnvMasker())


def register_masker(masker: Masker[Any]) -> Masker[Any] | None:
    """注册一个策略到 :data:`mask_manager`（转发，按策略名存放，允许覆盖同名）。

    Returns:
        被覆盖的旧策略；无旧值时为 ``None``。
    """
    return mask_manager.register_masker(masker)


def unregister_masker(name: str) -> Masker[Any] | None:
    """
    取消注册策略（转发）。

    Returns:
        被移除的策略；不存在时为 ``None``。
    """
    return mask_manager.unregister_masker(name)


def get_masker(name: str) -> Masker[Any] | None:
    """
    按名获取策略实例（不执行脱敏，转发）。

    Returns:
        策略实例；不存在时为 ``None``。
    """
    return mask_manager.get_masker(name)


def has_masker(name: str) -> bool:
    """
    判断策略是否已注册（转发）。
    """
    return mask_manager.has_masker(name)


def get_masker_names(name_pattern: str | None = None) -> list[str]:
    """
    列出已注册策略名（转发）。

    Args:
        name_pattern: 通配符模式（``*`` / ``?``）；为 ``None`` 返回全部。

    Returns:
        匹配的策略名列表（按名称升序）。
    """
    return mask_manager.get_masker_names(name_pattern)


def mask(value: Any) -> Any:
    """
    自动探测脱敏（委托 :data:`mask_manager`）。

    按优先级逐个试探已注册策略的 ``support``，首个命中者执行：

      - **字符串**值：手机号/身份证/银行卡/邮箱/姓名按对应规则脱敏，均不识别时由
        :class:`~pykunlun.data.mask.UniversalMasker` 兜底为 ``'***'``；
      - **命令**（``List[str]``）：由 :class:`CommandPasswordMasker` 屏蔽密码参数；
      - **环境变量**（``Dict[str, str]``）：由 :class:`EnvMasker` 屏蔽敏感键的值；
      - 其他未被任何策略认领的类型：原样返回（**识别不了就不乱改**）。

    Args:
        value: 原始值（任意类型）。

    Returns:
        脱敏后的值（类型由命中策略决定）；无命中时原样返回。

    Examples:
        >>> mask('13812345678')
        '138****5678'
        >>> mask(['mysqldump', '-psecret', 'db'])
        ['mysqldump', '-p***', 'db']
        >>> mask({'PGPASSWORD': 'pw', 'FOO': 'bar'})
        {'PGPASSWORD': '***', 'FOO': 'bar'}
    """
    return mask_manager.mask(value)


def mask_by_name(name: str, value: Any) -> Any:
    """
    按名显式脱敏（委托 :data:`mask_manager`，跳过 ``support`` 判定）。

    Args:
        name: 策略名（如 ``'phone'`` / ``'idcard'`` / ``'bankcard'`` / ``'email'`` /
            ``'name'`` / ``'default'`` / ``'cmd_password'`` / ``'env'``）。
        value: 原始值（类型应与该策略声明的 ``T`` 匹配）。

    Returns:
        脱敏后的值。

    Raises:
        KeyError: 策略未注册时抛出。
    """
    return mask_manager.mask_by_name(name, value)

# endregion

"""
脱敏抽象层与内置策略。

本模块定义抽象骨架并内置一组常用脱敏策略：

  - :class:`Masker`：脱敏策略抽象基类（泛型 ``Masker[T]``：判断 ``support`` + 处理
    ``apply``，入参与出参绑定同一类型 ``T``），并内置两个可复用的私有实例方法
    :meth:`Masker._mask_all` / :meth:`Masker._mask_part`（仅供 ``Masker[str]`` 子类复用）；
  - :class:`MaskManager`：策略编排器（注册表 + 自动探测/按名分发），其名称校验
    :meth:`MaskManager._resolve_name` 为可被子类覆写的实例方法；注册表在边界擦除为
    ``Masker[Any]``，故 :meth:`MaskManager.mask` / :meth:`MaskManager.mask_by_name`
    收发 ``Any``；
  - 内置策略：:class:`PhoneMasker` / :class:`IdCardMasker` / :class:`BankCardMasker` /
    :class:`EmailMasker` / :class:`NameMasker` / :class:`UniversalMasker`（均为
    ``Masker[str]``）。

默认管理器实例、命令行与环境变量脱敏、对外门面，由 :mod:`pykunlun.util.maskutil` 提供。
"""

import fnmatch
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

#: 脱敏策略的值类型参数。子类通过 ``Masker[str]`` / ``Masker[List[str]]`` 等声明自己
#: 处理的值类型，``support`` 的入参与 ``apply`` 的入参/出参均绑定到该 ``T``（入参出参
#: 同型）。注意：:class:`MaskManager` 的注册表需容纳不同 ``T`` 的策略，故在注册表边界
#: 擦除为 ``Masker[Any]``（等同 Java 的 ``Masker<?>``），类型安全仅在每个策略类的定义
#: 与直接使用处成立。
T = TypeVar('T')


# region ======== 脱敏策略抽象基类 ========

class Masker(ABC, Generic[T]):
    """
    脱敏策略抽象基类：判断 + 处理（泛型 ``Masker[T]``）。

    子类实现两个方法：

      - :meth:`support`：判断本策略是否适用于该值（内容/类型探测）；
      - :meth:`apply`：对值执行脱敏（假定已通过 ``support``），入参与出参同为 ``T``。

    子类通过继承 ``Masker[str]`` / ``Masker[List[str]]`` 等声明自己处理的值类型；
    ``support`` 与 ``apply`` 的入参（及 ``apply`` 的出参）都绑定到该 ``T``。常用场景为
    ``T=str``（手机号/身份证等数据值），命令行参数等复杂类型可用 ``Masker[List[str]]`` 等。

    ``name`` / ``priority`` / ``mask_placeholder`` 均为 **实例属性**，在 :meth:`__init__`
    中设置，以支持"同一策略类、不同实例参数不同"的动态场景（如同一手机号策略以不同名
    注册多份、或换用不同占位符/优先级）。子类按需覆盖 ``__init__`` 提供自己的默认值。
    ``Masker[str]`` 子类的 ``apply`` 通过两个私有实例方法 :meth:`_mask_all` /
    :meth:`_mask_part` 复用占位符（这两个方法仅适用于 ``str``，非 ``str`` 策略不复用）。
    本类不可直接实例化。

    Attributes:
        name: 策略名（注册到 :class:`MaskManager` 的键）。
        priority: 优先级（越大越先试探）。
        mask_placeholder: 脱敏占位字符（单个字符，默认 ``'*'``）；:meth:`_mask_all`
            将其重复 3 次（``'***'``），:meth:`_mask_part` 按被掩码长度逐字符重复。
    """

    def __init__(self, name: str | None = None, priority: int | None = None,
                 mask_placeholder: str | None = None) -> None:
        """
        Args:
            name: 策略名（注册到 :class:`MaskManager` 的键）；为空或纯空白时抛出。
            priority: 优先级（越大越先试探；同优先级按注册顺序）；为 ``None`` 时
                默认 ``100``。
            mask_placeholder: 本实例的脱敏占位字符（单个字符）；为空时默认 ``'*'``，
                :meth:`_mask_all` 重复 3 次得到 ``'***'``，:meth:`_mask_part`
                按被掩码字符数逐字符重复。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        # 校验并规范化策略名：去除前后空格，且不能为空
        if not name or not name.strip():
            raise ValueError("脱敏策略名 name 不能为空！")
        self.name = name
        # 校验并规范化优先级：为 ``None`` 时默认 ``100``
        if priority is None:
            priority = 100
        self.priority = priority
        # 校验并规范化占位符：为空时默认 ``'*'``
        if not mask_placeholder:
            mask_placeholder = '*'
        self.mask_placeholder = mask_placeholder

    def __repr__(self) -> str:
        """
        返回开发调试用的对象字符串表示。

        由 ``repr(obj)``、交互式回显、日志/断言输出等场景自动调用，区别于面向终端
        用户的 ``__str__``。覆盖默认的 ``<...object at 0x...>``，给出含类名、策略名
        与优先级的可读形式，便于在 :class:`MaskManager` 中排查已注册策略。

        Returns:
            形如 ``<PhoneMasker name='phone' priority=10>`` 的字符串。
        """
        return f'<{type(self).__name__} name={self.name!r} priority={self.priority}>'

    def _mask_all(self, value: str) -> str:
        """
        全量脱敏：将整段值替换为本实例 :attr:`mask_placeholder` 重复 3 次的结果
        （默认 ``'***'``），不保留任何原文字符。

        适用于密码、密钥、Token 等任何不应部分泄露的字段。无论原值长短、是否为空，
        一律返回占位符，表达"此处有值但已屏蔽"。

        Args:
            value: 原始值（内容不参与输出，仅用于统一签名）。

        Returns:
            占位符字符串。
        """
        return self.mask_placeholder * 3

    def _mask_part(self, value: str, keep_first: int = 0,
                   keep_last: int = 0) -> str:
        """
        中段脱敏：保留首尾各若干字符，中间替换为本实例的 :attr:`mask_placeholder`。

        适用于手机号、身份证、银行卡等"首尾有辨识度、中段敏感"的定长编码。被掩码区
        长度等于 ``len(value) - keep_first - keep_last``（透明长度：占位符数量与被掩
        字符数一致），按 :attr:`mask_placeholder` 逐字符重复填充。

        边界：当 ``keep_first + keep_last >= len(value)`` 时首尾区间重叠、无可掩码中段，
        原样返回（不追加占位符）。负的 ``keep_*`` 视为 0。

        Args:
            value: 原始值。
            keep_first: 保留的前缀字符数。
            keep_last: 保留的后缀字符数。

        Returns:
            首尾保留、中段掩码后的字符串。
        """
        keep_first = max(keep_first, 0)
        keep_last = max(keep_last, 0)
        end = len(value) - keep_last  # 保留后缀的起点
        if end <= keep_first:
            # 首尾区间重叠或相邻：没有可掩码的中段
            return value
        middle_len = end - keep_first
        return value[:keep_first] + self.mask_placeholder * middle_len + value[end:]

    @abstractmethod
    def support(self, value: T) -> bool:
        """
        判断本策略是否适用于 *value*（类型/内容探测）。

        由 :meth:`MaskManager.mask` 在自动探测时按优先级逐个调用，首个返回 ``True``
        者执行其 :meth:`apply`。子类通常先做类型判定（如 ``isinstance(value, str)``）
        再用正则/结构特征识别自己负责的数据形态——因为注册表混合了不同 ``T`` 的策略，
        自动探测时本方法可能收到非本策略期望类型的值，需自行排除。

        Args:
            value: 待判定的原始值（类型为本策略声明的 ``T``，自动探测时也可能是其他类型）。

        Returns:
            本策略可处理该值时为 ``True``，否则 ``False``。
        """
        raise NotImplementedError

    @abstractmethod
    def apply(self, value: T) -> T:
        """
        对 *value* 执行脱敏并返回结果（入参与出参同类型 ``T``）。

        假定调用前已通过 :meth:`support` 判定（或由 :meth:`MaskManager.mask_by_name`
        显式指定）。``Masker[str]`` 子类通常委托 :meth:`_mask_all` / :meth:`_mask_part`
        复用占位符。

        Args:
            value: 原始值。

        Returns:
            脱敏后的值（与入参同类型）。
        """
        raise NotImplementedError

# endregion


# region ======== 内置脱敏策略 ========

class PhoneMasker(Masker[str]):
    """
    手机号脱敏策略。

    识别 11 位中国大陆手机号（``1`` 开头共 11 位数字），脱敏时保留前 3 位与后 4 位，
    中段 4 位以占位符逐字符替换，例如 ``13812345678`` → ``138****5678``。
    """

    _RE = re.compile(r'^1\d{10}$')

    def __init__(self, name: str = 'phone', priority: int | None = None,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str) and bool(self._RE.match(value))

    def apply(self, value: str) -> str:
        return self._mask_part(value, 3, 4)


class IdCardMasker(Masker[str]):
    """
    身份证号脱敏策略。

    识别 18 位二代身份证（前 17 位数字 + 末位数字或 ``X/x``），脱敏时保留前 6 位
    （地区码）与后 4 位，中段 8 位以占位符逐字符替换。优先级高于
    :class:`BankCardMasker`，以消歧同为 18 位数字的银行卡号。
    """

    _RE = re.compile(r'^\d{17}[\dXx]$')

    def __init__(self, name: str = 'idcard', priority: int = 200,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str) and bool(self._RE.match(value))

    def apply(self, value: str) -> str:
        return self._mask_part(value, 6, 4)


class BankCardMasker(Masker[str]):
    """
    银行卡号脱敏策略。

    识别 16~19 位纯数字卡号，脱敏时保留前 4 位与后 4 位，中段以占位符逐字符替换，
    例如 ``6225881234567890`` → ``6225********7890``。
    """

    _RE = re.compile(r'^\d{16,19}$')

    def __init__(self, name: str = 'bankcard', priority: int | None = None,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str) and bool(self._RE.match(value))

    def apply(self, value: str) -> str:
        return self._mask_part(value, 4, 4)


class EmailMasker(Masker[str]):
    """
    邮箱地址脱敏策略。

    识别形如 ``local@domain.tld`` 的邮箱，脱敏时保留本地名首字符，其后追加 4 个
    占位符，再拼接完整域名（含 ``@``），例如 ``alice@example.com``
    → ``a****@example.com``。
    """

    _RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

    def __init__(self, name: str = 'email', priority: int | None = None,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str) and bool(self._RE.match(value))

    def apply(self, value: str) -> str:
        at = value.find('@')
        if at <= 0:
            # support 已保证有合法 @；此分支仅用于 mask_by_name 误用时稳健降级
            return self._mask_all(value)
        return value[0] + self.mask_placeholder * 4 + value[at:]


class NameMasker(Masker[str]):
    """
    姓名脱敏策略（启发式）。

    仅认领 2~4 个汉字的短串，脱敏时保留首字、其余逐字以占位符替换，例如
    ``张三`` → ``张*``、``王小明`` → ``王**``。因属启发式识别，优先级设低以
    避免误伤其他更确定的结构。
    """

    _RE = re.compile(r'^[\u4e00-\u9fff]{2,4}$')

    def __init__(self, name: str = 'name', priority: int = 50,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str) and bool(self._RE.match(value))

    def apply(self, value: str) -> str:
        # _mask_part(value, 1, 0) = 首字 + (len-1) 个 mask_placeholder
        return self._mask_part(value, 1, 0)


class UniversalMasker(Masker[str]):
    """
    全量脱敏策略（str 兜底）。

    :meth:`support` 对 ``str`` 恒为 ``True``（非 ``str`` 返回 ``False``，让出给其他
    ``T`` 的策略或由管理器原样返回），优先级最低（``0``）。当按优先级降序逐个试探时，
    它总是最后被命中，负责把所有未被其他策略认领的 **字符串** 整体替换为占位符，确保
    "识别不了也脱敏"。非字符串值不归本策略兜底。
    """

    def __init__(self, name: str | None = None, priority: int = 0,
                 mask_placeholder: str | None = None):
        super().__init__(name, priority, mask_placeholder)

    def support(self, value: str) -> bool:
        return isinstance(value, str)

    def apply(self, value: str) -> str:
        return self._mask_all(value)

# endregion


# region ======== 脱敏策略编排器 ========

class MaskManager:
    """
    脱敏策略编排器：注册若干 :class:`Masker`，按内容自动探测脱敏或按名显式分发。

    线程安全（锁内完成查找/注册，脱敏执行在锁外，避免回调访问注册表造成死锁）。
    """

    def __init__(self) -> None:
        """
        创建管理器并默认注册全部内置 **数据** 策略：:class:`IdCardMasker` /
        :class:`PhoneMasker` / :class:`BankCardMasker` / :class:`EmailMasker` /
        :class:`NameMasker` / :class:`UniversalMasker`（注册名 ``'default'``，str 兜底）。

        注意：这里只注册"数据值"层面的通用策略；命令行参数、环境变量等"工具型"策略
        （如 :class:`~pykunlun.util.maskutil.CommandPasswordMasker`）不属内置数据策略，
        由 :mod:`pykunlun.util.maskutil` 按需追加注册。之后可通过 :meth:`register_masker`
        追加或覆盖。
        """
        self._maskers: dict[str, Masker[Any]] = {}
        self._lock = threading.RLock()
        self.register_masker(IdCardMasker())
        self.register_masker(PhoneMasker())
        self.register_masker(BankCardMasker())
        self.register_masker(EmailMasker())
        self.register_masker(NameMasker())
        self.register_masker(UniversalMasker(name='default'))

    def _resolve_name(self, name: str) -> str:
        """
        校验并规范化策略名：去除前后空格，且不能为空。

        实例方法，子类可覆写以自定义名称规则（如加前缀、大小写归一化等）。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        stripped = name.strip()
        if not stripped:
            raise ValueError("脱敏策略名 name 不能为空")
        return stripped

    def register_masker(self, masker: Masker[Any]) -> Masker[Any] | None:
        """
        注册一个策略（按其 :attr:`Masker.name` 存放，允许覆盖同名）。

        Returns:
            被覆盖的旧策略；无旧值时为 ``None``。

        Raises:
            TypeError: masker 不是 :class:`Masker` 实例时抛出。
            ValueError: masker.name 为空时抛出。
        """
        if not isinstance(masker, Masker):
            raise TypeError(f"masker 必须是 Masker 实例，实际类型: {type(masker)}")
        key = self._resolve_name(masker.name)
        with self._lock:
            old = self._maskers.get(key)
            self._maskers[key] = masker
            return old

    def unregister_masker(self, name: str) -> Masker[Any] | None:
        """
        取消注册策略。

        Args:
            name: 策略名（前后空格会被去除）。

        Returns:
            被移除的策略；不存在时为 ``None``。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            return self._maskers.pop(key, None)

    def get_masker(self, name: str) -> Masker[Any] | None:
        """
        按名获取策略（不执行脱敏）。

        Args:
            name: 策略名（前后空格会被去除）。

        Returns:
            命中的策略实例；不存在时为 ``None``。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            return self._maskers.get(key)

    def has_masker(self, name: str) -> bool:
        """
        判断策略是否已注册。

        Args:
            name: 策略名（前后空格会被去除）。

        Returns:
            已注册返回 ``True``，否则 ``False``。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            return key in self._maskers

    def get_masker_names(self, name_pattern: str | None = None) -> list[str]:
        """
        列出已注册策略名。

        Args:
            name_pattern: 通配符模式（``*`` / ``?``）；为 ``None`` 返回全部。

        Returns:
            匹配的策略名列表（按名称升序）。
        """
        with self._lock:
            keys = list(self._maskers.keys())
        if name_pattern is None:
            return sorted(keys)
        return sorted(fnmatch.filter(keys, name_pattern))

    def mask(self, value: Any) -> Any:
        """
        自动探测脱敏：按优先级降序逐个试探 ``support``，首个命中者执行 ``apply``。

        排序对同优先级保持注册顺序（稳定排序）。均不命中时（例如传入未被任何策略
        认领的类型、或用户注销了兜底策略且无策略匹配）原样返回——**识别不了就不乱改**。

        Args:
            value: 原始值（任意类型；各策略在自己的 ``support`` 里做类型/内容判定）。

        Returns:
            脱敏后的值（类型由命中策略的 ``T`` 决定）；无命中时原样返回。
        """
        with self._lock:
            ordered = sorted(self._maskers.values(), key=lambda m: -m.priority)
        for masker in ordered:
            if masker.support(value):
                return masker.apply(value)
        return value

    def mask_by_name(self, name: str, value: Any) -> Any:
        """
        显式按名脱敏（跳过 ``support`` 判定，直接用指定策略处理）。

        适用于调用方明确知道字段类型的场景，如日志中对已知字段强制按 ``'phone'`` 脱敏。
        *value* 的类型应与该策略声明的 ``T`` 匹配，否则由策略自行处理（可能原样返回
        或抛错）。

        Raises:
            KeyError: 策略未注册时抛出。
            ValueError: name 为空时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            masker = self._maskers.get(key)
        if masker is None:
            raise KeyError(name)
        return masker.apply(value)

# endregion

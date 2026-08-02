"""
kunlun.util.maskutil 脱敏门面的单元测试。

覆盖：6 个内置数据策略（定义于 kunlun.data.mask）的 support/apply、命令行密码脱敏策略
CommandPasswordMasker、环境变量脱敏策略 EnvMasker、mask_manager 自动探测（含优先级
消歧与非字符串类型）、mask() / mask_by_name() 等转发门面、命令脱敏（含工具名识别）、
环境变量脱敏。
"""

from kunlun.util import maskutil
from kunlun.util import CommandPasswordMasker, EnvMasker
from kunlun.data.mask import (
    BankCardMasker,
    EmailMasker,
    IdCardMasker,
    NameMasker,
    PhoneMasker,
    UniversalMasker,
)

# region ======== 内置数据策略 ========

class TestPhoneMasker:
    def test_support_hit(self):
        assert PhoneMasker().support('13812345678') is True

    def test_support_miss(self):
        assert PhoneMasker().support('1381234567') is False     # 10 位
        assert PhoneMasker().support('23812345678') is False    # 首位非 1

    def test_support_rejects_non_str(self):
        """非字符串值（如命令列表）不应被字符串策略认领。"""
        assert PhoneMasker().support(['mysqldump', '-psecret']) is False
        assert PhoneMasker().support(13812345678) is False

    def test_apply(self):
        assert PhoneMasker().apply('13812345678') == '138****5678'


class TestIdCardMasker:
    def test_support_hit(self):
        assert IdCardMasker().support('110101199001011234') is True
        assert IdCardMasker().support('11010119900101123X') is True

    def test_support_miss(self):
        assert IdCardMasker().support('11010119900101123') is False   # 17 位

    def test_apply(self):
        assert IdCardMasker().apply('110101199001011234') == '110101********1234'


class TestBankCardMasker:
    def test_support_hit(self):
        assert BankCardMasker().support('6222123456789012') is True      # 16 位
        assert BankCardMasker().support('6222123456789012345') is True   # 19 位

    def test_support_miss_short(self):
        assert BankCardMasker().support('622212345678901') is False      # 15 位

    def test_apply(self):
        assert BankCardMasker().apply('6222123456789012') == '6222********9012'


class TestEmailMasker:
    def test_support_hit(self):
        assert EmailMasker().support('user@example.com') is True
        assert EmailMasker().support('a@b.cn') is True

    def test_support_miss(self):
        assert EmailMasker().support('plainvalue') is False
        assert EmailMasker().support('@example.com') is False
        assert EmailMasker().support('user@example') is False   # 域名无点号

    def test_apply(self):
        assert EmailMasker().apply('user@example.com') == 'u****@example.com'

    def test_apply_degrades_on_no_at(self):
        """apply 在无 @ 时稳健降级为全量脱敏（保护 mask_by_name 误用）。"""
        assert EmailMasker().apply('plain') == '***'


class TestNameMasker:
    def test_support_hit(self):
        assert NameMasker().support('张三') is True
        assert NameMasker().support('欧阳锋') is True

    def test_support_miss(self):
        assert NameMasker().support('张') is False          # 单字
        assert NameMasker().support('张三李四王五') is False  # 超过 4 字
        assert NameMasker().support('abc') is False

    def test_apply(self):
        assert NameMasker().apply('张三') == '张*'
        assert NameMasker().apply('欧阳锋') == '欧**'

    def test_apply_empty(self):
        assert NameMasker().apply('') == ''


class TestUniversalMasker:
    def test_support_always_true_for_str(self):
        assert UniversalMasker(name='all').support('anything') is True
        assert UniversalMasker(name='all').support('') is True

    def test_support_rejects_non_str(self):
        """UniversalMasker 只兜底字符串，非字符串让出给其他策略或原样返回。"""
        assert UniversalMasker(name='all').support(['a', 'b']) is False
        assert UniversalMasker(name='all').support(123) is False

    def test_apply(self):
        assert UniversalMasker(name='all').apply('secret') == '***'


class TestBuiltinPlaceholderConfig:
    """内置策略支持在实例化时配置占位符。"""

    def test_phone_custom_middle(self):
        assert PhoneMasker(mask_placeholder='#').apply('13812345678') == '138####5678'

    def test_all_custom_mask(self):
        assert UniversalMasker(name='all', mask_placeholder='?').apply('x') == '???'

    def test_email_custom_middle(self):
        assert EmailMasker(mask_placeholder='#').apply('a@b.com') == 'a####@b.com'

    def test_default_unchanged(self):
        """不传占位符时仍用默认值。"""
        assert PhoneMasker().apply('13812345678') == '138****5678'

# endregion


# region ======== mask_manager 自动探测 + 转发门面 ========

class TestMaskManagerAutoDetect:
    def test_has_all_builtins(self):
        assert set(maskutil.mask_manager.get_masker_names()) == {
            'phone', 'idcard', 'bankcard', 'email', 'name', 'default',
            'cmd_password', 'env',
        }

    def test_detect_phone(self):
        assert maskutil.mask('13812345678') == '138****5678'

    def test_detect_email(self):
        assert maskutil.mask('user@example.com') == 'u****@example.com'

    def test_detect_idcard(self):
        assert maskutil.mask('110101199001011234') == '110101********1234'

    def test_detect_bankcard_16_digit(self):
        assert maskutil.mask('6222123456789012') == '6222********9012'

    def test_priority_idcard_over_bankcard_for_18_digits(self):
        """18 位纯数字既匹配身份证又匹配银行卡，优先级高者（身份证）先认领。"""
        assert maskutil.mask('123456789012345678') == '123456********5678'

    def test_detect_name(self):
        assert maskutil.mask('张三') == '张*'

    def test_unknown_falls_back_to_all(self):
        assert maskutil.mask('unrecognized-data') == '***'
        assert maskutil.mask('') == '***'

    def test_mask_command_via_autodetect(self):
        """命令列表（List[str]）走同一 mask 入口，命中 cmd_password 策略。"""
        assert maskutil.mask(['mysqldump', '-psecret', 'db']) == ['mysqldump', '-p***', 'db']
        assert maskutil.mask(['t', '--password=x']) == ['t', '--password=***']

    def test_mask_env_via_autodetect(self):
        """环境变量字典走同一 mask 入口，命中 env 策略。"""
        assert maskutil.mask({'PGPASSWORD': 'pw', 'FOO': 'bar'}) \
            == {'PGPASSWORD': '***', 'FOO': 'bar'}

    def test_mask_unsupported_type_passthrough(self):
        """未被任何策略认领的类型原样返回（识别不了就不乱改）。"""
        assert maskutil.mask(12345) == 12345
        assert maskutil.mask(None) is None

    def test_mask_does_not_mutate_input(self):
        value = '13812345678'
        maskutil.mask(value)
        assert value == '13812345678'


class TestForwardingFacades:
    """register_masker/unregister_masker/get_masker/has_masker/get_masker_names 转发。"""

    def test_get_has_masker(self):
        assert maskutil.has_masker('phone') is True
        assert maskutil.has_masker('nope') is False
        assert maskutil.get_masker('phone') is not None
        assert maskutil.get_masker('nope') is None

    def test_get_masker_names_all_and_pattern(self):
        names = maskutil.get_masker_names()
        assert {'phone', 'cmd_password', 'env'} <= set(names)
        # fnmatch 的 ? 恰好匹配一个字符：5 字母名 → email / phone
        assert maskutil.get_masker_names('?????') == ['email', 'phone']

    def test_get_masker_then_configure(self):
        """取已注册命令策略实例，追加紧凑密码短标志后生效。"""
        tool = 'test-forward-cli'
        m = maskutil.get_masker('cmd_password')
        try:
            m.register_flag(tool, {'-W'})
            assert maskutil.mask([tool, '-Wsecret']) == [tool, '-W***']
        finally:
            m.register_flag(tool, set())


class TestMaskByNameFacade:
    def test_explicit_phone(self):
        assert maskutil.mask_by_name('phone', '13812345678') == '138****5678'

    def test_explicit_default(self):
        assert maskutil.mask_by_name('default', 'secret') == '***'

    def test_explicit_cmd_password(self):
        assert maskutil.mask_by_name('cmd_password', ['mysqldump', '-psecret']) \
            == ['mysqldump', '-p***']

    def test_explicit_env(self):
        assert maskutil.mask_by_name('env', {'PGPASSWORD': 'pw'}) == {'PGPASSWORD': '***'}

    def test_skips_support_judgment(self):
        """mask_by_name 不走 support，即便值不符合也照指定策略处理。"""
        assert maskutil.mask_by_name('phone', 'abc') == 'abc'

    def test_unknown_name_raises_keyerror(self):
        try:
            maskutil.mask_by_name('nope', 'x')
            assert False, '应抛 KeyError'
        except KeyError:
            pass

# endregion


# region ======== 命令行密码脱敏 ========

class TestCommandPasswordMasker:
    """CommandPasswordMasker 策略本身的 support/apply/register_flag/占位符。"""

    def test_support_list(self):
        assert CommandPasswordMasker().support(['mysqldump', '-psecret']) is True
        # 空列表 / 无密码参数的列表均不认领（严格内容探测）
        assert CommandPasswordMasker().support([]) is False
        assert CommandPasswordMasker().support(['ls', '-l']) is False

    def test_support_rejects_non_list(self):
        assert CommandPasswordMasker().support('mysqldump -psecret') is False
        assert CommandPasswordMasker().support(123) is False
        assert CommandPasswordMasker().support({'k': 'v'}) is False

    def test_apply_default_placeholder(self):
        assert CommandPasswordMasker().apply(['t', '--password=x']) == ['t', '--password=***']

    def test_apply_custom_mask_placeholder(self):
        """占位符 = mask_placeholder 重复 3 次。"""
        m = CommandPasswordMasker(mask_placeholder='#')
        assert m.apply(['mysqldump', '-psecret']) == ['mysqldump', '-p###']

    def test_register_flag_then_apply(self):
        m = CommandPasswordMasker()
        m.register_flag('custom-cli', {'-W'})
        assert m.apply(['custom-cli', '-Wsecret']) == ['custom-cli', '-W***']

    def test_register_flag_normalizes_tool_name(self):
        m = CommandPasswordMasker()
        m.register_flag('/opt/CUSTOM.exe', {'-x'})
        assert m.apply(['custom', '-xsecret']) == ['custom', '-x***']


class TestCommandMaskingLongForm:
    """--password= 长形式始终屏蔽（经 mask() 自动探测）。"""

    def test_long_form_masked(self):
        assert maskutil.mask(['mysqldump', '--password=secret', 'db']) \
            == ['mysqldump', '--password=***', 'db']

    def test_long_form_masked_even_for_unknown_tool(self):
        assert maskutil.mask(['weirdtool', '--password=p']) == ['weirdtool', '--password=***']

    def test_long_form_empty_value_still_masked(self):
        assert maskutil.mask(['t', '--password=']) == ['t', '--password=***']


class TestCommandMaskingCompactShort:
    """-p 紧凑短形式：按工具名注册表判定。"""

    def test_mysqldump_compact_masked(self):
        assert maskutil.mask(['mysqldump', '-psecret', 'db']) == ['mysqldump', '-p***', 'db']

    def test_mysql_client_compact_masked(self):
        assert maskutil.mask(['mysql', '-psecret']) == ['mysql', '-p***']

    def test_pg_dump_port_not_masked(self):
        assert maskutil.mask(['pg_dump', '-p5432', 'db']) == ['pg_dump', '-p5432', 'db']

    def test_psql_port_not_masked(self):
        assert maskutil.mask(['psql', '-p5432']) == ['psql', '-p5432']

    def test_unknown_tool_compact_not_masked(self):
        assert maskutil.mask(['mytool', '-psecret']) == ['mytool', '-psecret']

    def test_bare_short_flag_not_masked(self):
        """裸 -p（无附加值）不屏蔽：len==2，不满足 len>len(flag)。"""
        assert maskutil.mask(['mysqldump', '-p', 'db']) == ['mysqldump', '-p', 'db']


class TestCommandMaskingToolNormalization:
    def test_full_path(self):
        assert maskutil.mask(['/usr/bin/mysqldump', '-psecret']) \
            == ['/usr/bin/mysqldump', '-p***']

    def test_windows_exe_suffix(self):
        assert maskutil.mask(['mysqldump.exe', '-psecret']) == ['mysqldump.exe', '-p***']

    def test_uppercase_tool_name(self):
        assert maskutil.mask(['MYSQLDUMP', '-psecret']) == ['MYSQLDUMP', '-p***']

    def test_mariadb_variants(self):
        assert maskutil.mask(['mariadb', '-psecret']) == ['mariadb', '-p***']
        assert maskutil.mask(['mariadb-dump', '-psecret']) == ['mariadb-dump', '-p***']


class TestCommandMaskingEdgeCases:
    def test_empty_cmd(self):
        assert maskutil.mask([]) == []

    def test_mixed_command(self):
        assert maskutil.mask(
            ['mysqldump', '-u', 'root', '-psecret', '--password=x', 'db']
        ) == ['mysqldump', '-u', 'root', '-p***', '--password=***', 'db']

    def test_returns_new_list(self):
        src = ['t', '--password=secret']
        result = maskutil.mask(src)
        assert result == ['t', '--password=***']
        assert result is not src          # 必须是新列表
        assert src == ['t', '--password=secret']  # 原输入未被修改

# endregion


# region ======== 环境变量脱敏 ========

class TestEnvMasker:
    """EnvMasker 策略本身的 support/apply/register_sensitive_key/占位符。"""

    def test_support_dict(self):
        assert EnvMasker().support({'PGPASSWORD': 'pw'}) is True
        # 空字典 / 无敏感键的字典均不认领（严格内容探测）
        assert EnvMasker().support({}) is False
        assert EnvMasker().support({'FOO': 'bar'}) is False

    def test_support_rejects_non_dict(self):
        assert EnvMasker().support('PGPASSWORD=pw') is False
        assert EnvMasker().support(['PGPASSWORD']) is False
        assert EnvMasker().support(123) is False

    def test_apply_default_keys(self):
        assert EnvMasker().apply({'PGPASSWORD': 'pw', 'FOO': 'bar'}) \
            == {'PGPASSWORD': '***', 'FOO': 'bar'}

    def test_apply_case_insensitive(self):
        assert EnvMasker().apply({'pgpassword': 'pw'}) == {'pgpassword': '***'}
        assert EnvMasker().apply({'MySql_Pwd': 'pw'}) == {'MySql_Pwd': '***'}

    def test_apply_empty(self):
        assert EnvMasker().apply({}) == {}

    def test_apply_custom_mask_placeholder(self):
        m = EnvMasker(mask_placeholder='?')
        assert m.apply({'PGPASSWORD': 'x'}) == {'PGPASSWORD': '???'}

    def test_custom_sensitive_keys(self):
        m = EnvMasker(sensitive_keys=('token',))
        assert m.apply({'TOKEN': 'x', 'FOO': 'bar'}) == {'TOKEN': '***', 'FOO': 'bar'}
        # 默认键此时不生效（构造时整体替换）
        assert m.apply({'PGPASSWORD': 'pw'}) == {'PGPASSWORD': 'pw'}

    def test_register_sensitive_key_appends(self):
        m = EnvMasker()
        m.register_sensitive_key('TOKEN', 'API_KEY')
        result = m.apply({'TOKEN': 't', 'API_KEY': 'k', 'PGPASSWORD': 'p', 'FOO': 'f'})
        assert result == {'TOKEN': '***', 'API_KEY': '***', 'PGPASSWORD': '***', 'FOO': 'f'}

    def test_registered_in_mask_manager(self):
        assert maskutil.mask_by_name('env', {'PGPASSWORD': 'pw'}) == {'PGPASSWORD': '***'}


class TestEnvMaskingViaMask:
    """经 mask() 自动探测走 env 策略。"""

    def test_none_passthrough(self):
        """None 非字典，不被 env 策略认领，原样返回（区别于旧的 mask_env 返回 {}）。"""
        assert maskutil.mask(None) is None

    def test_empty_returns_empty(self):
        assert maskutil.mask({}) == {}

    def test_default_keys_masked(self):
        env = {'PGPASSWORD': 'a', 'MYSQL_PWD': 'b', 'PGPASSFILE': 'c', 'SAFE': 'd'}
        assert maskutil.mask(env) == {
            'PGPASSWORD': '***', 'MYSQL_PWD': '***', 'PGPASSFILE': '***', 'SAFE': 'd',
        }

    def test_returns_new_dict(self):
        src = {'PGPASSWORD': 'pw'}
        maskutil.mask(src)
        assert src == {'PGPASSWORD': 'pw'}

    def test_does_not_leak_secret(self):
        result = maskutil.mask({'PGPASSWORD': 'super-secret-value'})
        assert 'super-secret-value' not in result['PGPASSWORD']

# endregion

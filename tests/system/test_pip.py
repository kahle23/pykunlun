import subprocess
from unittest.mock import MagicMock, patch

from kunlun.system.pip import DEFAULT_MIRRORS, execute, install


class TestPipExecuteInstall:
    """测试 execute 的安装逻辑"""

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_success_first_mirror(self, mock_run):
        """测试第一个镜像安装成功"""
        mock_run.return_value.returncode = 0

        success, msg = execute('install', ['requests'], mirrors=DEFAULT_MIRRORS)

        assert success is True
        assert DEFAULT_MIRRORS[0] in msg
        mock_run.assert_called_once()

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_fail_first_success_second(self, mock_run):
        """测试第一个镜像失败，第二个镜像成功"""
        mock_run.side_effect = [
            Exception("Connection refused"),
            MagicMock(returncode=0)
        ]

        success, msg = execute('install', ['requests'], mirrors=DEFAULT_MIRRORS)

        assert success is True
        assert DEFAULT_MIRRORS[1] in msg
        assert mock_run.call_count == 2

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_all_mirrors_fail(self, mock_run):
        """测试所有镜像都失败"""
        mock_run.side_effect = Exception("Connection refused")

        success, msg = execute('install', ['requests'], mirrors=DEFAULT_MIRRORS)

        assert success is False
        assert "Command failed:" in msg
        assert "pip install" in msg
        assert mock_run.call_count == len(DEFAULT_MIRRORS)

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_timeout(self, mock_run):
        """测试安装超时"""
        mock_run.side_effect = [
            subprocess.TimeoutExpired(cmd=['python', '-m', 'pip'], timeout=60),
            MagicMock(returncode=0)
        ]

        success, msg = execute('install', ['requests'], timeout=60, mirrors=DEFAULT_MIRRORS)

        assert success is True
        assert DEFAULT_MIRRORS[1] in msg

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_with_custom_mirrors(self, mock_run):
        """测试使用自定义镜像列表"""
        custom_mirrors = ['https://custom-mirror.com/simple/']
        mock_run.return_value.returncode = 0

        success, msg = execute('install', ['requests'], mirrors=custom_mirrors)

        assert success is True
        assert custom_mirrors[0] in msg
        mock_run.assert_called_once()

    @patch('kunlun.system.pip.subprocess.run')
    def test_install_with_version(self, mock_run):
        """测试安装指定版本的包"""
        mock_run.return_value.returncode = 0

        success, msg = execute('install', ['requests==2.31.0'], mirrors=DEFAULT_MIRRORS)

        assert success is True
        assert 'requests==2.31.0' in msg

    @patch('kunlun.system.pip.subprocess.run')
    def test_upgrade_success(self, mock_run):
        """测试升级成功"""
        mock_run.return_value.returncode = 0

        success, msg = execute('install', ['requests', '--upgrade'], mirrors=DEFAULT_MIRRORS)

        assert success is True
        assert 'upgrade' in msg
        assert DEFAULT_MIRRORS[0] in msg

    @patch('kunlun.system.pip.subprocess.run')
    def test_uninstall_success_without_mirrors(self, mock_run):
        """测试卸载成功（不使用镜像）"""
        mock_run.return_value.returncode = 0

        success, msg = execute('uninstall', ['requests', '-y'])

        assert success is True
        assert 'uninstall' in msg
        assert 'requests' in msg

    @patch('kunlun.system.pip.subprocess.run')
    def test_uninstall_fail_without_mirrors(self, mock_run):
        """测试卸载失败（不使用镜像）"""
        mock_run.side_effect = subprocess.CalledProcessError(
            1, 'cmd', stderr='package not installed'
        )

        success, msg = execute('uninstall', ['requests', '-y'])

        assert success is False
        assert 'Command failed' in msg
        assert 'pip uninstall' in msg


class TestInstall:
    """测试 install 公开方法"""

    @patch('kunlun.system.pip.execute')
    def test_install_single_package(self, mock_execute):
        """测试安装单个包（字符串）"""
        mock_execute.return_value = (True, "success")

        result = install('requests')

        assert isinstance(result, tuple)
        assert result[0] is True
        mock_execute.assert_called_once_with('install', ['requests'], timeout=300, mirrors=DEFAULT_MIRRORS)

    @patch('kunlun.system.pip.execute')
    def test_install_multiple_packages_all_success(self, mock_execute):
        """测试批量安装所有包成功"""
        mock_execute.return_value = (True, "success")

        success_list, fail_list = install(['requests', 'pandas'])

        assert success_list == ['requests', 'pandas']
        assert fail_list == []
        assert mock_execute.call_count == 2

    @patch('kunlun.system.pip.execute')
    def test_install_multiple_packages_partial_success(self, mock_execute):
        """测试批量安装部分成功"""
        mock_execute.side_effect = [
            (True, "success"),
            (False, "fail")
        ]

        success_list, fail_list = install(['requests', 'nonexistent-package'])

        assert success_list == ['requests']
        assert len(fail_list) == 1
        assert 'nonexistent-package' in fail_list[0]

    @patch('kunlun.system.pip.execute')
    def test_install_empty_list(self, mock_execute):
        """测试安装空列表"""
        success_list, fail_list = install([])

        assert success_list == []
        assert fail_list == []
        mock_execute.assert_not_called()

    @patch('kunlun.system.pip.execute')
    def test_install_with_custom_timeout(self, mock_execute):
        """测试使用自定义超时时间"""
        mock_execute.return_value = (True, "success")

        install('requests', timeout=60)

        mock_execute.assert_called_once_with('install', ['requests'], timeout=60, mirrors=DEFAULT_MIRRORS)

    @patch('kunlun.system.pip.execute')
    def test_install_with_custom_mirrors(self, mock_execute):
        """测试使用自定义镜像列表"""
        custom_mirrors = ['https://custom.com/simple/']
        mock_execute.return_value = (True, "success")

        install('requests', mirrors=custom_mirrors)

        mock_execute.assert_called_once_with('install', ['requests'], timeout=300, mirrors=custom_mirrors)

    @patch('kunlun.system.pip.execute')
    def test_install_multiple_with_mirrors(self, mock_execute):
        """测试批量安装时传递自定义镜像"""
        custom_mirrors = ['https://custom.com/simple/']
        mock_execute.return_value = (True, "success")

        install(['a', 'b'], mirrors=custom_mirrors)

        calls = mock_execute.call_args_list
        for call in calls:
            assert call[1]['mirrors'] == custom_mirrors

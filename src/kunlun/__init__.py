"""
Kunlun — 与具体业务无关的底层能力库。

承载跨平台、跨业务的通用抽象与基础设施，不依赖上层应用包。上层包按需在此之上扩展具体实现。
"""

from .system import env, env_var, pip

# 不捕获 PackageNotFoundError：能执行到此处说明包已加载，版本缺失应报错而非静默回退
__version__ = env.get_package_version(env.get_own_top_package_name())

__all__ = ["env", "env_var", "pip"]

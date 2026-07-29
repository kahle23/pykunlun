"""
测试模块。

本目录存放 kunlun 的单元测试与集成测试，统一基于 pytest 组织。

目录约定：
    - 按源码子模块对应建子目录（如 base/、db/），测试文件以 test_ 前缀命名
    - 跨模块通用的测试辅助（fixture、工厂、断言工具）置于顶层或 _helpers/

运行方式：
    python -m pytest tests/                 运行全部测试
    python -m pytest tests/base -v          运行指定子目录并输出详情
    python -m pytest tests/base/test_xxx.py 运行指定测试文件
"""

# Kunlun (昆仑)

与具体业务无关的 Python 底层能力库。承载跨平台、跨业务的通用抽象与基础设施，不依赖上层应用包。<br />

<br />

## 定位

- **底层抽象** — 沉淀跨平台、跨业务的通用能力，保持稳定、零业务耦合
- **基础设施** — 为上层应用包提供可复用的底层支撑，上层按需在此之上扩展具体实现
- **最小依赖** — 不依赖任何上层应用包，可被任意项目自由引用

<br />

## 开始使用

### 安装

```bash
# 安装 pykunlun（使用清华镜像源加速）
pip install pykunlun -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

<br />

## 开发指南

### 本地安装

```bash
# 开发模式（修改源码立刻生效，无需重新安装）
python -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 基础安装
python -m pip install . -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

<br />

### 开发依赖

```bash
# 安装开发依赖（代码检查、测试等）
python -m pip install ".[dev]"
```

<br />

### 代码质量

```bash
# 代码风格检查
python -m ruff check src/

# 类型检查
python -m mypy src/

# 运行测试
python -m pytest tests/
```

<br />

### 打包上传

```bash
# 清理 __pycache__ 缓存
python -m baibao py_clean .

# 构建源码包和 wheel 包
python -m build

# 构建结果
ls dist/

# 检查元数据
python -m twine check dist/*

# 上传到正式 PyPI
python -m twine upload dist/*

```

<br />

## 许可证

GPL-3.0-or-later

<br />


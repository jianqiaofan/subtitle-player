"""Verify pywhispercpp installation and report inference backend."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def site_packages() -> Path:
    import site

    for entry in site.getsitepackages() + [site.getusersitepackages()]:
        root = Path(entry)
        if (root / "pywhispercpp").is_dir() or any(root.glob("ggml*.dll")):
            return root
    raise RuntimeError("无法定位 site-packages")


def main() -> int:
    pkg = site_packages()
    try:
        os.add_dll_directory(str(pkg))
    except (OSError, AttributeError):
        pass

    dlls = sorted(f.name for f in pkg.glob("ggml*.dll"))
    if not dlls:
        print("错误: 未找到 ggml 库，请运行 安装依赖.bat")
        return 1

    backend = "CUDA (GPU)" if any("cuda" in name for name in dlls) else "CPU"
    print(f"推理后端: {backend}")
    print("ggml 库:", ", ".join(dlls))

    spec = importlib.util.find_spec("_pywhispercpp")
    if spec is None or not spec.origin:
        print("错误: 未找到 _pywhispercpp 扩展")
        return 1

    import _pywhispercpp  # noqa: F401

    print("pywhispercpp 验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

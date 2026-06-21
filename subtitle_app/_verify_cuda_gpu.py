"""Verify CUDA pywhispercpp loads and can initialize GPU with the configured model."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"


def site_packages() -> Path:
    import site

    for entry in site.getsitepackages() + [site.getusersitepackages()]:
        root = Path(entry)
        if (root / "pywhispercpp").is_dir() or any(root.glob("ggml*.dll")):
            return root
    raise RuntimeError("无法定位 site-packages")


def setup_dll_paths(sp: Path) -> None:
    os.add_dll_directory(str(sp))
    for cuda_root in (
        Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin"),
        Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin"),
        Path(os.environ.get("CUDA_PATH", "")) / "bin",
    ):
        if cuda_root.is_dir():
            os.add_dll_directory(str(cuda_root))


def model_path_from_config() -> str:
    if CONFIG_PATH.is_file():
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        path = data.get("model_path", "").strip()
        if path and Path(path).is_file():
            return path
    default = APP_DIR.parent / "models" / "win系统模型中等.bin"
    if default.is_file():
        return str(default)
    raise RuntimeError("未找到可用的 Whisper 模型文件")


def main() -> int:
    sp = site_packages()
    setup_dll_paths(sp)

    dlls = sorted(f.name for f in sp.glob("ggml*.dll"))
    if not any("cuda" in name for name in dlls):
        print("错误: 未找到 ggml-cuda*.dll")
        return 1

    print("推理后端: CUDA (GPU)")
    print("ggml 库:", ", ".join(dlls))

    import _pywhispercpp  # noqa: F401
    from pywhispercpp.model import Model

    model_path = model_path_from_config()
    print(f"正在加载模型: {Path(model_path).name}")
    Model(model_path, n_threads=4, context_params={"flash_attn": False})
    print("GPU 模型加载验证通过")
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.path.insert(0, str(APP_DIR))
    raise SystemExit(main())

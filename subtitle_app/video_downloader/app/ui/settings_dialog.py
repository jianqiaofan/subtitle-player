from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, master, settings, on_save) -> None:
        super().__init__(master)
        self.settings = settings
        self.on_save = on_save
        self.title("设置")
        self.geometry("560x640")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.configure(fg_color="#12161c")
        pad = {"padx": 18, "pady": 8}

        ctk.CTkLabel(
            self, text="下载与解析设置", font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w", padx=18, pady=(16, 4))

        form = ctk.CTkScrollableFrame(self, fg_color="#1a212b", corner_radius=12)
        form.pack(fill="both", expand=True, padx=16, pady=8)

        self.vars: dict[str, ctk.StringVar | ctk.BooleanVar] = {}

        def add_entry(
            key: str,
            label: str,
            placeholder: str = "",
            *,
            readonly: bool = False,
        ) -> ctk.CTkEntry:
            ctk.CTkLabel(form, text=label, anchor="w").pack(fill="x", **pad)
            var = ctk.StringVar(value=str(self.settings.get(key) or ""))
            self.vars[key] = var
            entry = ctk.CTkEntry(
                form,
                textvariable=var,
                placeholder_text=placeholder,
                state="disabled" if readonly else "normal",
                text_color="#9aa7b5" if readonly else None,
                fg_color="#141a22" if readonly else None,
            )
            entry.pack(fill="x", padx=18, pady=(0, 6))
            return entry

        def add_switch(key: str, label: str) -> None:
            var = ctk.BooleanVar(value=bool(self.settings.get(key)))
            self.vars[key] = var
            ctk.CTkSwitch(form, text=label, variable=var).pack(anchor="w", **pad)

        # 保存目录 + 路径选择按钮
        ctk.CTkLabel(form, text="保存目录", anchor="w").pack(fill="x", **pad)
        save_row = ctk.CTkFrame(form, fg_color="transparent")
        save_row.pack(fill="x", padx=18, pady=(0, 6))
        save_var = ctk.StringVar(value=str(self.settings.get("save_dir") or ""))
        self.vars["save_dir"] = save_var
        ctk.CTkEntry(save_row, textvariable=save_var).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(
            save_row,
            text="选择…",
            width=80,
            fg_color="#2a3441",
            hover_color="#3a4656",
            command=self._browse_save_dir,
        ).pack(side="right")

        add_entry("filename_template", "文件名模板 (yt-dlp)", readonly=True)
        add_entry(
            "video_format",
            "格式选择表达式（推荐优先 H.264）",
            "bv*[vcodec^=avc1]+ba/bv*+ba/b",
            readonly=True,
        )
        add_entry("merge_output", "合并封装格式", "mp4 / mkv / webm", readonly=True)
        add_entry("max_concurrent_tasks", "同时下载任务数", "2")
        add_entry("concurrent_fragments", "分片并发数", "8")
        add_entry("proxy", "代理 (可选)", "http://127.0.0.1:7890")
        add_entry("limit_rate", "限速 (可选)", "例如 5M")
        add_entry(
            "cookies_from_browser",
            "从浏览器读取 Cookie",
            "edge / chrome / firefox（留空则不用）",
        )
        add_entry("cookies_file", "Cookie 文件路径 (Netscape 格式)")
        add_entry("ffmpeg_path", "FFmpeg 路径（留空自动检测）")

        add_switch("write_subs", "下载字幕")
        add_switch("write_thumbnail", "下载封面")
        add_switch("embed_metadata", "写入元数据")
        add_switch("watch_clipboard", "监视剪贴板中的链接")
        add_switch("auto_download_after_parse", "解析后自动开始下载")

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(fill="x", padx=16, pady=12)
        ctk.CTkButton(
            btns, text="取消", width=100, fg_color="#2a3441", hover_color="#3a4656",
            command=self.destroy,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(
            btns, text="保存", width=100, fg_color="#0f766e", hover_color="#0d9488",
            command=self._save,
        ).pack(side="right")

    def _browse_save_dir(self) -> None:
        current = str(self.vars["save_dir"].get() or "").strip()
        initial = current if current and Path(current).exists() else str(Path.home())
        chosen = filedialog.askdirectory(
            parent=self,
            title="选择保存目录",
            initialdir=initial,
        )
        if not chosen:
            return
        path = str(Path(chosen).resolve())
        self.vars["save_dir"].set(path)
        # 选中后立即持久化，关闭程序后下次仍为该目录
        self.settings.set("save_dir", path)
        self.settings.save()
        self.on_save()

    def _save(self) -> None:
        data = {}
        for key, var in self.vars.items():
            val = var.get()
            if key in {"max_concurrent_tasks", "concurrent_fragments"}:
                try:
                    val = int(str(val).strip() or "1")
                except ValueError:
                    val = self.settings.get(key)
            elif key == "save_dir":
                val = str(Path(str(val).strip()).resolve()) if str(val).strip() else val
            data[key] = val
        self.settings.update(data)
        self.settings.save()
        self.on_save()
        self.destroy()

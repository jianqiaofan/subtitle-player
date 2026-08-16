from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from app import __app_name__, __version__
from app.core.queue import TaskQueue
from app.core.settings import Settings
from app.core.task import DownloadTask, TaskStatus
from app.ui.settings_dialog import SettingsDialog

_subtitle_app_root = Path(__file__).resolve().parents[3]
if str(_subtitle_app_root) not in sys.path:
    sys.path.insert(0, str(_subtitle_app_root))
from core.console_window import (
    add_console_visibility_listener,
    console_button_label,
    mirror_log,
    toggle_console,
)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# 专业青绿 + 深石板色，避免常见 AI 紫/奶油风
COLORS = {
    "bg": "#0e1218",
    "panel": "#161c25",
    "panel2": "#1c2430",
    "border": "#2a3544",
    "text": "#e8eef5",
    "muted": "#8b9bb0",
    "accent": "#0f766e",
    "accent_hover": "#0d9488",
    "danger": "#b91c1c",
    "warn": "#b45309",
    "ok": "#15803d",
}


class MainWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.settings = Settings()
        geo = self.settings.get("window_geometry") or "1080x720"
        self.title(f"{__app_name__}  v{__version__}")
        self.geometry(geo)
        self.minsize(900, 600)
        self.configure(fg_color=COLORS["bg"])
        # CustomTkinter 会延迟写入自定义图标，稍后恢复为 Python/Tk 默认图标
        self.after(200, self._use_default_icon)
        self.after(600, self._use_default_icon)

        self.queue = TaskQueue(
            self.settings,
            on_task_update=self._on_task_update,
            on_log=self._on_log,
        )
        self._row_map: dict[str, str] = {}  # task_id -> tree iid (same)
        self._last_clipboard = ""
        self._build()
        self.after(800, self._clipboard_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── layout ──────────────────────────────────────────────
    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.pack(side="left", padx=20, pady=12)
        ctk.CTkLabel(
            brand,
            text="视频下载工具",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=20, weight="bold"),
            text_color=COLORS["text"],
        ).pack(side="left")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.pack(side="right", padx=16)
        self._console_btn = ctk.CTkButton(
            actions, text=console_button_label(), width=90, fg_color=COLORS["panel2"],
            hover_color=COLORS["border"], text_color=COLORS["text"],
            command=toggle_console,
        )
        self._console_btn.pack(side="left", padx=4)
        add_console_visibility_listener(self._on_console_visibility_changed)
        ctk.CTkButton(
            actions, text="打开目录", width=90, fg_color=COLORS["panel2"],
            hover_color=COLORS["border"], command=self._open_save_dir,
        ).pack(side="left", padx=4)
        ctk.CTkButton(
            actions, text="设置", width=70, fg_color=COLORS["panel2"],
            hover_color=COLORS["border"], command=self._open_settings,
        ).pack(side="left", padx=4)

        # URL bar
        bar = ctk.CTkFrame(self, fg_color=COLORS["bg"])
        bar.pack(fill="x", padx=16, pady=(14, 6))
        self.url_var = ctk.StringVar()
        self.url_entry = ctk.CTkEntry(
            bar,
            textvariable=self.url_var,
            placeholder_text="粘贴视频链接，然后点「解析单视频」或「解析合集」…",
            height=40,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["panel"],
            border_color=COLORS["border"],
        )
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.url_entry.bind("<Return>", lambda _e: self._parse_single())
        self._setup_url_entry_menu()
        ctk.CTkButton(
            bar, text="解析单视频", width=120, height=40,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._parse_single,
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            bar, text="解析合集", width=110, height=40,
            fg_color="#134e4a", hover_color=COLORS["accent"],
            command=self._parse_collection,
        ).pack(side="left")

        # toolbar for table
        tools = ctk.CTkFrame(self, fg_color="transparent")
        tools.pack(fill="x", padx=16, pady=(4, 4))
        for text, cmd in [
            ("开始下载选中", self._start_selected),
            ("取消选中", self._cancel_selected),
            ("删除选中", self._remove_selected),
            ("清除已结束", self._clear_finished),
        ]:
            ctk.CTkButton(
                tools, text=text, width=120 if text == "开始下载选中" else 100, height=30,
                fg_color=COLORS["panel2"], hover_color=COLORS["border"],
                command=cmd,
            ).pack(side="left", padx=(0, 6))

        # task table via tkinter Treeview inside CTk frame
        table_wrap = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=12)
        table_wrap.pack(fill="both", expand=True, padx=16, pady=6)

        import tkinter.ttk as ttk

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "App.Treeview",
            background=COLORS["panel"],
            fieldbackground=COLORS["panel"],
            foreground=COLORS["text"],
            rowheight=46,
            borderwidth=0,
            font=("Segoe UI", 18),
        )
        style.configure(
            "App.Treeview.Heading",
            background=COLORS["panel2"],
            foreground=COLORS["muted"],
            relief="flat",
            font=("Segoe UI Semibold", 17),
        )
        style.map(
            "App.Treeview",
            background=[("selected", "#134e4a")],
            foreground=[("selected", COLORS["text"])],
        )

        cols = ("check", "title", "status", "progress", "speed", "eta", "size")
        self.tree = ttk.Treeview(
            table_wrap,
            columns=cols,
            show="headings",
            style="App.Treeview",
            selectmode="extended",
        )
        headings = {
            "check": ("", 48),
            "title": ("标题", 420),
            "status": ("状态", 90),
            "progress": ("进度", 80),
            "speed": ("速度", 100),
            "eta": ("剩余", 80),
            "size": ("大小", 90),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            anchor = "w" if key == "title" else "center"
            self.tree.column(key, width=width, minwidth=width, stretch=(key == "title"), anchor=anchor)

        scroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        scroll.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")

        # log panel
        log_frame = ctk.CTkFrame(self, fg_color=COLORS["panel"], corner_radius=12, height=160)
        log_frame.pack(fill="x", padx=16, pady=(4, 12))
        ctk.CTkLabel(
            log_frame, text="运行日志", text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=12, pady=(8, 0))
        self.log_box = ctk.CTkTextbox(
            log_frame, height=120, fg_color=COLORS["panel2"],
            text_color="#c5d0dc", font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_box.pack(fill="x", padx=10, pady=8)
        self.log_box.configure(state="disabled")
        self._append_log(f"{__app_name__} v{__version__} 已启动")
        self._append_log("引擎: yt-dlp · 界面: CustomTkinter · 仅供学习与合法用途")
        self._append_log("单集用「解析单视频」，整集用「解析合集」")

    def _on_console_visibility_changed(self, visible: bool) -> None:
        def apply() -> None:
            if not hasattr(self, "_console_btn"):
                return
            self._console_btn.configure(text="隐藏后台" if visible else "查看后台")

        try:
            self.after(0, apply)
        except Exception:  # noqa: BLE001
            pass

    def _use_default_icon(self) -> None:
        """去掉 CustomTkinter 自带图标，使用 Python/Tk 默认图标。"""
        try:
            self.iconbitmap(default="")
        except Exception:  # noqa: BLE001
            try:
                self.wm_iconbitmap("")
            except Exception:  # noqa: BLE001
                pass

    def _setup_url_entry_menu(self) -> None:
        """输入框右键菜单：复制 / 粘贴 / 剪切 / 全选。"""
        import tkinter as tk

        menu = tk.Menu(
            self,
            tearoff=0,
            bg=COLORS["panel2"],
            fg=COLORS["text"],
            activebackground=COLORS["accent"],
            activeforeground=COLORS["text"],
            font=("Segoe UI", 18),
        )
        menu.add_command(label="复制", command=self._url_copy)
        menu.add_command(label="粘贴", command=self._url_paste)
        menu.add_command(label="剪切", command=self._url_cut)
        menu.add_separator()
        menu.add_command(label="全选", command=self._url_select_all)
        self._url_menu = menu

        def popup(event) -> None:
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

        # CTkEntry 实际输入控件在内部 _entry
        targets = [self.url_entry]
        inner = getattr(self.url_entry, "_entry", None)
        if inner is not None:
            targets.append(inner)
        for w in targets:
            w.bind("<Button-3>", popup)
            # 部分键鼠会触发 Button-2
            w.bind("<Button-2>", popup)

    def _url_inner(self):
        return getattr(self.url_entry, "_entry", self.url_entry)

    def _url_copy(self) -> None:
        entry = self._url_inner()
        try:
            text = entry.selection_get()
        except Exception:  # noqa: BLE001
            text = self.url_var.get()
        if not text:
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()

    def _url_paste(self) -> None:
        try:
            text = self.clipboard_get()
        except Exception:  # noqa: BLE001
            return
        entry = self._url_inner()
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:  # noqa: BLE001
            pass
        entry.insert("insert", text)

    def _url_cut(self) -> None:
        entry = self._url_inner()
        try:
            text = entry.selection_get()
        except Exception:  # noqa: BLE001
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:  # noqa: BLE001
            pass

    def _url_select_all(self) -> None:
        entry = self._url_inner()
        entry.select_range(0, "end")
        entry.icursor("end")

    # ── actions ─────────────────────────────────────────────
    def _parse_single(self) -> None:
        """解析单个视频并加入任务列表（相当于原「添加任务」）。"""
        self._add_urls(want_collection=False)

    def _parse_collection(self) -> None:
        """解析合集并展开为多个任务（相当于原勾选「下载整个合集」）。"""
        self._add_urls(want_collection=True)

    def _add_urls(self, *, want_collection: bool) -> None:
        raw = self.url_var.get().strip()
        if not raw:
            messagebox.showinfo("提示", "请先粘贴视频链接")
            return
        urls = URL_RE.findall(raw)
        if not urls:
            urls = [raw]
        auto = bool(self.settings.get("auto_download_after_parse"))
        for u in urls:
            task = self.queue.add_url(u, auto_start=False)
            self._append_log(f"已添加: {u}")
            if want_collection:
                self._schedule_collection_expand(task.id, auto_start=auto)
            elif auto:
                self._schedule_auto_start(task.id)
        self.url_var.set("")

    def _schedule_auto_start(self, task_id: str, tries: int = 0) -> None:
        task = next((t for t in self.queue.list_tasks() if t.id == task_id), None)
        if not task:
            return
        if task.status == TaskStatus.QUEUED:
            self.queue.enqueue(task_id)
            return
        if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return
        if tries < 600:
            self.after(500, lambda: self._schedule_auto_start(task_id, tries + 1))

    def _schedule_collection_expand(self, task_id: str, auto_start: bool, tries: int = 0) -> None:
        task = next((t for t in self.queue.list_tasks() if t.id == task_id), None)
        if not task:
            return
        if task.status == TaskStatus.QUEUED:
            if task.collection_count:
                self._do_expand(task_id, auto_start=auto_start)
            else:
                title = task.title or task.url
                messagebox.showinfo(
                    "无合集",
                    f"当前链接是单个视频，未检测到合集。\n\n"
                    f"「{title}」\n\n"
                    "将按「解析单视频」处理，已加入任务列表。",
                )
                self._append_log(f"[{task_id}] 无合集，已按单视频处理: {title}")
                if auto_start:
                    self.queue.enqueue(task_id)
            return
        if task.status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return
        if tries < 600:
            self.after(
                500,
                lambda: self._schedule_collection_expand(task_id, auto_start, tries + 1),
            )

    def _do_expand(self, task_id: str, auto_start: bool = True) -> None:
        if self.tree.exists(task_id):
            self.tree.delete(task_id)
        ids = self.queue.expand_collection(
            task_id, auto_start=auto_start, remove_seed=True
        )
        self._append_log(f"已展开合集，新增 {len(ids)} 个任务")

    def _selected_ids(self) -> list[str]:
        return list(self.tree.selection())

    def _start_selected(self) -> None:
        for tid in self._selected_ids():
            self.queue.enqueue(tid)

    def _cancel_selected(self) -> None:
        for tid in self._selected_ids():
            self.queue.cancel(tid)

    def _remove_selected(self) -> None:
        for tid in self._selected_ids():
            self.queue.remove(tid)
            if self.tree.exists(tid):
                self.tree.delete(tid)

    def _clear_finished(self) -> None:
        finished = {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
        }
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            if len(vals) >= 3 and vals[2] in finished:
                self.tree.delete(item)
        self.queue.clear_finished()

    def _open_settings(self) -> None:
        SettingsDialog(self, self.settings, on_save=lambda: None)

    def _open_save_dir(self) -> None:
        path = Path(self.settings.get("save_dir"))
        path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            webbrowser.open(path.as_uri())

    def _on_double_click(self, _event) -> None:
        sel = self._selected_ids()
        if not sel:
            return
        task = next((t for t in self.queue.list_tasks() if t.id == sel[0]), None)
        if task and task.filepath and Path(task.filepath).exists():
            folder = str(Path(task.filepath).parent)
            if sys.platform == "win32":
                subprocess.run(["explorer", "/select,", task.filepath], check=False)
            else:
                webbrowser.open(Path(folder).as_uri())

    # ── callbacks from worker threads → UI thread ───────────
    def _on_task_update(self, task: DownloadTask) -> None:
        self.after(0, lambda t=task: self._upsert_row(t))

    def _on_log(self, msg: str) -> None:
        self.after(0, lambda m=msg: self._append_log(m))

    def _upsert_row(self, task: DownloadTask) -> None:
        checked = task.id in self.tree.selection()
        values = task.to_row(checked=checked)
        if self.tree.exists(task.id):
            self.tree.item(task.id, values=values)
        else:
            self.tree.insert("", "end", iid=task.id, values=values)

    def _refresh_checkmarks(self) -> None:
        selected = set(self.tree.selection())
        for iid in self.tree.get_children():
            vals = list(self.tree.item(iid, "values"))
            if not vals:
                continue
            mark = "☑" if iid in selected else "☐"
            if vals[0] != mark:
                vals[0] = mark
                self.tree.item(iid, values=vals)

    def _on_tree_select(self, _event=None) -> None:
        self._refresh_checkmarks()

    def _on_tree_click(self, event) -> str | None:
        """点击复选框列时切换该行选中状态。"""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return None
        col = self.tree.identify_column(event.x)
        # #1 = 第一列 check
        if col != "#1":
            return None
        row = self.tree.identify_row(event.y)
        if not row:
            return None
        if row in self.tree.selection():
            self.tree.selection_remove(row)
        else:
            self.tree.selection_add(row)
        self._refresh_checkmarks()
        return "break"

    def _append_log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{line}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        mirror_log(line)

    def _clipboard_tick(self) -> None:
        try:
            if self.settings.get("watch_clipboard"):
                import pyperclip

                text = (pyperclip.paste() or "").strip()
                if text and text != self._last_clipboard and URL_RE.search(text):
                    self._last_clipboard = text
                    # 不自动下载，只填入输入框并提示
                    if not self.url_var.get().strip():
                        self.url_var.set(text)
                        self._append_log("剪贴板检测到链接，已填入输入框")
                elif text:
                    self._last_clipboard = text
        except Exception:  # noqa: BLE001
            pass
        self.after(1200, self._clipboard_tick)

    def _on_close(self) -> None:
        self.settings.set("window_geometry", self.geometry())
        self.settings.save()
        self.destroy()


def choose_save_dir_dialog(initial: str) -> str:
    return filedialog.askdirectory(initialdir=initial) or initial

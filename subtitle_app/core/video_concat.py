"""把多个视频按顺序接成一个文件。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from core.audio import find_ffmpeg, find_ffprobe, get_media_duration


class ConcatCancelled(Exception):
    """用户取消了拼接。"""


@dataclass(frozen=True)
class _Clip:
    path: Path
    vcodec: str
    width: int
    height: int
    pix_fmt: str
    fps: float
    acodec: str
    sample_rate: str
    channels: int
    duration: float
    video_duration: float
    audio_duration: float

    def copy_key(self) -> tuple:
        fps = round(self.fps, 2) if self.fps else 0
        return (
            self.vcodec,
            self.width,
            self.height,
            self.pix_fmt,
            fps,
            self.acodec,
            self.sample_rate,
            self.channels,
        )


def default_output_path(first: Path) -> Path:
    """第 1 个视频所在目录，文件名为原名加上「拼接」。"""
    suffix = first.suffix if first.suffix else ".mp4"
    return first.with_name(f"{first.stem}拼接{suffix}")


def concat_videos(
    sources: list[Path],
    output: Path,
    *,
    cancel_event: threading.Event | None = None,
) -> None:
    files = [Path(item) for item in sources]
    if len(files) < 2:
        raise ValueError("请至少选择 2 个视频。")
    output = Path(output)
    resolved_out = output.resolve()
    for src in files:
        if not src.is_file():
            raise FileNotFoundError(f"找不到视频：{src}")
        if src.resolve() == resolved_out:
            raise ValueError("输出文件不能与要拼接的视频是同一个文件。")

    clips = [_probe(src) for src in files]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.part{output.suffix or '.mp4'}")
    if partial.exists():
        partial.unlink()
    try:
        if _can_stream_copy(clips):
            try:
                _concat_copy(files, partial, cancel_event)
            except ConcatCancelled:
                raise
            except Exception:
                if partial.exists():
                    partial.unlink()
                _concat_reencode(clips, partial, cancel_event)
        else:
            _concat_reencode(clips, partial, cancel_event)
        _raise_if_cancelled(cancel_event)
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise RuntimeError("拼接没有生成视频文件。")
        _assert_full_length(partial, clips)
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _same_layout(clips: list[_Clip]) -> bool:
    first = clips[0].copy_key()
    return all(clip.copy_key() == first for clip in clips[1:])


def _can_stream_copy(clips: list[_Clip]) -> bool:
    """画面比声音短时，直接复制会在第一段画面结束处停掉，后面的文件都接不上。"""
    if not _same_layout(clips):
        return False
    for clip in clips:
        if clip.audio_duration <= 0:
            continue
        if abs(clip.video_duration - clip.audio_duration) > 0.75:
            return False
    return True


def _target_duration(clip: _Clip) -> float:
    return max(clip.duration, clip.video_duration, clip.audio_duration)


def _assert_full_length(path: Path, clips: list[_Clip]) -> None:
    expected = sum(_target_duration(clip) for clip in clips)
    actual = get_media_duration(path)
    if expected > 1 and actual + 1.5 < expected * 0.9:
        raise RuntimeError(
            f"拼接结果只有 {actual:.0f} 秒，各段加起来应约 {expected:.0f} 秒。"
            "结果未保存。"
        )


def _probe(path: Path) -> _Clip:
    cmd = [
        find_ffprobe(),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    result = _run(cmd, cancel_event=None)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"无法读取视频信息：{path.name}") from exc

    video = next(
        (
            stream
            for stream in payload.get("streams") or []
            if stream.get("codec_type") == "video"
            and not (stream.get("disposition") or {}).get("attached_pic")
        ),
        None,
    )
    if not video:
        raise RuntimeError(f"这个文件没有视频画面：{path.name}")
    audio = next(
        (stream for stream in payload.get("streams") or [] if stream.get("codec_type") == "audio"),
        None,
    )
    duration = _as_float((payload.get("format") or {}).get("duration"))
    if duration <= 0:
        duration = _as_float(video.get("duration"))
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"无法读取画面尺寸：{path.name}")
    return _Clip(
        path=path,
        vcodec=str(video.get("codec_name") or ""),
        width=width,
        height=height,
        pix_fmt=str(video.get("pix_fmt") or ""),
        fps=_parse_rate(str(video.get("avg_frame_rate") or ""))
        or _parse_rate(str(video.get("r_frame_rate") or "")),
        acodec=str(audio.get("codec_name") or "") if audio else "",
        sample_rate=str(audio.get("sample_rate") or "") if audio else "",
        channels=int(audio.get("channels") or 0) if audio else 0,
        duration=duration,
        video_duration=_as_float(video.get("duration")) or duration,
        audio_duration=_as_float(audio.get("duration")) if audio else 0.0,
    )


def _concat_copy(files: list[Path], output: Path, cancel_event: threading.Event | None) -> None:
    lines = ["ffconcat version 1.0"]
    for path in files:
        lines.append("file " + _quote_concat_path(path))
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".ffconcat",
        delete=False,
    ) as handle:
        handle.write("\n".join(lines) + "\n")
        list_path = Path(handle.name)
    try:
        cmd = [
            find_ffmpeg(),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-fflags",
            "+genpts",
            *_faststart_args(output),
            str(output),
        ]
        _run(cmd, cancel_event)
    finally:
        list_path.unlink(missing_ok=True)


def _concat_reencode(clips: list[_Clip], output: Path, cancel_event: threading.Event | None) -> None:
    # 画布用所有视频里最大的宽和最大的高，小画面不拉伸，四周用黑色补上。
    width = _even_up(max(clip.width for clip in clips))
    height = _even_up(max(clip.height for clip in clips))
    fps = clips[0].fps if clips[0].fps >= 1 else 30.0
    any_audio = any(clip.acodec or clip.audio_duration for clip in clips)
    parts: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        video_chain = (
            f"[{index}:v:0]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps:.3f}"
        )
        video_pad = max(0.0, _target_duration(clip) - clip.video_duration)
        if video_pad > 0.05:
            # 这一段自己的画面比整段时长短时，补到原来的时长再接下一段，不把这段掐掉。
            video_chain += f",tpad=stop_mode=clone:stop_duration={video_pad:.3f}"
        parts.append(video_chain + f"[v{index}]")
        labels.append(f"[v{index}]")
        if any_audio:
            audio_pad = max(0.0, _target_duration(clip) - clip.audio_duration) if clip.acodec else 0.0
            if clip.acodec:
                audio_chain = (
                    f"[{index}:a:0]aformat=sample_fmts=fltp:sample_rates=48000:"
                    "channel_layouts=stereo,asetpts=PTS-STARTPTS"
                )
                if audio_pad > 0.05:
                    audio_chain += f",apad=pad_dur={audio_pad:.3f}"
                parts.append(audio_chain + f"[a{index}]")
            else:
                length = _target_duration(clip) if _target_duration(clip) > 0 else 0.1
                parts.append(
                    "anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={length:.3f},asetpts=PTS-STARTPTS[a{index}]"
                )
            labels.append(f"[a{index}]")
    if any_audio:
        parts.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=1[v][a]")
    else:
        parts.append(f"{''.join(labels)}concat=n={len(clips)}:v=1:a=0[v]")
    script = ";".join(parts)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".txt",
        delete=False,
    ) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    try:
        cmd = [find_ffmpeg(), "-y"]
        for clip in clips:
            cmd.extend(["-i", str(clip.path)])
        cmd.extend(["-/filter_complex", str(script_path), "-map", "[v]"])
        if any_audio:
            cmd.extend(["-map", "[a]", "-c:a", "aac", "-b:a", "160k"])
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                *_faststart_args(output),
                str(output),
            ]
        )
        _run(cmd, cancel_event)
    finally:
        script_path.unlink(missing_ok=True)


def _run(cmd: list[str], cancel_event: threading.Event | None) -> subprocess.CompletedProcess[str]:
    _raise_if_cancelled(cancel_event)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **kwargs)
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []

    def _drain(pipe, parts: list[bytes]) -> None:
        try:
            while True:
                block = pipe.read(65536)
                if not block:
                    break
                parts.append(block)
        finally:
            pipe.close()

    readers = [
        threading.Thread(target=_drain, args=(proc.stdout, stdout_parts), daemon=True),
        threading.Thread(target=_drain, args=(proc.stderr, stderr_parts), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                break
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
        proc.wait(timeout=10)
    finally:
        for reader in readers:
            reader.join(timeout=5)
    _raise_if_cancelled(cancel_event)
    stdout = b"".join(stdout_parts).decode("utf-8", errors="replace")
    stderr = b"".join(stderr_parts).decode("utf-8", errors="replace")
    if proc.returncode != 0:
        detail = (stderr or stdout).strip()
        tail = detail[-800:] if detail else f"退出码 {proc.returncode}"
        raise RuntimeError(f"拼接失败：\n{tail}")
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ConcatCancelled("已取消")


def _faststart_args(output: Path) -> list[str]:
    if output.suffix.lower() in {".mp4", ".mov", ".m4v"}:
        return ["-movflags", "+faststart"]
    return []


def _quote_concat_path(path: Path) -> str:
    text = path.resolve().as_posix().replace("'", r"'\''")
    return f"'{text}'"


def _parse_rate(value: str) -> float:
    text = (value or "").strip()
    if not text or text == "0/0":
        return 0.0
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _even_up(value: int) -> int:
    size = max(2, int(value))
    return size if size % 2 == 0 else size + 1

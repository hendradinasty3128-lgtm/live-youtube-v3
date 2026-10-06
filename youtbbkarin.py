import sys
import subprocess
import threading
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path

# Install streamlit jika belum ada
try:
    import streamlit as st
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "streamlit"])
    import streamlit as st

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

FFMPEG_PROCESS = None
PROCESS_LOCK = threading.Lock()


def safe_filename(name: str) -> str:
    name = Path(name).name
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- ()"
    cleaned = "".join(c if c in allowed else "_" for c in name).strip()
    return cleaned or "video.mp4"


def save_uploaded_file(uploaded_file, slot: int) -> str:
    original = safe_filename(uploaded_file.name)
    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    filename = f"video_{slot}_{stem}{suffix}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(path)


def download_video_from_url(url: str, slot: int, filename_hint: str = "") -> str:
    url = url.strip()
    if not url:
        raise ValueError("Link video kosong.")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Link harus diawali http:// atau https://")

    if "drive.google.com" in parsed.netloc or "docs.google.com" in parsed.netloc:
        try:
            import gdown
        except ImportError as exc:
            raise RuntimeError("Library gdown belum terpasang.") from exc
        hint = safe_filename(filename_hint or f"video_{slot}.mp4")
        if not Path(hint).suffix:
            hint += ".mp4"
        target = UPLOAD_DIR / f"video_{slot}_drive_{hint}"
        result = gdown.download(url=url, output=str(target), quiet=True, fuzzy=True)
        if not result or not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("Google Drive gagal diunduh. Pastikan file disetel 'Anyone with the link'.")
        return str(target)

    hint = filename_hint.strip()
    if not hint:
        name = Path(urllib.parse.unquote(parsed.path)).name
        hint = name or f"video_{slot}.mp4"
    hint = safe_filename(hint)
    if not Path(hint).suffix:
        hint += ".mp4"
    target = UPLOAD_DIR / f"video_{slot}_link_{hint}"

    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("Link tidak menghasilkan file video.")
    return str(target)


def save_uploaded_audio(uploaded_file, slot: int) -> str:
    original = safe_filename(uploaded_file.name)
    stem = Path(original).stem
    suffix = Path(original).suffix.lower()
    filename = f"audio_{slot}_{stem}{suffix}"
    path = UPLOAD_DIR / filename
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return str(path)


def make_concat_playlist(video_paths, repeat_count=1):
    playlist = UPLOAD_DIR / "playlist.txt"
    repeat_count = max(1, int(repeat_count))
    with open(playlist, "w", encoding="utf-8") as f:
        for _ in range(repeat_count):
            for path in video_paths:
                p = Path(path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{p}'\n")
    return str(playlist)


def make_audio_playlist(audio_paths, repeat_count=1):
    playlist = UPLOAD_DIR / "audio_playlist.txt"
    repeat_count = max(1, int(repeat_count))
    with open(playlist, "w", encoding="utf-8") as f:
        for _ in range(repeat_count):
            for path in audio_paths:
                p = Path(path).resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{p}'\n")
    return str(playlist)


def run_ffmpeg(mode, video_paths, audio_paths, stream_key, is_shorts, playback_mode, repeat_count, duration_hours, video_quality, log_callback):
    global FFMPEG_PROCESS

    output_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    duration_seconds = int(duration_hours * 3600) if playback_mode == "Jadwal Durasi (Jam)" and duration_hours else None

    if is_shorts:
        scale_filter = "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2"
    else:
        if video_quality == "HD 720p (Ringan & Stabil)":
            scale_filter = "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2"
        else:
            scale_filter = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"

    if mode == "Video + MP3 Playlist":
        if not video_paths or not audio_paths:
            log_callback("ERROR: Butuh video latar dan file MP3.")
            return

        if playback_mode == "Jumlah Pengulangan":
            audio_playlist = make_audio_playlist(audio_paths, repeat_count)
            audio_loop_args = []
        else:
            audio_playlist = make_audio_playlist(audio_paths, 1)
            audio_loop_args = ["-stream_loop", "-1"]

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-thread_queue_size", "512", "-re", "-stream_loop", "-1",
            "-i", video_paths[0], "-thread_queue_size", "512", "-re",
        ]
        cmd += audio_loop_args
        cmd += [
            "-f", "concat", "-safe", "0", "-i", audio_playlist,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-r", "20", "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-threads", "2", "-b:v", "2500k", "-maxrate", "2500k",
            "-bufsize", "3600k", "-g", "50", "-keyint_min", "50",
            "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k",
            "-ar", "48000", "-ac", "2", "-af", "aresample=async=1:first_pts=0",
            "-fps_mode", "cfr", "-max_interleave_delta", "0",
            "-avoid_negative_ts", "make_zero", "-vf", scale_filter,
        ]

        if duration_seconds:
            cmd += ["-t", str(duration_seconds)]
        elif playback_mode == "Jumlah Pengulangan":
            cmd += ["-shortest"]

        cmd += ["-flvflags", "no_duration_filesize", "-muxdelay", "0", "-muxpreload", "0", "-f", "flv", output_url]
        log_callback("🚀 Memulai Streaming Mode: Video + MP3")
    else:
        if not video_paths:
            log_callback("ERROR: Minimal 1 video diperlukan.")
            return

        playlist_repeat = repeat_count if playback_mode == "Jumlah Pengulangan" else 1
        playlist = make_concat_playlist(video_paths, playlist_repeat)
        cmd = ["ffmpeg", "-hide_banner", "-re"]

        if playback_mode == "Tanpa Batas (Looping 24 Jam)":
            cmd += ["-stream_loop", "-1"]

        cmd += [
            "-f", "concat", "-safe", "0", "-i", playlist,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-r", "20", "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-threads", "2", "-b:v", "2500k", "-maxrate", "2500k",
            "-bufsize", "3600k", "-g", "50", "-keyint_min", "50",
            "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k",
            "-ar", "48000", "-af", "aresample=async=1:first_pts=0",
            "-fps_mode", "cfr", "-vf", scale_filter,
        ]

        if duration_seconds:
            cmd += ["-t", str(duration_seconds)]

        cmd += ["-f", "flv", output_url]
        log_callback("🚀 Memulai Streaming Mode: Playlist 5 Video")

    try:
        with PROCESS_LOCK:
            FFMPEG_PROCESS = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
            )
        process = FFMPEG_PROCESS
        for line in process.stdout:
            line = line.strip()
            if line:
                log_callback(line)
        process.wait()
    except Exception as e:
        log_callback(f"Error: {e}")
    finally:
        with PROCESS_LOCK:
            FFMPEG_PROCESS = None


def stop_ffmpeg():
    global FFMPEG_PROCESS
    with PROCESS_LOCK:
        if FFMPEG_PROCESS and FFMPEG_PROCESS.poll() is None:
            try:
                FFMPEG_PROCESS.terminate()
                FFMPEG_PROCESS.wait(timeout=5)
            except Exception:
                pass
        FFMPEG_PROCESS = None


def main():

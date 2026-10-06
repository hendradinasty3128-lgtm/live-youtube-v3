import sys
import subprocess
import threading
import os
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime
import pytz

# Install streamlit jika belum ada
try:
    import streamlit as st
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "streamlit"])
    import streamlit as st

# Install pytz jika belum ada untuk zona waktu Jakarta
try:
    import pytz
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pytz"])
    import pytz

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


def run_ffmpeg(mode, video_paths, audio_paths, stream_key, is_shorts, playback_mode, repeat_count, total_duration_seconds, start_delay_seconds, log_callback):
    global FFMPEG_PROCESS

    if start_delay_seconds > 0:
        log_callback(f"⏳ Menunda siaran sesuai jadwal selama {start_delay_seconds} detik...")
        time.sleep(start_delay_seconds)

    output_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"
    duration_seconds = total_duration_seconds if total_duration_seconds and total_duration_seconds > 0 else None

    if is_shorts:
        scale_filter = (
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2"
        )
    else:
        scale_filter = (
            "scale=1280:720:force_original_aspect_ratio=decrease,"
            "pad=1280:720:(ow-iw)/2:(oh-ih)/2"
        )

    if mode == "Video + MP3 Playlist":
        if not video_paths or not audio_paths:
            log_callback("ERROR: Butuh video latar dan file MP3.")
            return

        audio_playlist = make_audio_playlist(audio_paths, 1)
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "info",
            "-thread_queue_size", "512", "-re", "-stream_loop", "-1",
            "-i", video_paths[0], "-thread_queue_size", "512", "-re",
            "-stream_loop", "-1",
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

        cmd += ["-flvflags", "no_duration_filesize", "-muxdelay", "0", "-muxpreload", "0", "-f", "flv", output_url]
        log_callback("🚀 Memulai Streaming Mode: Video + MP3 (Terjadwal)")
    else:
        if not video_paths:
            log_callback("ERROR: Minimal 1 video diperlukan.")
            return

        playlist = make_concat_playlist(video_paths, 9999)
        cmd = ["ffmpeg", "-hide_banner", "-re", "-stream_loop", "-1", "-f", "concat", "-safe", "0", "-i", playlist]

        cmd += [
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
        log_callback("🚀 Memulai Streaming Mode: Playlist Video (Terjadwal)")

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
    st.set_page_config(page_title="Hendra Waskita - YouTube Live Streaming", page_icon="🎬", layout="wide")

    # Custom CSS & Javascript Live Clock untuk Sidebar
    st.markdown("""
        <style>
        [data-testid="stSidebar"] {
            background-color: #5c419c;
            color: white;
        }
        [data-testid="stSidebar"] * {
            color: white !important;
        }
        .profile-container {
            text-align: center;
            padding-bottom: 15px;
            border-bottom: 1px solid rgba(255,255,255,0.2);
            margin-bottom: 15px;
        }
        .clock-box {
            background-color: rgba(255, 255, 255, 0.15);
            border-radius: 12px;
            padding: 10px;
            text-align: center;
            margin-top: 10px;
            margin-bottom: 15px;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        </style>

        <script>
        function updateClock() {
            const now = new Date();
            const optionsTime = { timeZone: 'Asia/Jakarta', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
            const optionsDate = { timeZone: 'Asia/Jakarta', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' };
            
            let timeString = new Intl.DateTimeFormat('id-ID', optionsTime).format(now).replace(/:/g, '.');
            let dateString = new Intl.DateTimeFormat('id-ID', optionsDate).format(now);
            
            const clockEl = document.getElementById('live-clock');
            const dateEl = document.getElementById('live-date');
            
            if (clockEl) clockEl.innerText = timeString + ' WIB';
            if (dateEl) dateEl.innerText = dateString;
        }
        setInterval(updateClock, 1000);
        window.onload = updateClock;
        </script>
    """, unsafe_allow_html=True)

    days_indo = {"Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu", "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"}
    months_indo = {1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember"}
    
    tz_jakarta = pytz.timezone("Asia/Jakarta")
    now_jakarta = datetime.now(tz_jakarta)
    
    day_name = days_indo.get(now_jakarta.strftime("%A"), now_jakarta.strftime("%A"))
    formatted_date = f"{day_name}, {now_jakarta.day} {months_indo.get(now_jakarta.month, '')} {now_jakarta.year}"
    formatted_time = now_jakarta.strftime("%H.%M.%S")

    with st.sidebar:
        st.markdown('<div class="profile-container">', unsafe_allow_html=True)
        try:
            st.image("DINASTY.jpg.webp", width=80)
        except Exception:
            st.image("https://images.unsplash.com/photo-1534528741775-53994a69daeb?w=150&h=150&fit=crop&crop=faces", width=80)
        st.markdown("### YouTube Live Streaming")
        st.markdown("<p style='font-size: 14px; font-weight: bold; margin-bottom: 2px;'>By Hendra Waskita</p>", unsafe_allow_html=True)
        
        st.markdown(f"<p id='live-date' style='font-size: 13px; opacity: 0.9; margin-bottom: 0;'>{formatted_date}</p>", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(f"""
            <div class="clock-box">
                <span id="live-clock" style="font-size: 22px; font-weight: bold; letter-spacing: 1px;">{formatted_time} WIB</span>
            </div>
        """, unsafe_allow_html=True)

        menu = st.radio("Navigasi Menu", ["📊 Dashboard Utama", "🚀 Buat & Atur Live", "📁 Berkas Video", "⚙️ Pengaturan & Cache"])

    if menu == "📊 Dashboard Utama":
        st.title("📊 Panel Dashboard Live Streaming")
        st.markdown("Halo, **Hendra Waskita!** Selamat datang kembali di panel kontrol streaming Anda.")
        
        col1, col2 = st.columns(2)
        streaming = FFMPEG_PROCESS is not None and FFMPEG_PROCESS.poll() is None
        
        with col1:
            st.metric(label="LIVE AKTIF", value="1" if streaming else "0")
        with col2:
            st.metric(label="STATUS SERVER", value="ONLINE (FFmpeg Ready)")

        st.markdown("---")
        st.subheader("🔴 Status Siaran Langsung Saat Ini")
        
        if streaming:
            st.success("Status: AKTIF 🟢 (Sedang mengudara ke YouTube)")
        else:
            st.warning("Status: TIDAK AKTIF (Belum ada siaran yang berjalan)")

        if st.session_state.get("logs"):
            st.text_area("Live Terminal Logs:", "\n".join(st.session_state["logs"][-15:]), height=200)

    elif menu == "🚀 Buat & Atur Live":
        st.title("🚀 Buat & Kelola Siaran Live (Dengan Penjadwalan)")
        st.markdown("---")

        mode = st.radio("Pilih Format Konten", ["Playlist Video (Multi-File)", "Video + MP3 Playlist"], horizontal=True)

        selected_paths = []
        audio_paths = []

        # Ambil daftar semua video yang ada di folder uploads
        existing_videos = sorted([f for f in UPLOAD_DIR.glob("*") if f.suffix.lower() in ['.mp4', '.mkv', '.mov', '.webm']])
        video_options = [f.name for f in existing_videos]

        if mode == "Playlist Video (Multi-File)":
            st.subheader("📁 Pilih File Video dari Berkas Tersimpan")
            if video_options:
                chosen_videos = st.multiselect(
                    "Pilih video untuk playlist siaran (urutkan sesuai keinginan):",
                    options=video_options,
                    default=video_options[:min(3, len(video_options))]
                )
                for name in chosen_videos:
                    selected_paths.append(str(UPLOAD_DIR / name))
            else:
                st.warning("Belum ada file video di folder Berkas. Silakan upload terlebih dahulu melalui menu **📁 Berkas Video** atau upload di bawah.")
                up_direct = st.file_uploader("Upload Video Cepat", type=["mp4", "mkv", "mov", "webm"], accept_multiple_files=True)
                if up_direct:
                    for f in up_direct:
                        saved = save_uploaded_file(f, 1)
                        selected_paths.append(saved)
                    st.rerun()
        else:
            st.subheader("🎵 Manajemen Background Video & MP3")
            if video_options:
                chosen_bg = st.selectbox("Pilih Video Latar Utama:", options=video_options)
                if chosen_bg:
                    selected_paths = [str(UPLOAD_DIR / chosen_bg)]
            else:
                st.warning("Belum ada video latar. Silakan upload video terlebih dahulu.")
                up_bg = st.file_uploader("Upload Video Latar", type=["mp4", "mkv", "mov"])
                if up_bg:
                    selected_paths = [save_uploaded_file(up_bg, 1)]
                    st.rerun()

            st.markdown("---")
            st.subheader("Pilih File MP3 Audio")
            existing_audios = sorted([f for f in UPLOAD_DIR.glob("*") if f.suffix.lower() == '.mp3'])
            audio_options = [f.name for f in existing_audios]
            if audio_options:
                chosen_audios = st.multiselect("Pilih file MP3 untuk playlist audio:", options=audio_options, default=audio_options)
                for name in chosen_audios:
                    audio_paths.append(str(UPLOAD_DIR / name))
            else:
                up_a = st.file_uploader("Upload MP3", type=["mp3"], accept_multiple_files=True, key="multi_mp3")
                if up_a:
                    for f in up_a:
                        audio_paths.append(save_uploaded_audio(f, 1))
                    st.rerun()

        st.markdown("---")
        st.subheader("⚙️ Konfigurasi Siaran & Penjadwalan")
        
        col_k1, col_k2 = st.columns(2)
        with col_k1:
            stream_key = st.text_input("Stream Key YouTube", type="password")
            video_quality = st.selectbox("Kualitas Resolusi", ["HD 720p (Ringan & Stabil)", "Full HD 1080p"])
        with col_k2:
            is_shorts = st.checkbox("Format YouTube Shorts (Vertikal)")

        st.markdown("---")
        st.markdown("#### 🕒 Pengaturan Jadwal & Waktu (WIB)")
        
        col_j1, col_j2 = st.columns(2)
        with col_j1:
            sched_date = st.date_input("Tanggal Mulai", value=now_jakarta.date())
        with col_j2:
            sched_time = st.time_input("Jam Mulai", value=now_jakarta.time())
        st.caption("⏰ Waktu akan disimpan dalam timezone: **Jakarta (Asia/Jakarta)**")

        st.markdown("#### ⏹️ Auto Stop & Durasi Otomatis")
        col_as1, col_as2 = st.columns(2)
        with col_as1:
            stop_hours = st.selectbox("Auto Stop: Pilih Jam", [0, 1, 2, 3, 4, 6, 8, 12, 24], index=0)
        with col_as2:
            stop_minutes = st.selectbox("Auto Stop: Pilih Menit", [0, 15, 30, 45], index=0)
        st.caption("Atur durasi auto-stop (Jam dan Menit). Biarkan 0 jika ingin siaran berjalan terus tanpa henti.")

        col_js1, col_js2 = st.columns(2)
        with col_js1:
            stop_date = st.date_input("Tanggal Stop (Opsi)", value=now_jakarta.date())
        with col_js2:
            stop_time = st.time_input("Jam Stop (Opsi)", value=now_jakarta.time())

        repeat_schedule = st.selectbox("Pengulangan Jadwal:", ["Jadwal Manual", "Harian (Daily)", "Mingguan (Weekly)"])
        st.caption("Pengulangan hanya tersedia jika Auto Stop atau Jadwal Stop diaktifkan.")

        log_placeholder = st.empty()
        logs = st.session_state.get("logs", [])

        def log_callback(msg):
            logs.append(msg)
            st.session_state["logs"] = logs[-100:]
            try:
                log_placeholder.text("\n".join(st.session_state["logs"][-20:]))
            except Exception:
                print(msg)

        streaming = FFMPEG_PROCESS is not None and FFMPEG_PROCESS.poll() is None

        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            if st.button("▶️ Jadwalkan & Mulai Siaran", disabled=streaming, use_container_width=True):
                if not selected_paths:
                    st.error("Video belum dipilih!")
                elif mode == "Video + MP3 Playlist" and not audio_paths:
                    st.error("MP3 belum dipilih!")
                elif not stream_key:
                    st.error("Stream Key wajib diisi!")
                else:
                    target_start_dt = tz_jakarta.localize(datetime.combine(sched_date, sched_time))
                    current_dt = datetime.now(tz_jakarta)
                    start_delay = (target_start_dt - current_dt).total_seconds()
                    start_delay_seconds = max(0, int(start_delay))

                    total_duration_seconds = (stop_hours * 3600) + (stop_minutes * 60)

                    st.session_state["logs"] = []
                    thread = threading.Thread(
                        target=run_ffmpeg,
                        args=(mode, selected_paths, audio_paths, stream_key, is_shorts, None, 1, total_duration_seconds, start_delay_seconds, log_callback),
                        daemon=True,
                    )
                    thread.start()
                    time.sleep(0.5)
                    st.success(f"Siaran berhasil dijadwalkan! Akan mulai dalam {start_delay_seconds} detik.")

        with col_btn2:
            if st.button("⏹️ Hentikan Paksa Siaran", disabled=not streaming, use_container_width=True):
                stop_ffmpeg()
                st.warning("Siaran dihentikan.")

        if streaming:
            st.info("🔴 Status Live: Sedang mengudara...")

        if st.session_state.get("logs"):
            log_placeholder.text("\n".join(st.session_state["logs"][-20:]))

    elif menu == "📁 Berkas Video":
        st.title("📁 Daftar Berkas Video & Audio Tersimpan")
        st.markdown("Anda bisa mengunggah file baru di sini, dan file tersebut akan langsung tersedia untuk dipilih di menu **Buat & Atur Live**.")
        
        uploaded_files = st.file_uploader("Upload File Video / Audio Baru", type=["mp4", "mkv", "mov", "webm", "mp3"], accept_multiple_files=True)
        if uploaded_files:
            for f in uploaded_files:
                if f.name.endswith(".mp3"):
                    save_uploaded_audio(f, 1)
                else:
                    save_uploaded_file(f, 1)
            st.success("File berhasil diunggah dan disimpan ke berkas!")
            st.rerun()

        st.markdown("---")
        st.subheader("Daftar Berkas di Server:")
        files = list(UPLOAD_DIR.glob("*"))
        if files:
            for f in files:
                col_f1, col_f2 = st.columns([4, 1])
                with col_f1:
                    st.write(f"📄 **{f.name}** — `{f.stat().st_size // 1024 // 1024} MB`")
                with col_f2:
                    if st.button("Hapus", key=f"del_{f.name}"):
                        try:
                            f.unlink()
                            st.rerun()
                        except Exception:
                            pass
        else:
            st.info("Belum ada file tersimpan.")

    elif menu == "⚙️ Pengaturan & Cache":
        st.title("⚙️ Pengaturan Sistem")
        if st.button("🧹 Bersihkan Semua Cache & Berkas"):
            for f in UPLOAD_DIR.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            st.success("Cache berhasil dibersihkan!")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
yt_to_cc.py

Runs entirely on YOUR computer (not in Minecraft). Two modes:

AUDIO (tape):
  1. Download audio from a YouTube URL (or search term) with yt-dlp
  2. Convert it to .dfpwm with ffmpeg (which has a native DFPWM1a codec)
  3. Upload the .dfpwm to a temporary anonymous file host
  4. Print the in-game commands to download it and write it to a
     Computronics tape

VIDEO (32vid):
  1. Download a low-res video with yt-dlp
  2. Convert it to a 32vid file with sanjuuni (MCJack123), which packs
     frames into CC's 2x3 drawing characters with a per-frame palette
     and DFPWM audio in a single combined stream
  3. Upload the .32v to a temporary anonymous file host
  4. Print the in-game commands to fetch 32vid-player-mini and stream
     the file straight from the URL, no copying to the world folder

Requirements (install once, or use the Install buttons):
    pip install yt-dlp
    ffmpeg on PATH (winget install Gyan.FFmpeg)
    sanjuuni on PATH for video (github.com/MCJack123/sanjuuni/releases)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog
from pathlib import Path


SANJUUNI_RELEASE = "https://github.com/MCJack123/sanjuuni/releases/latest/download/sanjuuni-Win64.zip"
SANJUUNI_DIR = Path.home() / "sanjuuni"
PLAYER_URL = "https://raw.githubusercontent.com/MCJack123/sanjuuni/master/32vid-player-mini.lua"

# CC:Tweaked's default http.max_download. The player pulls the whole file
# into memory with http.get, so the 32vid must fit under this. We aim a
# little under it to leave headroom.
CC_MAX_DOWNLOAD = 16777216
SIZE_TARGET = int(CC_MAX_DOWNLOAD * 0.95)
MIN_FPS = 4


def is_installed(tool: str) -> bool:
    return shutil.which(tool) is not None


def run_logged(cmd, log_func):
    """Run a command and stream its output into the GUI log. Progress
    bars that redraw with carriage returns are split so each update
    shows up as its own line rather than being swallowed."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1, errors="replace")
    buf = ""
    last_progress = ""
    for chunk in iter(lambda: proc.stdout.read(1), ""):
        if chunk in ("\r", "\n"):
            line = buf.strip()
            buf = ""
            if not line:
                continue
            # Only log a progress line when it actually changes, to
            # avoid spamming hundreds of near identical lines.
            if "%" in line:
                if line != last_progress:
                    last_progress = line
                    log_func(line)
            else:
                log_func(line)
        else:
            buf += chunk
    proc.wait()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd)


def _add_sanjuuni_to_path():
    """Make ~/sanjuuni visible to shutil.which for this process."""
    d = str(SANJUUNI_DIR)
    if SANJUUNI_DIR.is_dir() and d not in os.environ["PATH"]:
        os.environ["PATH"] += os.pathsep + d


def install_sanjuuni(log_func, done_func):
    """Download the Windows release zip and unpack it to ~/sanjuuni."""
    import urllib.request
    import zipfile
    try:
        log_func("Downloading sanjuuni release zip...")
        SANJUUNI_DIR.mkdir(exist_ok=True)
        zip_path = SANJUUNI_DIR / "sanjuuni-Win64.zip"
        urllib.request.urlretrieve(SANJUUNI_RELEASE, zip_path)
        log_func("Extracting...")
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(SANJUUNI_DIR)
        zip_path.unlink()
        # The zip may or may not have a top level folder; flatten so
        # sanjuuni.exe sits directly in ~/sanjuuni.
        exe = next(SANJUUNI_DIR.rglob("sanjuuni.exe"), None)
        if exe and exe.parent != SANJUUNI_DIR:
            for f in exe.parent.iterdir():
                shutil.move(str(f), str(SANJUUNI_DIR / f.name))
        _add_sanjuuni_to_path()
        if is_installed("sanjuuni"):
            log_func(f"sanjuuni installed to {SANJUUNI_DIR}")
            done_func("sanjuuni", True)
        else:
            log_func("Extracted but sanjuuni.exe not found; check " + str(SANJUUNI_DIR))
            done_func("sanjuuni", False)
    except Exception as e:
        log_func(f"Failed to install sanjuuni: {e}")
        done_func("sanjuuni", False)


def install_ytdlp(log_func, done_func):
    """Install yt-dlp via pip."""
    try:
        log_func("Installing yt-dlp via pip...")
        subprocess.run([sys.executable, "-m", "pip", "install", "yt-dlp"],
                       check=True, capture_output=True, text=True)
        log_func("yt-dlp installed successfully!")
        done_func("yt-dlp", True)
    except Exception as e:
        log_func(f"Failed to install yt-dlp: {e}")
        done_func("yt-dlp", False)


def install_ffmpeg(log_func, done_func):
    """Install ffmpeg via winget."""
    try:
        log_func("Installing ffmpeg via winget (this may take a minute)...")
        subprocess.run(["winget", "install", "--id", "Gyan.FFmpeg", "-e",
                        "--accept-source-agreements", "--accept-package-agreements"],
                       check=True, capture_output=True, text=True)
        import glob
        for d in glob.glob(r"C:\Users\*\AppData\Local\Microsoft\WinGet\Links"):
            if d not in os.environ["PATH"]:
                os.environ["PATH"] += os.pathsep + d
        log_func("ffmpeg installed successfully!")
        done_func("ffmpeg", True)
    except Exception as e:
        log_func(f"Failed to install ffmpeg: {e}")
        done_func("ffmpeg", False)


def download_audio(query: str, out_dir: Path) -> Path:
    """Download best audio track as wav using yt-dlp. Accepts a URL or a
    plain search term (auto-prefixed with ytsearch1: if it's not a URL)."""
    if query.startswith(("http://", "https://")):
        # Strip playlist/radio params, keep only the video ID
        parsed = urlparse(query)
        params = parse_qs(parsed.query)
        clean_params = {k: v for k, v in params.items() if k == "v"}
        target = urlunparse(parsed._replace(query=urlencode(clean_params, doseq=True)))
    else:
        target = f"ytsearch1:{query}"
    out_template = str(out_dir / "audio.%(ext)s")

    print(f"Downloading audio for: {query}")
    subprocess.run(
        [
            "yt-dlp",
            "-x", "--audio-format", "wav",
            "--no-playlist",
            "-o", out_template,
            target,
        ],
        check=True,
    )

    wav_files = list(out_dir.glob("audio.*"))
    if not wav_files:
        print("yt-dlp did not produce an output file.", file=sys.stderr)
        sys.exit(1)
    return wav_files[0]


def download_video(query: str, out_dir: Path, log_func, max_height: int = 240) -> Path:
    """Download a small mp4 with yt-dlp. Resolution is capped because the
    output is a handful of characters wide anyway; smaller input means a
    much faster sanjuuni pass."""
    if query.startswith(("http://", "https://")):
        parsed = urlparse(query)
        params = parse_qs(parsed.query)
        clean_params = {k: v for k, v in params.items() if k == "v"}
        target = urlunparse(parsed._replace(query=urlencode(clean_params, doseq=True)))
    else:
        target = f"ytsearch1:{query}"
    out_template = str(out_dir / "video.%(ext)s")

    run_logged(
        [
            "yt-dlp",
            "-f", f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--newline",
            "-o", out_template,
            target,
        ],
        log_func,
    )
    files = list(out_dir.glob("video.*"))
    if not files:
        raise RuntimeError("yt-dlp did not produce a video file.")
    return files[0]


def video_duration(video_path: Path) -> float:
    """Length in seconds via ffprobe (ships with ffmpeg)."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


def limit_fps(video_path: Path, out_dir: Path, max_fps: int, log_func,
              duration: float = 0.0) -> Path:
    """Re-encode with ffmpeg at a lower frame rate, optionally cut to
    `duration` seconds. Fewer frames means a smaller 32vid and a faster
    sanjuuni pass. Returns the new path, or the original if nothing to do."""
    if max_fps <= 0 and duration <= 0:
        return video_path
    out = out_dir / f"video_{max_fps}fps_{int(duration)}s.mp4"
    msg = f"Re-encoding at {max_fps} fps" if max_fps > 0 else "Re-encoding"
    if duration > 0:
        msg += f", cut to {duration:.0f}s"
    log_func(msg + "...")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats", "-i", str(video_path)]
    if duration > 0:
        cmd += ["-t", f"{duration:.2f}"]
    if max_fps > 0:
        cmd += ["-r", str(max_fps)]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "copy", str(out)]
    run_logged(cmd, log_func)
    return out


def convert_within_limit(video_path: Path, out_path: Path, tmp_dir: Path,
                         monitors: str, scale: float, quality: str, dither: str,
                         max_fps: int, log_func) -> Path:
    """Convert, and if the 32vid is over CC's download cap, shrink and
    retry: first by lowering fps (down to MIN_FPS), then by cutting the
    length. 32vid size is close to linear in frame count, so each retry
    scales by the measured overshoot rather than guessing."""
    fps = max_fps if max_fps > 0 else 30
    duration = 0.0
    total = video_duration(video_path)

    for attempt in range(1, 6):
        src = limit_fps(video_path, tmp_dir, fps, log_func, duration)
        log_func(f"Converting with sanjuuni (attempt {attempt}: {fps} fps"
                 + (f", {duration:.0f}s" if duration > 0 else "") + ")...")
        convert_to_32vid(src, out_path, monitors, scale, quality, dither,
                         log_func)
        size = out_path.stat().st_size
        log_func(f"Result: {size / 1024 / 1024:.1f} MB (limit {SIZE_TARGET / 1024 / 1024:.1f} MB)")
        if size <= SIZE_TARGET:
            return src
        ratio = SIZE_TARGET / size

        if fps > MIN_FPS:
            new_fps = max(MIN_FPS, int(fps * ratio))
            if new_fps < fps:
                log_func(f"Too big. Dropping fps {fps} -> {new_fps}.")
                fps = new_fps
                continue
        # fps is already at the floor; cut the length instead
        current = duration if duration > 0 else total
        duration = max(5.0, current * ratio)
        log_func(f"Too big at minimum fps. Cutting length to {duration:.0f}s of {total:.0f}s.")

    raise RuntimeError("Could not get the video under the size limit after 5 attempts.")


def monitor_chars(blocks_w: int, blocks_h: int, scale: float):
    """Terminal size in characters of a multiblock CC monitor.
    Mirrors CC:Tweaked's own formula: the usable area is the block size
    minus border and margin (20/64 of a block each way), divided by the
    6x9 pixel font at the given text scale, rounded.
    1x1 at 0.5 scale gives 15x10; 8x6 at 0.5 gives 164x81."""
    w = max(1, round((64 * blocks_w - 20) / (6 * scale)))
    h = max(1, round((64 * blocks_h - 20) / (9 * scale)))
    return w, h


def convert_to_32vid(video_path: Path, out_path: Path, monitors: str,
                     scale: float, quality: str, dither: str,
                     log_func):
    """Run sanjuuni to produce a combined stream 32vid sized for one
    multiblock monitor.

    monitors: monitor size in BLOCKS, "WxH" (e.g. "3x2")
    scale:    monitor text scale (0.5 is the smallest, gives most pixels)
    quality:  "median" | "kmeans" | "octree"
    dither:   "floyd" | "ordered" | "none"
    """
    bw, bh = (int(v) for v in monitors.lower().split("x"))
    cw, ch = monitor_chars(bw, bh, scale)
    # Each character cell is 2x3 pixels in CC's drawing characters.
    px_w, px_h = cw * 2, ch * 3
    log_func(f"Monitor {bw}x{bh} blocks at scale {scale}: {cw}x{ch} chars, {px_w}x{px_h} px")

    cmd = ["sanjuuni", "-i", str(video_path), "-o", str(out_path),
           "-3",          # 32vid output (combined stream since sanjuuni 0.5)
           "-L",          # CIELAB colour matching
           "-W", str(px_w), "-H", str(px_h)]
    cmd.append("-m")  # no audio stream — audio must be done separately
    if quality == "kmeans":
        cmd.append("-k")
    elif quality == "octree":
        cmd.append("-8")
    if dither == "ordered":
        cmd.append("-O")
    elif dither == "none":
        cmd.append("-t")
    log_func("Running: " + " ".join(cmd))
    run_logged(cmd, log_func)


def convert_to_dfpwm(audio_path: Path, out_path: Path, volume_db: float = 0.0,
                     compressor: bool = False, gate: bool = False):
    """ffmpeg has a native DFPWM1a codec. CC:Tweaked expects 48kHz mono."""
    print("Converting to DFPWM (48kHz mono)...")
    af_filters = []
    if gate:
        af_filters.append("agate=threshold=0.01:range=0.1:attack=5:release=50")
    if compressor:
        af_filters.append("acompressor=threshold=0.1:ratio=4:attack=5:release=50:makeup=2")
    if volume_db != 0.0:
        af_filters.append(f"volume={volume_db}dB")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "48000",
    ]
    if af_filters:
        cmd += ["-af", ",".join(af_filters)]
    cmd += ["-c:a", "dfpwm", "-f", "dfpwm", str(out_path)]
    subprocess.run(cmd, check=True)


CHUNK_SIZE = 16 * 1024 * 1024  # 16 MB


def upload_chunked(file_path: Path, log_func) -> str:
    """Split a file into 16 MB chunks, upload each to catbox, then create
    a jukebox txt listing the catbox IDs and upload that too. Returns the
    catbox URL of the txt file."""
    data = file_path.read_bytes()
    chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)]
    log_func(f"Splitting into {len(chunks)} chunk(s) of up to 16 MB each...")

    ids = []
    for i, chunk in enumerate(chunks):
        chunk_path = file_path.parent / f"{file_path.stem}_part{i}{file_path.suffix}"
        chunk_path.write_bytes(chunk)
        log_func(f"Uploading chunk {i + 1}/{len(chunks)} ({len(chunk) / 1024 / 1024:.1f} MB)...")
        url = _upload_catbox(chunk_path)
        # Extract the file ID from the catbox URL (e.g. https://files.catbox.moe/b8vt60.32v -> b8vt60)
        catbox_id = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        ids.append(catbox_id)
        chunk_path.unlink()
        log_func(f"  Chunk {i + 1} uploaded: {catbox_id}")

    # Build the jukebox txt
    txt_content = "# jukebox\n" + "\n".join(ids) + "\n"
    txt_path = file_path.parent / "jukebox.txt"
    txt_path.write_text(txt_content)
    log_func("Uploading jukebox index file...")
    txt_url = _upload_catbox(txt_path)
    txt_path.unlink()
    log_func(f"Jukebox index uploaded: {txt_url}")
    return txt_url


def upload_file(file_path: Path) -> str:
    """Upload a file, trying multiple hosts until one works."""
    hosts = [
        _upload_catbox,
        _upload_litterbox,
        _upload_fileio,
    ]
    last_err = None
    for host in hosts:
        try:
            return host(file_path)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All upload hosts failed. Last error: {last_err}")


def _upload_catbox(file_path: Path) -> str:
    result = subprocess.run(
        [
            "curl", "-s",
            "-F", "reqtype=fileupload",
            "-F", "userhash=",
            "-F", f"fileToUpload=@{file_path}",
            "https://catbox.moe/user/api.php",
        ],
        capture_output=True, text=True, check=True,
    )
    url = result.stdout.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox: {result.stdout} {result.stderr}")
    return url


def _upload_litterbox(file_path: Path) -> str:
    result = subprocess.run(
        [
            "curl", "-s",
            "-F", "reqtype=fileupload",
            "-F", "time=72h",
            "-F", f"fileToUpload=@{file_path}",
            "https://litterbox.catbox.moe/resources/internals/api.php",
        ],
        capture_output=True, text=True, check=True,
    )
    url = result.stdout.strip()
    if not url.startswith("http"):
        raise RuntimeError(f"litterbox: {result.stdout} {result.stderr}")
    return url


def _upload_fileio(file_path: Path) -> str:
    import json
    result = subprocess.run(
        ["curl", "-s", "-F", f"file=@{file_path}", "https://file.io"],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    if not data.get("success"):
        raise RuntimeError(f"file.io: {result.stdout}")
    return data["link"]


def run_conversion(mode: str, query: str, keep: bool, volume_db: float,
                   compressor: bool, gate: bool, monitors: str, scale: float,
                   monitor_side: str, quality: str, dither: str,
                   max_fps: int, tape: bool, loop: bool, larger16: bool,
                   local_file: str,
                   log_func, done_func):
    """Run the full download/convert/upload pipeline in a background thread."""
    try:
        _add_sanjuuni_to_path()
        missing = []
        if not local_file and not is_installed("yt-dlp"):
            missing.append("yt-dlp — click 'Install' next to yt-dlp")
        if not is_installed("ffmpeg"):
            missing.append("ffmpeg — click 'Install' next to ffmpeg")
        if not is_installed("curl"):
            missing.append("curl — should be built into Windows 10+")
        if mode in ("video", "both") and not is_installed("sanjuuni"):
            missing.append("sanjuuni — click 'Install' next to sanjuuni")
        if missing:
            log_func("Missing required tools:\n" + "\n".join(f"  - {m}" for m in missing))
            done_func(False)
            return

        output_dir = Path.home() / "dfpwm_output"
        output_dir.mkdir(exist_ok=True)

        video_url = None
        audio_url = None

        if mode in ("video", "both"):
            out_path = output_dir / "output.32v"
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                if local_file:
                    log_func(f"Using local file: {local_file}")
                    video_path = Path(local_file)
                else:
                    log_func(f"Downloading video for: {query}")
                    video_path = download_video(query, tmp_dir, log_func)
                if larger16:
                    log_func("Larger than 16 mode: no size limit, will split into chunks.")
                    log_func("Speed guide: median is fast, kmeans is several times slower, octree slowest.")
                    src = limit_fps(video_path, tmp_dir, max_fps, log_func)
                    log_func("Converting with sanjuuni...")
                    convert_to_32vid(src, out_path, monitors, scale, quality,
                                     dither, log_func)
                else:
                    log_func(f"Target: under {SIZE_TARGET / 1024 / 1024:.1f} MB "
                             f"(CC:Tweaked max_download {CC_MAX_DOWNLOAD}).")
                    log_func("Speed guide: median is fast, kmeans is several times slower, octree slowest.")
                    convert_within_limit(video_path, out_path, tmp_dir, monitors,
                                         scale, quality, dither,
                                         max_fps, log_func)
            size_kb = out_path.stat().st_size / 1024
            log_func(f"Encoded: {out_path.name} ({size_kb:.1f} KB)")
            if larger16:
                log_func("Splitting and uploading video chunks...")
                video_url = upload_chunked(out_path, log_func)
            else:
                log_func("Uploading video...")
                video_url = upload_file(out_path)
            log_func(f"Video uploaded: {video_url}")

        if mode in ("audio", "both"):
            out_path = output_dir / "output.dfpwm"
            if local_file:
                log_func(f"Using local file: {local_file}")
                convert_to_dfpwm(Path(local_file), out_path, volume_db, compressor, gate)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_dir = Path(tmp)
                    log_func(f"Downloading audio for: {query}")
                    wav_path = download_audio(query, tmp_dir)
                    convert_to_dfpwm(wav_path, out_path, volume_db, compressor, gate)
            size_kb = out_path.stat().st_size / 1024
            log_func(f"Encoded: {out_path.name} ({size_kb:.1f} KB)")
            log_func("Uploading audio...")
            audio_url = upload_file(out_path)
            log_func(f"Audio uploaded: {audio_url}")

        log_func("")
        log_func("=" * 50)
        log_func("Run these in-game on your CC:Tweaked computer:")
        log_func("")
        if video_url:
            log_func("Video:")
            vid_pastebin = "tvKLKwjS" if larger16 else "ftfDg65u"
            vid_args = video_url
            if monitor_side:
                vid_args += f" {monitor_side}"
            if tape:
                vid_args += " tape"
            if loop:
                vid_args += " loop"
            log_func(f"  pastebin run {vid_pastebin} {vid_args}")
            if not monitor_side:
                log_func("  (no monitor side set: plays on the computer's own screen)")
            log_func("")
            log_func("Change 'Monitor side' in the app if the monitor is elsewhere:")
            log_func("top, bottom, left, right, front, back, or monitor_0 over a modem.")
        if audio_url:
            if video_url:
                log_func("")
            log_func("Audio:")
            log_func(f'  pastebin run D1m1dq0n "{audio_url}" song.dfpwm')
        log_func("")
        log_func(f"Local files: {output_dir}")
        log_func("=" * 50)
        done_func(True)
    except subprocess.CalledProcessError as e:
        log_func(f"\nError: command failed — {e}")
        done_func(False)
    except Exception as e:
        log_func(f"\nUnexpected error: {e}")
        done_func(False)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YT to CC — CC:Tweaked audio / video converter")
        self.geometry("1100x600")
        _add_sanjuuni_to_path()
        self.resizable(True, True)

        # --- Dependencies row ---
        dep_frame = ttk.LabelFrame(self, text="Dependencies", padding=8)
        dep_frame.pack(fill="x", padx=10, pady=(10, 0))

        # yt-dlp
        ytdlp_row = ttk.Frame(dep_frame)
        ytdlp_row.pack(fill="x", pady=2)
        self.ytdlp_status = ttk.Label(ytdlp_row, width=12)
        self.ytdlp_status.pack(side="left")
        ttk.Label(ytdlp_row, text="yt-dlp").pack(side="left", padx=(0, 8))
        self.ytdlp_btn = ttk.Button(ytdlp_row, text="Install", command=self.install_ytdlp)
        self.ytdlp_btn.pack(side="left")

        # ffmpeg
        ffmpeg_row = ttk.Frame(dep_frame)
        ffmpeg_row.pack(fill="x", pady=2)
        self.ffmpeg_status = ttk.Label(ffmpeg_row, width=12)
        self.ffmpeg_status.pack(side="left")
        ttk.Label(ffmpeg_row, text="ffmpeg").pack(side="left", padx=(0, 8))
        self.ffmpeg_btn = ttk.Button(ffmpeg_row, text="Install", command=self.install_ffmpeg)
        self.ffmpeg_btn.pack(side="left")

        # sanjuuni (video only)
        sanjuuni_row = ttk.Frame(dep_frame)
        sanjuuni_row.pack(fill="x", pady=2)
        self.sanjuuni_status = ttk.Label(sanjuuni_row, width=12)
        self.sanjuuni_status.pack(side="left")
        ttk.Label(sanjuuni_row, text="sanjuuni (video only)").pack(side="left", padx=(0, 8))
        self.sanjuuni_btn = ttk.Button(sanjuuni_row, text="Install", command=self.install_sanjuuni)
        self.sanjuuni_btn.pack(side="left")

        # curl
        curl_row = ttk.Frame(dep_frame)
        curl_row.pack(fill="x", pady=2)
        self.curl_status = ttk.Label(curl_row, width=12)
        self.curl_status.pack(side="left")
        ttk.Label(curl_row, text="curl (built-in)").pack(side="left")

        self.refresh_dep_status()

        # --- Input row ---
        input_frame = ttk.Frame(self, padding=10)
        input_frame.pack(fill="x")

        ttk.Label(input_frame, text="YouTube URL or search:").pack(side="left")
        self.query_var = tk.StringVar()
        self.entry = ttk.Entry(input_frame, textvariable=self.query_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.entry.bind("<Return>", lambda e: self.start())

        # --- Local file row ---
        file_frame = ttk.Frame(self, padding=(10, 0))
        file_frame.pack(fill="x")
        ttk.Label(file_frame, text="Or pick a local file:").pack(side="left")
        self.local_file_var = tk.StringVar()
        self.file_entry = ttk.Entry(file_frame, textvariable=self.local_file_var)
        self.file_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        ttk.Button(file_frame, text="Browse", command=self._browse_file).pack(side="left")

        # --- Mode row ---
        mode_frame = ttk.Frame(self, padding=(10, 0))
        mode_frame.pack(fill="x")
        self.mode_frame = mode_frame
        ttk.Label(mode_frame, text="Mode:").pack(side="left")
        self.mode_var = tk.StringVar(value="audio")
        ttk.Radiobutton(mode_frame, text="Audio (tape)", variable=self.mode_var,
                        value="audio", command=self._mode_changed).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(mode_frame, text="Video (32vid)", variable=self.mode_var,
                        value="video", command=self._mode_changed).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(mode_frame, text="Both", variable=self.mode_var,
                        value="both", command=self._mode_changed).pack(side="left", padx=(8, 0))

        # --- Video options row ---
        self.vid_frame = ttk.Frame(self, padding=(10, 4))
        ttk.Label(self.vid_frame, text="Monitor blocks (WxH):").pack(side="left")
        self.monitors_var = tk.StringVar(value="3x2")
        ttk.Entry(self.vid_frame, textvariable=self.monitors_var, width=6).pack(side="left", padx=(4, 8))
        ttk.Label(self.vid_frame, text="Scale:").pack(side="left")
        self.scale_var = tk.StringVar(value="0.5")
        ttk.Combobox(self.vid_frame, textvariable=self.scale_var, width=4, state="readonly",
                     values=("0.5", "1", "1.5", "2")).pack(side="left", padx=(4, 12))
        ttk.Label(self.vid_frame, text="Monitor side:").pack(side="left")
        self.side_var = tk.StringVar(value="top")
        ttk.Combobox(self.vid_frame, textvariable=self.side_var, width=9,
                     values=("top", "bottom", "left", "right", "front", "back", "monitor_0")).pack(side="left", padx=(4, 12))
        ttk.Label(self.vid_frame, text="Colour:").pack(side="left")
        self.quality_var = tk.StringVar(value="median")
        ttk.Combobox(self.vid_frame, textvariable=self.quality_var, width=8, state="readonly",
                     values=("median", "kmeans", "octree")).pack(side="left", padx=(4, 12))
        ttk.Label(self.vid_frame, text="Dither:").pack(side="left")
        self.dither_var = tk.StringVar(value="ordered")
        ttk.Combobox(self.vid_frame, textvariable=self.dither_var, width=8, state="readonly",
                     values=("floyd", "ordered", "none")).pack(side="left", padx=(4, 12))
        ttk.Label(self.vid_frame, text="Max FPS:").pack(side="left", padx=(12, 0))
        self.fps_var = tk.StringVar(value="15")
        ttk.Entry(self.vid_frame, textvariable=self.fps_var, width=4).pack(side="left", padx=(4, 0))
        self.tape_var = tk.BooleanVar()
        ttk.Checkbutton(self.vid_frame, text="Tape", variable=self.tape_var).pack(side="left", padx=(12, 0))
        self.loop_var = tk.BooleanVar()
        ttk.Checkbutton(self.vid_frame, text="Loop", variable=self.loop_var).pack(side="left", padx=(8, 0))
        self.larger16_var = tk.BooleanVar()
        ttk.Checkbutton(self.vid_frame, text="Larger than 16", variable=self.larger16_var).pack(side="left", padx=(8, 0))

        # --- Options row ---
        opt_frame = ttk.Frame(self, padding=(10, 0))
        opt_frame.pack(fill="x")

        self.keep_var = tk.BooleanVar()
        ttk.Checkbutton(opt_frame, text="Keep local .dfpwm file", variable=self.keep_var).pack(side="left")

        self.compressor_var = tk.BooleanVar()
        ttk.Checkbutton(opt_frame, text="Compressor", variable=self.compressor_var).pack(side="left", padx=(8, 0))

        self.gate_var = tk.BooleanVar()
        ttk.Checkbutton(opt_frame, text="Gate", variable=self.gate_var).pack(side="left", padx=(8, 0))

        self.go_btn = ttk.Button(opt_frame, text="Convert", command=self.start)
        self.go_btn.pack(side="right")

        # --- Volume boost row ---
        vol_frame = ttk.Frame(self, padding=(10, 4))
        vol_frame.pack(fill="x")

        ttk.Label(vol_frame, text="Volume boost:").pack(side="left")
        self.volume_var = tk.DoubleVar(value=0.0)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=100, variable=self.volume_var,
                                       orient="horizontal", command=self._update_vol_label)
        self.volume_slider.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.vol_label = ttk.Label(vol_frame, text="0 dB", width=8)
        self.vol_label.pack(side="left")

        # --- Log area ---
        self.log = scrolledtext.ScrolledText(self, state="disabled", wrap="word",
                                             font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

    def _browse_file(self):
        path = filedialog.askopenfilename(
            title="Select an audio file",
            filetypes=[("Audio/Video files", "*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma *.mp4 *.avi *.mkv *.webm"),
                       ("All files", "*.*")])
        if path:
            self.local_file_var.set(path)

    def _update_vol_label(self, _=None):
        self.vol_label.configure(text=f"+{int(self.volume_var.get())} dB")

    def _fps_value(self) -> int:
        try:
            return max(0, int(self.fps_var.get().strip()))
        except ValueError:
            return 15

    def _mode_changed(self):
        # Show the video options in video or both mode, slotted in above the
        # generic options row.
        if self.mode_var.get() in ("video", "both"):
            self.vid_frame.pack(fill="x", after=self.mode_frame)
        else:
            self.vid_frame.pack_forget()

    def refresh_dep_status(self):
        for tool, label, btn in [
            ("yt-dlp", self.ytdlp_status, self.ytdlp_btn),
            ("ffmpeg", self.ffmpeg_status, self.ffmpeg_btn),
            ("sanjuuni", self.sanjuuni_status, self.sanjuuni_btn),
            ("curl", self.curl_status, None),
        ]:
            if is_installed(tool):
                label.configure(text="Installed", foreground="green")
                if btn:
                    btn.configure(state="disabled")
            else:
                label.configure(text="Not found", foreground="red")
                if btn:
                    btn.configure(state="normal")

    def _on_install_done(self, tool: str, success: bool):
        self.after(0, self.refresh_dep_status)

    def install_ytdlp(self):
        self.ytdlp_btn.configure(state="disabled")
        self.log_msg("Installing yt-dlp...")
        threading.Thread(
            target=install_ytdlp,
            args=(self.log_msg, self._on_install_done),
            daemon=True,
        ).start()

    def install_ffmpeg(self):
        self.ffmpeg_btn.configure(state="disabled")
        self.log_msg("Installing ffmpeg...")
        threading.Thread(
            target=install_ffmpeg,
            args=(self.log_msg, self._on_install_done),
            daemon=True,
        ).start()

    def install_sanjuuni(self):
        self.sanjuuni_btn.configure(state="disabled")
        self.log_msg("Installing sanjuuni...")
        threading.Thread(
            target=install_sanjuuni,
            args=(self.log_msg, self._on_install_done),
            daemon=True,
        ).start()

    def log_msg(self, text: str):
        self.after(0, self._append_log, text)

    def _append_log(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_done(self, success: bool):
        self.after(0, self._unlock)

    def _unlock(self):
        self.go_btn.configure(state="normal")
        self.entry.configure(state="normal")

    def start(self):
        query = self.query_var.get().strip()
        local_file = self.local_file_var.get().strip()
        if not query and not local_file:
            return
        self.go_btn.configure(state="disabled")
        self.entry.configure(state="disabled")
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

        thread = threading.Thread(
            target=run_conversion,
            args=(self.mode_var.get(), query, self.keep_var.get(),
                  self.volume_var.get(), self.compressor_var.get(),
                  self.gate_var.get(), self.monitors_var.get().strip() or "1x1",
                  float(self.scale_var.get()), self.side_var.get().strip(),
                  self.quality_var.get(),
                  self.dither_var.get(),
                  self._fps_value(), self.tape_var.get(), self.loop_var.get(),
                  self.larger16_var.get(), local_file,
                  self.log_msg, self.on_done),
            daemon=True,
        )
        thread.start()


if __name__ == "__main__":
    App().mainloop()

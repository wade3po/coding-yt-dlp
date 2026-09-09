"""
自动化流水线：下载 → 字幕识别翻译 → 烧录 → 按日期归档
用法: python auto_pipeline.py <YouTube链接>
"""

import sys
import os
import subprocess
import re
from datetime import datetime

# ─── 路径配置 ──────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
YT_DLP      = os.path.join(BASE_DIR, 'yt-dlp.exe')
FFMPEG      = os.path.join(BASE_DIR, 'ffmpeg.exe')
COOKIES     = os.path.join(BASE_DIR, 'cookies.txt')
WHISPER_MODEL = "base"

# 今天的日期文件夹，例如 downloads/2026-07-31
today = datetime.now().strftime("%Y-%m-%d")
OUTPUT_DIR = os.path.join(BASE_DIR, 'downloads', today)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── 工具函数 ──────────────────────────────────────────────
def log(msg):
    print(f"\n{'='*50}\n▶ {msg}\n{'='*50}")

def format_srt_time(seconds):
    ms = int((seconds % 1) * 1000)
    s  = int(seconds) % 60
    m  = int(seconds) // 60 % 60
    h  = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_srt(segments, output_path):
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = format_srt_time(seg["start"])
            end   = format_srt_time(seg["end"])
            text  = seg["text"].strip()
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
    print(f"  字幕文件: {output_path}")

# ─── 第一步：下载视频 ────────────────────────────────────────
def step_download(url):
    log("第一步：下载视频")

    cmd = [
        YT_DLP,
        '--js-runtimes', 'node',
        '--ffmpeg-location', FFMPEG,
        '-f', 'bestvideo+bestaudio/best',
        '-o', os.path.join(OUTPUT_DIR, '%(title)s.%(ext)s'),
        '--newline',
        '--no-playlist',
    ]
    if os.path.isfile(COOKIES):
        cmd += ['--cookies', COOKIES]
    cmd.append(url)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    downloaded_file = None
    for line in proc.stdout:
        line = line.rstrip()
        print(f"  {line}")
        # 从输出里捕获最终文件名
        m = re.search(r'\[(?:download|Merger)\] Destination: (.+)', line)
        if m:
            downloaded_file = m.group(1).strip()
        # 合并后的文件名
        m2 = re.search(r'\[ffmpeg\] Merging formats into "(.+)"', line)
        if m2:
            downloaded_file = m2.group(1).strip()

    proc.wait()
    if proc.returncode != 0:
        print("  ❌ 下载失败")
        sys.exit(1)

    # 如果没捕获到，扫描目录找最新文件
    if not downloaded_file or not os.path.isfile(downloaded_file):
        files = sorted(
            [os.path.join(OUTPUT_DIR, f) for f in os.listdir(OUTPUT_DIR)],
            key=os.path.getmtime, reverse=True
        )
        # 排除 srt 和 mp4（还没生成），找视频源文件
        video_exts = ('.webm', '.mp4', '.mkv', '.mov', '.avi')
        for f in files:
            if f.endswith(video_exts) and '_字幕' not in f:
                downloaded_file = f
                break

    print(f"  ✅ 下载完成: {downloaded_file}")
    return downloaded_file

# ─── 第二步：Whisper 识别 + 中文翻译 ────────────────────────
def step_subtitle(video_path):
    log("第二步：语音识别 + 中文翻译")

    import whisper
    print(f"  加载 Whisper {WHISPER_MODEL} 模型...")
    model = whisper.load_model(WHISPER_MODEL)

    print("  识别中（英文 → 中文）...")
    result = model.transcribe(
        video_path,
        task="translate",   # 先转成英文
        language="en",
        verbose=False
    )
    segments = result["segments"]

    # 尝试用 deep-translator 翻译成中文
    try:
        from deep_translator import MyMemoryTranslator
        print("  翻译成中文...")
        translator = MyMemoryTranslator(source="en-US", target="zh-CN")
        for seg in segments:
            try:
                seg["text"] = translator.translate(seg["text"].strip())
            except Exception:
                pass  # 翻译失败保留英文
        print("  ✅ 中文翻译完成")
    except ImportError:
        print("  ⚠️ 未安装 deep-translator，保留英文字幕")

    base     = os.path.splitext(video_path)[0]
    srt_path = base + "_zh.srt"
    generate_srt(segments, srt_path)
    return srt_path

# ─── 第三步：烧录字幕 ────────────────────────────────────────
def step_burn(video_path, srt_path):
    log("第三步：烧录字幕到视频")

    base        = os.path.splitext(video_path)[0]
    output_path = base + "_字幕.mp4"

    # FFmpeg 路径中反斜杠需要转义
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

    cmd = [
        FFMPEG,
        "-i", video_path,
        "-vf", (
            f"subtitles='{srt_escaped}':"
            "force_style='FontName=Arial,FontSize=14,"
            "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2'"
        ),
        "-c:v", "libx264",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        output_path
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    for line in proc.stdout:
        line = line.rstrip()
        if 'frame=' in line or 'error' in line.lower():
            print(f"  {line}")
    proc.wait()

    if proc.returncode == 0:
        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"  ✅ 输出: {output_path} ({size_mb:.1f} MB)")
    else:
        print("  ❌ 烧录失败")
        sys.exit(1)

    return output_path

# ─── 主流程 ─────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        print("用法: python auto_pipeline.py <YouTube链接>")
        print("例如: python auto_pipeline.py https://www.youtube.com/watch?v=xxxxx")
        sys.exit(1)

    url = sys.argv[1].strip()
    print(f"\n🚀 开始处理: {url}")
    print(f"📁 输出目录: {OUTPUT_DIR}")

    video_path  = step_download(url)
    srt_path    = step_subtitle(video_path)
    final_path  = step_burn(video_path, srt_path)

    print(f"\n🎉 全部完成！")
    print(f"📄 字幕: {srt_path}")
    print(f"🎬 视频: {final_path}")

if __name__ == "__main__":
    main()

"""
下载视频 → 裁剪9段 → 每段字幕翻译烧录
用法: python clip_and_process.py
"""
import os
import sys
import subprocess
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YT_DLP   = os.path.join(BASE_DIR, 'yt-dlp.exe')
FFMPEG   = os.path.join(BASE_DIR, 'ffmpeg.exe')
COOKIES  = os.path.join(BASE_DIR, 'cookies.txt')

today      = datetime.now().strftime("%Y-%m-%d")
OUTPUT_DIR = os.path.join(BASE_DIR, 'downloads', today)
os.makedirs(OUTPUT_DIR, exist_ok=True)

URL = "https://www.youtube.com/watch?v=nxBssMWBeHc"

# 裁剪方案：(片段名, 开始时间, 结束时间)
CLIPS = [
    ("clip1_effort",        "00:00:00", "00:01:15"),  # 努力：effort is between you and you
    ("clip2_scars",         "00:01:15", "00:02:00"),  # 伤疤与潜力：Goggins + scars show desire
    ("clip3_kobe",          "00:02:00", "00:03:04"),  # 科比+痴迷：mastery + stop haggling
    ("clip4_will_to_win",   "00:03:04", "00:03:50"),  # 求胜意志：will to win → winners vs losers
    ("clip5_tired",         "00:03:50", "00:04:50"),  # 疲惫前提：built by tired people（含自然停顿）
    ("clip6_hardwork",      "00:04:50", "00:05:57"),  # 努力vs做梦：diploma + no shortcuts
    ("clip7_patience",      "00:05:57", "00:06:50"),  # 耐心与回报：endure patiently
    ("clip8_repetition",    "00:06:50", "00:07:45"),  # 重复训练：do the reps
    ("clip9_breakthrough",  "00:07:45", "00:08:48"),  # 控制+突破：control + break through
]

def log(msg):
    print(f"\n{'='*55}\n>> {msg}\n{'='*55}")

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

# ── 第一步：下载完整视频 ──────────────────────────────────────
def step_download():
    log("第一步：下载原始视频")
    raw_path = os.path.join(OUTPUT_DIR, "source.mp4")
    if os.path.isfile(raw_path):
        print(f"  已存在，跳过下载: {raw_path}")
        return raw_path

    cmd = [
        YT_DLP,
        '--js-runtimes', 'node',
        '--ffmpeg-location', FFMPEG,
        '--cookies', COOKIES,
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        '--merge-output-format', 'mp4',
        '-o', raw_path,
        '--newline',
        '--no-playlist',
        URL,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace')
    for line in proc.stdout:
        print(f"  {line.rstrip()}")
    proc.wait()
    if proc.returncode != 0:
        print("  ❌ 下载失败"); sys.exit(1)
    print(f"  ✅ 下载完成: {raw_path}")
    return raw_path

# ── 第二步：裁剪9段 ──────────────────────────────────────────
def step_clip(raw_path):
    log("第二步：裁剪片段")
    clip_paths = []
    for name, start, end in CLIPS:
        out = os.path.join(OUTPUT_DIR, f"{name}.mp4")
        clip_paths.append(out)
        if os.path.isfile(out):
            print(f"  已存在，跳过: {out}")
            continue
        cmd = [FFMPEG, '-y', '-i', raw_path,
               '-ss', start, '-to', end,
               '-c', 'copy', out]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            print(f"  ✅ {name}: {start} → {end}")
        else:
            print(f"  ❌ 裁剪失败: {name}\n{r.stderr[-300:]}")
    return clip_paths

# ── 第三步：Whisper 识别 + 翻译 ──────────────────────────────
def step_subtitle(video_path):
    import whisper
    model = whisper.load_model("base")
    result = model.transcribe(video_path, task="translate", language="en", verbose=False)
    segments = result["segments"]

    try:
        from deep_translator import MyMemoryTranslator
        translator = MyMemoryTranslator(source="en-US", target="zh-CN")
        for seg in segments:
            try:
                seg["text"] = translator.translate(seg["text"].strip())
            except Exception:
                pass
        print("  ✅ 中文翻译完成")
    except ImportError:
        print("  ⚠️ 未安装 deep-translator，保留英文字幕")

    srt_path = os.path.splitext(video_path)[0] + "_zh.srt"
    generate_srt(segments, srt_path)
    return srt_path

# ── 第四步：烧录字幕 ─────────────────────────────────────────
def step_burn(video_path, srt_path):
    output_path = os.path.splitext(video_path)[0] + "_字幕.mp4"
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    cmd = [
        FFMPEG, '-y', '-i', video_path,
        '-vf', (
            f"subtitles='{srt_escaped}':"
            "force_style='FontName=Arial,FontSize=14,"
            "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2'"
        ),
        '-c:v', 'libx264', '-c:a', 'aac', '-b:a', '128k',
        output_path,
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
        print(f"  ❌ 烧录失败: {video_path}")
    return output_path

# ── 主流程 ───────────────────────────────────────────────────
def main():
    print(f"\n[START] 开始处理")
    print(f"[DIR] 输出目录: {OUTPUT_DIR}")

    raw_path   = step_download()
    clip_paths = step_clip(raw_path)

    log("第三+四步：字幕翻译 + 烧录（逐段处理）")
    for i, clip_path in enumerate(clip_paths, 1):
        name = os.path.basename(clip_path)
        final = os.path.splitext(clip_path)[0] + "_字幕.mp4"
        if os.path.isfile(final):
            print(f"  [{i}/{len(clip_paths)}] 已存在，跳过: {name}")
            continue
        print(f"\n  [{i}/{len(clip_paths)}] 处理: {name}")
        srt_path = step_subtitle(clip_path)
        step_burn(clip_path, srt_path)

    print(f"\n[DONE] 全部完成！成品文件在: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()

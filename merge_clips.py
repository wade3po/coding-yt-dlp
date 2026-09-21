"""
merge_clips.py
把 start.mp4 / demo.mp4 / end.mp4 拼接，
并在 start 片段上居中烧入标题两行字幕。
"""

import subprocess
import os
import tempfile
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FFMPEG   = os.path.join(BASE_DIR, 'ffmpeg.exe')

START = os.path.join(BASE_DIR, 'start.mp4')
DEMO  = os.path.join(BASE_DIR, 'downloads', 'demo.mp4')
END   = os.path.join(BASE_DIR, 'end.mp4')
OUT   = os.path.join(BASE_DIR, 'downloads', 'merged_output.mp4')

# ── 标题文字（两行）──
LINE1 = "《当幸福来敲门》"
LINE2 = "成年人的崩溃，只在一瞬间"

TMP = tempfile.mkdtemp(prefix='merge_')

def run(cmd):
    print('▶', ' '.join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print(r.stderr[-500:])
        raise RuntimeError(f'ffmpeg 失败，退出码 {r.returncode}')
    return r

def get_resolution(path):
    """用 ffmpeg stderr 解析分辨率"""
    r = subprocess.run(
        [FFMPEG, '-i', path],
        capture_output=True, text=True, encoding='utf-8', errors='replace'
    )
    import re
    m = re.search(r'(\d{2,5})x(\d{2,5})', r.stderr)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1920, 1080  # 默认

print("=" * 50)
print("Step 1: 获取视频分辨率")
w, h = get_resolution(START)
print(f"  分辨率: {w}x{h}")

# 字体大小根据分辨率自适应
font_size = max(36, h // 18)
line_gap  = int(font_size * 1.4)
# 两行居中：第一行在中线上方，第二行在中线下方
y1 = f"(h-{line_gap*2})/2"
y2 = f"(h-{line_gap*2})/2+{line_gap}"

print("=" * 50)
print("Step 2: 给 start.mp4 烧入标题字幕")

FONT = r"C\:/Windows/Fonts/msyh.ttc"

# drawtext 滤镜：白色文字 + 黑色描边，居中两行
vf = (
    f"drawtext=fontfile='{FONT}':text='{LINE1}'"
    f":fontsize={font_size}"
    f":fontcolor=white"
    f":borderw=3:bordercolor=black"
    f":x=(w-text_w)/2:y={y1},"
    f"drawtext=fontfile='{FONT}':text='{LINE2}'"
    f":fontsize={font_size}"
    f":fontcolor=white"
    f":borderw=3:bordercolor=black"
    f":x=(w-text_w)/2:y={y2}"
)

start_titled = os.path.join(TMP, 'start_titled.mp4')
run([
    FFMPEG, '-y', '-i', START,
    '-vf', vf,
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
    '-c:a', 'copy',
    start_titled
])
print("  ✅ 完成")

print("=" * 50)
print("Step 3: 拼接三段视频（start_titled → demo → end）")

# 用 filter_complex 统一缩放+拼接，避免音视频不同步
run([
    FFMPEG, '-y',
    '-i', start_titled,
    '-i', DEMO,
    '-i', END,
    '-filter_complex',
    f'[0:v]scale={w}:{h},setsar=1,fps=30[v0];'
    f'[1:v]scale={w}:{h},setsar=1,fps=30[v1];'
    f'[2:v]scale={w}:{h},setsar=1,fps=30[v2];'
    f'[0:a]aresample=44100[a0];'
    f'[1:a]aresample=44100[a1];'
    f'[2:a]aresample=44100[a2];'
    f'[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vout][aout]',
    '-map', '[vout]', '-map', '[aout]',
    '-c:v', 'libx264', '-preset', 'fast', '-crf', '18',
    '-c:a', 'aac', '-b:a', '128k',
    OUT
])
print("  ✅ 完成")

shutil.rmtree(TMP, ignore_errors=True)

size_mb = os.path.getsize(OUT) / 1024 / 1024
print("=" * 50)
print(f"🎉 完成！输出文件: {OUT}")
print(f"   大小: {size_mb:.1f} MB")

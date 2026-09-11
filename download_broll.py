"""
download_broll.py
批量下载高燃运动素材，每个搜索词各20个，时长15-25秒，无声音
"""

import subprocess
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YTDLP    = os.path.join(BASE_DIR, 'yt-dlp.exe')
FFMPEG   = os.path.join(BASE_DIR, 'ffmpeg.exe')
COOKIES  = os.path.join(BASE_DIR, 'cookies.txt')
OUT_DIR  = os.path.join(BASE_DIR, 'downloads', 'broll')

os.makedirs(OUT_DIR, exist_ok=True)

SEARCH_QUERIES = [
    "gym broll shorts no text",
    "fitness cinematic broll shorts",
    "workout broll vertical shorts",
    "dark moody gym shorts cinematic",
]

TARGET_PER_QUERY = 5
SEARCH_POOL      = 20   # 每组搜索候选数量（多搜一些，筛出足够的）
MIN_DUR          = 15
MAX_DUR          = 25


def run():
    for query in SEARCH_QUERIES:
        safe_name = query.replace(' ', '_').replace('/', '_')[:40]
        out_dir   = os.path.join(OUT_DIR, safe_name)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\n{'='*55}")
        print(f"🔍 搜索：{query}")
        print(f"📁 输出：{out_dir}")
        print(f"{'='*55}")

        cmd = [
            YTDLP,
            '--match-filter', f'duration >= {MIN_DUR} & duration <= {MAX_DUR}',
            '--max-downloads', str(TARGET_PER_QUERY),
            '--no-playlist',
            '--js-runtimes', 'node',
            '--ffmpeg-location', FFMPEG,
            '-f', 'bestvideo/best',          # 纯视频，无音轨
            '--postprocessor-args', 'ffmpeg:-an',  # 确保去掉音频
            '--merge-output-format', 'mp4',
            '--newline',
            '-o', os.path.join(out_dir, '%(title).60s.%(ext)s'),
            f'ytsearch{SEARCH_POOL}:{query}',
        ]

        if os.path.isfile(COOKIES):
            cmd += ['--cookies', COOKIES]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        for line in proc.stdout:
            print(line, end='')
        proc.wait()

        # 统计结果
        files = [f for f in os.listdir(out_dir) if f.endswith('.mp4')]
        print(f"\n✅ 完成：{len(files)} 个文件 → {out_dir}")

    print(f"\n🎉 全部完成！素材目录：{OUT_DIR}")


if __name__ == '__main__':
    run()

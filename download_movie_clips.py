"""
download_movie_clips.py
下载经典电影名场面片段：励志、爱情、伤感、自由主题
"""

import subprocess
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YTDLP   = os.path.join(BASE_DIR, 'yt-dlp.exe')
FFMPEG  = os.path.join(BASE_DIR, 'ffmpeg.exe')
COOKIES = os.path.join(BASE_DIR, 'cookies.txt')
OUT_DIR = os.path.join(BASE_DIR, 'downloads', 'movie_clips')

os.makedirs(OUT_DIR, exist_ok=True)

# 每条：(主题分类, 搜索关键词, 候选数量)
QUERIES = [
    # ── 觉醒与反抗 ──
    ("觉醒反抗", "truman show good morning good night final scene", 5),
    ("觉醒反抗", "truman show ending door scene", 5),

    # ── 孤独与坚守 ──
    ("孤独坚守", "a better tomorrow chow yun fat iconic scene", 5),
    ("孤独坚守", "英雄本色 小马哥 经典台词", 5),

    # ── 宿命与抉择 ──
    ("宿命抉择", "matrix red pill blue pill scene morpheus neo", 5),
    ("宿命抉择", "matrix rabbit hole scene", 5),

    # ── 尊严与抗争 ──
    ("尊严抗争", "braveheart freedom scene mel gibson", 5),
    ("尊严抗争", "braveheart final scene william wallace", 5),

    # ── 遗憾与释怀 ──
    ("遗憾释怀", "the grandmaster 一代宗师 gong er final scene", 5),
    ("遗憾释怀", "一代宗师 宫二 经典片段", 5),

    # ── 额外补充：经典励志/爱情/伤感 ──
    ("励志",     "rocky training montage original", 5),
    ("励志",     "pursuit of happyness crying bathroom scene", 5),
    ("爱情",     "eternal sunshine of spotless mind ending scene", 5),
    ("伤感",     "forrest gump jenny death scene", 5),
    ("自由",     "dead poets society o captain my captain scene", 5),
    ("自由",     "shawshank redemption rain scene andy free", 5),
]

TARGET_PER_QUERY = 2   # 每个关键词取前2个结果


def run():
    total_downloaded = 0

    for theme, query, pool in QUERIES:
        safe = query.replace(' ', '_').replace('/', '_')[:50]
        out_dir = os.path.join(OUT_DIR, theme)
        os.makedirs(out_dir, exist_ok=True)

        print(f"\n{'='*55}")
        print(f"🎬 [{theme}] {query}")
        print(f"{'='*55}")

        cmd = [
            YTDLP,
            '--max-downloads', str(TARGET_PER_QUERY),
            '--no-playlist',
            '--js-runtimes', 'node',
            '--ffmpeg-location', FFMPEG,
            '-f', 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            '--merge-output-format', 'mp4',
            '--newline',
            '-o', os.path.join(out_dir, f'%(title).60s.%(ext)s'),
            f'ytsearch{pool}:{query}',
        ]

        if os.path.isfile(COOKIES):
            cmd += ['--cookies', COOKIES]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        for line in proc.stdout:
            line = line.rstrip()
            # 只打印关键行，过滤掉 API/下载进度噪音
            if any(k in line for k in ['Downloading item', 'Destination', 'has already', '✓', 'ERROR', 'finished', '[download] 100%']):
                print(line)
        proc.wait()

        files = [f for f in os.listdir(out_dir) if f.endswith('.mp4')]
        total_downloaded = sum(
            len([f for f in os.listdir(os.path.join(OUT_DIR, d)) if f.endswith('.mp4')])
            for d in os.listdir(OUT_DIR)
            if os.path.isdir(os.path.join(OUT_DIR, d))
        )
        print(f"✅ [{theme}] 当前目录共 {len(files)} 个文件")

    print(f"\n🎉 全部完成！共下载 {total_downloaded} 个片段")
    print(f"📁 {OUT_DIR}")


if __name__ == '__main__':
    run()

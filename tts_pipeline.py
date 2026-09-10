"""
tts_pipeline.py
视频创作流水线：
  1. 用 edge-tts 把中英文文案分段生成情绪语音
  2. 按语音时长自动生成双语 SRT 字幕
  3. 去除原视频音轨
  4. 随机选一首 BGM，按视频时长截取 + 淡出
  5. ffmpeg 合成：静音视频 + BGM(压低) + 语音 + 字幕烧录
"""

import os
import re
import random
import asyncio
import tempfile
import shutil
import subprocess
from datetime import datetime


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def find_ffmpeg():
    import glob, shutil as sh
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg.exe')
    if os.path.isfile(local):
        return local
    sys_ff = sh.which('ffmpeg')
    if sys_ff:
        return sys_ff
    patterns = [
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe'),
    ]
    for p in patterns:
        matches = glob.glob(p, recursive=True)
        if matches:
            return matches[0]
    return 'ffmpeg'


def fmt_time_srt(s: float) -> str:
    """秒数 → SRT 时间戳 00:00:00,000"""
    ms = int(round((s % 1) * 1000))
    h  = int(s) // 3600
    m  = (int(s) % 3600) // 60
    sec = int(s) % 60
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def get_audio_duration(path: str) -> float:
    """用 ffprobe 获取音频/视频时长（秒）"""
    ffmpeg = find_ffmpeg()
    ffprobe = ffmpeg.replace('ffmpeg', 'ffprobe')
    if not os.path.isfile(ffprobe):
        ffprobe = 'ffprobe'
    result = subprocess.run(
        [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def get_video_duration(path: str) -> float:
    return get_audio_duration(path)


# ── TTS 语音生成 ───────────────────────────────────────────────────────────────

VOICE_STYLES = {
    'energetic':   ('en-US-AndrewMultilingualNeural', 'excited'),
    'calm':        ('en-US-AriaNeural',               'calm'),
    'empathetic':  ('en-US-AriaNeural',               'empathetic'),
    'motivational':('en-US-GuyNeural',                'excited'),
    'serious':     ('en-US-GuyNeural',                'newscast-formal'),
}


async def _tts_segment(text_en: str, voice: str, style: str, out_path: str):
    """用 edge-tts 生成单段语音，支持 style（情绪）"""
    import edge_tts
    if style:
        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="http://www.w3.org/2001/mstts" xml:lang="en-US">'
            f'<voice name="{voice}">'
            f'<mstts:express-as style="{style}">{text_en}</mstts:express-as>'
            f'</voice></speak>'
        )
        communicate = edge_tts.Communicate(ssml, voice, ssml=True)
    else:
        communicate = edge_tts.Communicate(text_en, voice)
    await communicate.save(out_path)


def generate_tts_segments(segments: list, voice_style: str, work_dir: str,
                           log_fn=None) -> list:
    """
    segments: [{"en": "...", "zh": "..."}, ...]
    返回: [{"en": ..., "zh": ..., "audio": path, "duration": float}, ...]
    """
    try:
        import edge_tts  # noqa
    except ImportError:
        raise ImportError('请安装 edge-tts：pip install edge-tts')

    voice, style = VOICE_STYLES.get(voice_style, VOICE_STYLES['energetic'])
    results = []

    for i, seg in enumerate(segments):
        text_en = seg.get('en', '').strip()
        text_zh = seg.get('zh', '').strip()
        if not text_en:
            continue

        out_path = os.path.join(work_dir, f'seg_{i:03d}.mp3')
        if log_fn:
            log_fn(f'  🎙 第{i+1}段 TTS: {text_en[:40]}...' if len(text_en) > 40 else f'  🎙 第{i+1}段 TTS: {text_en}')

        asyncio.run(_tts_segment(text_en, voice, style, out_path))
        dur = get_audio_duration(out_path)

        results.append({
            'en': text_en,
            'zh': text_zh,
            'audio': out_path,
            'duration': dur,
        })

    return results


# ── SRT 字幕生成 ───────────────────────────────────────────────────────────────

def build_bilingual_srt(segments_with_time: list, srt_path: str):
    """
    segments_with_time: [{"en": ..., "zh": ..., "start": float, "end": float}, ...]
    生成双语 SRT（英文在上，中文在下）
    """
    with open(srt_path, 'w', encoding='utf-8') as f:
        for i, seg in enumerate(segments_with_time, 1):
            start = fmt_time_srt(seg['start'])
            end   = fmt_time_srt(seg['end'])
            en    = seg.get('en', '').strip()
            zh    = seg.get('zh', '').strip()
            text  = en
            if zh:
                text = f"{en}\n{zh}"
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")


# ── 合并分段音频 ───────────────────────────────────────────────────────────────

def concat_audio_segments(seg_audios: list, out_path: str, log_fn=None):
    """把多段 mp3 拼接成一整轨 wav"""
    ffmpeg = find_ffmpeg()
    tmp_list = out_path + '_list.txt'
    with open(tmp_list, 'w', encoding='utf-8') as f:
        for p in seg_audios:
            f.write(f"file '{p.replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n")
    cmd = [ffmpeg, '-f', 'concat', '-safe', '0', '-i', tmp_list,
           '-c:a', 'pcm_s16le', '-y', out_path]
    subprocess.run(cmd, capture_output=True)
    try:
        os.remove(tmp_list)
    except Exception:
        pass


# ── BGM 处理 ──────────────────────────────────────────────────────────────────

def prepare_bgm(bgm_paths: list, target_duration: float, out_path: str,
                volume: float = 0.15, log_fn=None):
    """
    随机选一首 BGM，截取 target_duration 秒，最后 3 秒淡出，降音量
    如果 BGM 比目标短则循环
    """
    ffmpeg = find_ffmpeg()
    chosen = random.choice(bgm_paths)
    if log_fn:
        log_fn(f'  🎵 BGM: {os.path.basename(chosen)}')

    fade_start = max(0, target_duration - 3)
    cmd = [
        ffmpeg,
        '-stream_loop', '-1',        # 循环（短 BGM 用）
        '-i', chosen,
        '-t', str(target_duration),  # 截取到目标时长
        '-af', f'afade=t=out:st={fade_start:.2f}:d=3,volume={volume}',
        '-c:a', 'aac', '-b:a', '128k',
        '-y', out_path
    ]
    subprocess.run(cmd, capture_output=True)


# ── 主合成函数 ────────────────────────────────────────────────────────────────

def run_creator_pipeline(task: dict, video_path: str, bgm_paths: list,
                         segments: list, voice_style: str,
                         out_dir: str, log_fn=None):
    """
    task: 用于写入状态的字典（step / status / error / output_file）
    segments: [{"en": "...", "zh": "..."}, ...]
    """
    import traceback

    def log(msg):
        if log_fn:
            log_fn(msg)

    def fail(msg):
        task['status'] = 'error'
        task['error']  = msg
        log('❌ ' + msg)

    ffmpeg = find_ffmpeg()
    work_dir = tempfile.mkdtemp(prefix='creator_')

    try:
        # ── Step 1: TTS 生成各段语音 ──────────────────────────────────────────
        task['step'] = 1
        log('=' * 48)
        log('▶ 第一步：TTS 生成配音')
        tts_segs = generate_tts_segments(segments, voice_style, work_dir, log_fn=log)
        if not tts_segs:
            fail('没有可处理的文案段落')
            return
        log(f'✅ 共生成 {len(tts_segs)} 段语音')

        # ── Step 2: 计算时间轴，生成双语 SRT ──────────────────────────────────
        task['step'] = 2
        log('')
        log('=' * 48)
        log('▶ 第二步：生成双语字幕')

        # 段落之间加 0.3 秒间隔
        GAP = 0.3
        cursor = 0.0
        segs_with_time = []
        for seg in tts_segs:
            start = cursor
            end   = cursor + seg['duration']
            segs_with_time.append({
                'en': seg['en'], 'zh': seg['zh'],
                'start': start, 'end': end,
                'audio': seg['audio'],
            })
            cursor = end + GAP

        total_voice_dur = cursor  # 所有语音 + 间隔的总时长

        srt_path = os.path.join(work_dir, 'bilingual.srt')
        build_bilingual_srt(segs_with_time, srt_path)
        log(f'✅ 字幕文件生成：{len(segs_with_time)} 段双语字幕')

        # ── Step 3: 拼接语音轨 ────────────────────────────────────────────────
        task['step'] = 3
        log('')
        log('=' * 48)
        log('▶ 第三步：拼接语音轨道')

        voice_concat = os.path.join(work_dir, 'voice_concat.wav')
        concat_audio_segments([s['audio'] for s in segs_with_time],
                               voice_concat, log_fn=log)
        log('✅ 语音轨拼接完成')

        # ── Step 4: 准备 BGM ──────────────────────────────────────────────────
        task['step'] = 4
        log('')
        log('=' * 48)
        log('▶ 第四步：截取背景音乐')

        # 视频时长与语音时长取较大值（保证字幕能看完）
        video_dur = get_video_duration(video_path)
        target_dur = max(video_dur, total_voice_dur) + 1.0  # 多留 1 秒

        bgm_out = os.path.join(work_dir, 'bgm_trimmed.aac')
        prepare_bgm(bgm_paths, target_dur, bgm_out, volume=0.15, log_fn=log)
        log(f'✅ BGM 截取完成（{target_dur:.1f}s）')

        # ── Step 5: ffmpeg 合成 ───────────────────────────────────────────────
        task['step'] = 5
        log('')
        log('=' * 48)
        log('▶ 第五步：合成视频（去原声 + BGM + 配音 + 字幕烧录）')

        # 复制 SRT 到临时目录（路径不含特殊字符）
        tmp_srt = os.path.join(tempfile.gettempdir(), 'creator_sub.srt')
        shutil.copy2(srt_path, tmp_srt)
        srt_esc = tmp_srt.replace('\\', '/').replace(':', '\\:')

        basename = os.path.splitext(os.path.basename(video_path))[0]
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_file = os.path.join(out_dir, f'{basename}_创作_{ts}.mp4')

        # 混音：BGM + 语音 amix，语音保持 100% 音量
        cmd = [
            ffmpeg,
            '-i', video_path,       # 0: 背景视频
            '-i', bgm_out,           # 1: BGM
            '-i', voice_concat,      # 2: 语音
            '-filter_complex',
            '[1:a][2:a]amix=inputs=2:duration=first:dropout_transition=2[aout]',
            '-map', '0:v',
            '-map', '[aout]',
            '-vf', f"subtitles='{srt_esc}'",
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '192k',
            '-shortest',
            '-y', out_file
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        for line in proc.stdout:
            line = line.rstrip()
            if 'frame=' in line or 'error' in line.lower():
                log(line)
        proc.wait()

        try:
            os.remove(tmp_srt)
        except Exception:
            pass

        if proc.returncode != 0 or not os.path.isfile(out_file):
            fail('ffmpeg 合成失败')
            return

        size_mb = os.path.getsize(out_file) / 1024 / 1024
        log(f'✅ 合成完成: {os.path.basename(out_file)} ({size_mb:.1f} MB)')

        # ── 完成 ──────────────────────────────────────────────────────────────
        task['step'] = 6
        task['status'] = 'done'
        task['output_file'] = out_file
        log('')
        log('🎉 全部完成！')
        log(f'📁 {out_file}')

    except Exception as e:
        fail(str(e) + '\n' + traceback.format_exc())
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

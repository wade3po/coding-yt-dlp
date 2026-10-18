"""
gen_text_video.py  v7
- body_scenes: list[list[str]]  每个 scene 是一个画面，内含多行文字
- 逗号切割 = 同一画面多行，不同文案 = 不同画面
- MAXW 缩小（左右60px边距），字符超出自动缩字号
- seg_alpha / 时长按行数动态计算
"""

import os
import re
import json
import subprocess
import tempfile
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── 目录 ──────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
TEXTVIDEO = os.path.join(BASE_DIR, "video_source")

# ── 画面参数 ──────────────────────────────────────────────────
W, H, FPS = 1920, 1080, 30
MARGIN    = 60                    # 左右各60px边距
MAXW      = W - MARGIN * 2       # 1800px

TITLE_SIZE  = 130
BODY_SIZE   = 118
LINE_GAP    = 1.52

FONT_SPEC    = (r"C:\Windows\Fonts\NotoSerifSC-VF.ttf", [900])
TITLE_TRACK  = 0.05
BODY_TRACK   = 0.05

TEXT_RGB        = (252, 252, 248)
SHADOW_STRENGTH = 0.55
SHADOW_BLUR     = 22

# ── 节奏（每行额外时间） ──────────────────────────────────────
LINE_FADE    = 0.85
LINE_STAGGER = 1.05    # 每多一行额外错开 1.05s
LINE_RISE    = 12
SENT_FADE_OUT = 0.90
HOLD_AFTER   = 2.00

TITLE_FADE     = 1.00
TITLE_FADE_OUT = 0.75
TITLE_HOLD     = 1.80
TITLE_DUR      = TITLE_FADE + TITLE_HOLD + TITLE_FADE_OUT   # 3.55s


def _scene_dur(n_lines):
    """n 行画面的总时长"""
    return (n_lines - 1) * LINE_STAGGER + LINE_FADE + HOLD_AFTER + SENT_FADE_OUT


FALLBACK_FONTS = (
    (r"C:\Windows\Fonts\msyhbd.ttc", None),
    (r"C:\Windows\Fonts\simhei.ttf", None),
)


# ── 工具 ──────────────────────────────────────────────────────

def _strip_punct(text):
    """去标点，保留汉字/英文/数字"""
    return re.sub(
        r'[，。！？、；：\u2018\u2019\u201c\u201d「」【】…—～,.!?;:\'"《》（）()\[\]\u3000\s]+',
        '', text
    ).strip()


def _split_by_comma(text):
    """按中英文逗号切割为行列表，每行去标点"""
    parts = re.split(r'[,，]', text)
    return [_strip_punct(p) for p in parts if _strip_punct(p)]


def load_font(size, spec=None):
    for path, axes in ([spec] if spec else []) + list(FALLBACK_FONTS):
        try:
            f = ImageFont.truetype(path, size)
            if axes is not None:
                try:
                    f.set_variation_by_axes(axes)
                except Exception:
                    pass
            return f, path
        except Exception:
            continue
    raise RuntimeError("找不到可用的中文字体")


def _measure(text, font, trk):
    """实测宽度：逐字累加 + 字间距，与 draw_line 完全一致"""
    if not text:
        return 0
    if trk <= 0 or len(text) < 2:
        return font.getlength(text)
    return sum(font.getlength(c) for c in text) + trk * (len(text) - 1)


def _draw_line(draw, x, y, text, font, trk):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=255)
        x += font.getlength(ch) + trk


def build_scene(lines_input, max_size, spec, trk_ratio, log_fn=None):
    """
    把一组行文字渲染为 mask+shadow 列表（一个画面的所有行）。
    自动缩字号直到所有行都在 MAXW 内。
    """
    size = max_size
    while size > 48:
        font, font_path = load_font(size, spec)
        trk = trk_ratio * size
        widths = [_measure(ln, font, trk) for ln in lines_input]
        if max(widths) <= MAXW:
            break
        size -= 2

    font, font_path = load_font(size, spec)
    trk    = trk_ratio * size
    line_h = int(size * LINE_GAP)
    n      = len(lines_input)
    y0     = (H - line_h * n) // 2

    result = []
    for i, ln in enumerate(lines_input):
        w  = _measure(ln, font, trk)
        x0 = max(MARGIN, (W - w) / 2)       # 绝不超出左边距
        mask = Image.new("L", (W, H), 0)
        _draw_line(ImageDraw.Draw(mask), x0, y0 + i * line_h, ln, font, trk)
        result.append({
            "mask":   np.asarray(mask, dtype=np.float32) / 255.0,
            "shadow": np.asarray(
                mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)),
                dtype=np.float32,
            ) / 255.0,
        })

    if log_fn:
        log_fn(f"   {n}行 / 字号{size} / {font_path.split(os.sep)[-1]} : {' | '.join(lines_input)}")
    return result


def seg_alpha(kind, t, n_lines, scene_dur):
    """
    返回 [(line_idx, alpha), ...] 供 render_frame 使用。
    scene_dur: 该画面的总时长（按行数动态计算）
    """
    if kind == "title":
        a_in  = min(1.0, t / TITLE_FADE)
        a_out = 1.0 if t <= TITLE_FADE + TITLE_HOLD else \
                max(0.0, (TITLE_DUR - t) / TITLE_FADE_OUT)
        a = a_in * a_out
        sm = a * a * (3 - 2 * a)
        return [(0, sm)]

    out_start = scene_dur - SENT_FADE_OUT
    a_out = 1.0 if t <= out_start else max(0.0, (scene_dur - t) / SENT_FADE_OUT)
    res = []
    for i in range(n_lines):
        t0   = i * LINE_STAGGER
        a_in = 0.0 if t < t0 else min(1.0, (t - t0) / LINE_FADE)
        a    = a_in * a_out
        res.append((i, a * a * (3 - 2 * a)))
    return res


def render_frame(bg_img, lines_meta, kind, t, scene_dur):
    n = len(lines_meta)
    arr = None
    for i, a in seg_alpha(kind, t, n, scene_dur):
        if i >= n or a < 0.002:
            continue
        info = lines_meta[i]
        rise = int(LINE_RISE * (1 - a))
        ta   = np.roll(info["mask"],   rise, axis=0)
        sh   = np.roll(info["shadow"], rise, axis=0)
        if rise > 0:
            ta[:rise] = 0; sh[:rise] = 0
        if arr is None:
            arr = np.asarray(bg_img).astype(np.float32)
        arr -= (sh * SHADOW_STRENGTH * a)[..., None] * arr
        arr  = arr * (1 - (ta * a)[..., None]) + \
               np.array(TEXT_RGB, np.float32) * (ta * a)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)) if arr is not None else bg_img


# ══════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════

def generate_video(title, body_scenes, bg_video, bg_music, music_start,
                   out_dir, log_fn=print, ffmpeg_bin=None):
    """
    title        : str
    body_scenes  : list[list[str]]  每个元素是一个画面的行列表
                   例：[["春风得意马蹄疾", "一日看尽长安花"],
                        ["欲买桂花同载酒", "终不似少年游"]]
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = "ffmpeg"

    clean_title = _strip_punct(title) or "无标题"
    clean_scenes = []
    for scene_lines in body_scenes:
        lines = [_strip_punct(l) for l in scene_lines if _strip_punct(l)]
        if lines:
            clean_scenes.append(lines)

    if not clean_scenes:
        raise ValueError("文案内容为空")

    log_fn(f"标题: {clean_title}")
    log_fn(f"共 {len(clean_scenes)} 个画面")

    # ── 构建画面序列：(kind, lines_list, duration) ─────────────
    segs = [("title", [clean_title], TITLE_DUR, TITLE_SIZE, TITLE_TRACK)]
    for lines in clean_scenes:
        dur = _scene_dur(len(lines))
        segs.append(("body", lines, dur, BODY_SIZE, BODY_TRACK))

    # ── 预渲染 ────────────────────────────────────────────────
    log_fn("预渲染字体...")
    metas = []
    for kind, lines, dur, sz, trk in segs:
        metas.append(build_scene(lines, sz, FONT_SPEC, trk, log_fn))

    durations = [s[2] for s in segs]
    per_seg   = [int(round(d * FPS)) for d in durations]
    total_frames  = sum(per_seg)
    total_seconds = total_frames / FPS
    starts        = np.cumsum([0] + per_seg)

    log_fn(f"总时长: {total_seconds:.2f}s ({total_frames} 帧)")

    os.makedirs(out_dir, exist_ok=True)
    safe_name   = re.sub(r'[^\w\u4e00-\u9fff]', '_', clean_title)[:20]
    out_nomusic = os.path.join(out_dir, f"{safe_name}_视频.mp4")
    out_final   = os.path.join(out_dir, f"{safe_name}_最终.mp4")

    # ── 逐帧渲染 ──────────────────────────────────────────────
    log_fn("开始渲染帧...")
    dec = subprocess.Popen(
        [ffmpeg_bin, "-v", "error", "-i", bg_video,
         "-t", str(total_seconds),
         "-vf", f"fps={FPS},scale={W}:{H}:flags=lanczos",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    enc = subprocess.Popen(
        [ffmpeg_bin, "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-crf", "18", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
         out_nomusic],
        stdin=subprocess.PIPE,
    )

    frame_bytes = W * H * 3
    for k in range(total_frames):
        raw = dec.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            raw += b"\x00" * (frame_bytes - len(raw))
        bg_img = Image.frombytes("RGB", (W, H), raw)
        idx    = int(np.searchsorted(starts, k, side="right") - 1)
        t_     = (k - starts[idx]) / FPS
        frame  = render_frame(bg_img, metas[idx], segs[idx][0], t_, durations[idx])
        enc.stdin.write(frame.tobytes())
        if k % 90 == 0:
            pct = int(k / total_frames * 80)
            log_fn(f"渲染 {k}/{total_frames} 帧  ({k/FPS:.1f}s)  [{pct}%]")

    dec.stdout.close(); dec.wait()
    enc.stdin.close();  enc.wait()
    log_fn("视频帧渲染完成")

    # ── 背景音乐 ──────────────────────────────────────────────
    if not bg_music or not os.path.isfile(bg_music):
        log_fn("⚠️ 未选择背景音乐，跳过")
        log_fn(f"✅ 输出: {out_nomusic}")
        return out_nomusic

    log_fn("处理背景音乐...")
    FADE_IN, FADE_OUT = 1.2, 1.8
    tmp_dir = tempfile.mkdtemp()
    try:
        clip_wav = os.path.join(tmp_dir, "clip.wav")
        mix_wav  = os.path.join(tmp_dir, "mix.wav")

        subprocess.run([
            ffmpeg_bin, "-y", "-v", "error",
            "-ss", str(music_start), "-t", str(total_seconds),
            "-i", bg_music, "-ac", "2", "-ar", "48000",
            "-c:a", "pcm_s16le", clip_wav
        ], check=True)

        p = subprocess.run([
            ffmpeg_bin, "-v", "info", "-i", clip_wav,
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-"
        ], capture_output=True)
        stdout = p.stdout.decode('utf-8', errors='ignore') if p.stdout else ''
        stderr = p.stderr.decode('utf-8', errors='ignore') if p.stderr else ''
        m = re.search(r"\{[^{}]*\}", stdout + stderr, re.S)
        if m:
            gain = -14.0 - float(json.loads(m.group(0))["input_i"])
            log_fn(f"音频增益: {gain:+.1f} dB")
        else:
            gain = 0.0
            log_fn("⚠️ 响度分析失败，使用默认增益")

        filt = (f"volume={gain:.2f}dB,"
                f"afade=t=in:st=0:d={FADE_IN}:curve=qsin,"
                f"afade=t=out:st={total_seconds - FADE_OUT}:d={FADE_OUT}:curve=qsin,"
                f"alimiter=limit=0.891:level=disabled")
        subprocess.run([
            ffmpeg_bin, "-y", "-v", "error",
            "-i", clip_wav, "-af", filt,
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", mix_wav
        ], check=True)

        subprocess.run([
            ffmpeg_bin, "-y", "-v", "error",
            "-i", out_nomusic, "-i", mix_wav,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
            "-ar", "48000", "-movflags", "+faststart", "-shortest",
            out_final
        ], check=True)

        if os.path.exists(out_nomusic):
            os.remove(out_nomusic)
        log_fn(f"✅ 完成（含音乐）-> {out_final}")
        return out_final

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── 命令行 ────────────────────────────────────────────────────

def main():
    title = "第一次读就很震撼的诗词"
    # 每个列表 = 一个画面，逗号前后是同一画面的两行
    scripts = [
        "春风得意马蹄疾，一日看尽长安花",
        "欲买桂花同载酒，终不似少年游",
        "须知少时凌云志，曾许人间第一流",
    ]

    body_scenes = [_split_by_comma(s) for s in scripts if _split_by_comma(s)]

    generate_video(
        title=title,
        body_scenes=body_scenes,
        bg_video=os.path.join(TEXTVIDEO, "bg_ink_long.mp4"),
        bg_music=os.path.join(TEXTVIDEO, "bgm_piano.mp3"),
        music_start=148.9,
        out_dir=TEXTVIDEO,
        log_fn=print,
        ffmpeg_bin=os.path.join(BASE_DIR, "ffmpeg.exe"),
    )


if __name__ == "__main__":
    main()

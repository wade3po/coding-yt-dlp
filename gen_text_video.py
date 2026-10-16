"""
gen_text_video.py  v5
移植自 deepseek-harness 验证过的实现
核心：numpy 逐帧合成 + smoothstep 缓动 + 高斯阴影 + NotoSerifSC 大字号
"""

import subprocess
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE_DIR  = "c:\\Users\\yms1\\Desktop\\wade\\coding-yt-dlp"
TEXTVIDEO = BASE_DIR + "\\textvideo"
BG        = TEXTVIDEO + "\\1minbg3.mp4"
OUT       = TEXTVIDEO + "\\output.mp4"

W, H, FPS = 1920, 1080, 30
MAXW = W - 260

TITLE_SIZE  = 138
BODY_SIZE   = 132
LINE_GAP    = 1.46

FONT_SPEC       = (r"C:\Windows\Fonts\NotoSerifSC-VF.ttf", [900])
TITLE_TRACK     = 0.06
BODY_TRACK      = 0.06

TEXT_RGB         = (252, 252, 248)   # 近纯白，极微冷，去掉LED感
SHADOW_STRENGTH  = 0.55
SHADOW_BLUR      = 24

# ── 节奏 ──────────────────────────────────────────────────────
LINE_FADE       = 0.90
LINE_STAGGER    = 1.10
LINE_RISE       = 12
SENT_FADE_OUT   = 0.90
HOLD_AFTER      = 2.10
TITLE_FADE      = 1.00
TITLE_FADE_OUT  = 0.75
TITLE_HOLD      = 1.75   # 原0.75，加了1秒停留

SENT_DUR  = LINE_STAGGER + LINE_FADE + HOLD_AFTER + SENT_FADE_OUT  # 5.0s
TITLE_DUR = TITLE_FADE + TITLE_HOLD + TITLE_FADE_OUT               # 2.5s

# ── 内容 ──────────────────────────────────────────────────────
TITLE = "第一次读就很震撼的诗词"
BODY  = [
    "春风得意马蹄疾一日看尽长安花",
    "欲买桂花同载酒终不似少年游",
    "须知少时凌云志曾许人间第一流",
]
HARD_BREAK = {t: 7 for t in BODY}

FALLBACK_FONTS = (
    (r"C:\Windows\Fonts\msyhbd.ttc", None),
    (r"C:\Windows\Fonts\simhei.ttf", None),
)


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


def text_width(text, font, trk):
    if trk <= 0 or len(text) < 2:
        return font.getlength(text)
    return sum(font.getlength(c) for c in text) + trk * (len(text) - 1)


def draw_line(draw, xy, text, font, fill, trk):
    x, y = xy
    if trk <= 0:
        draw.text((x, y), text, font=font, fill=fill)
        return
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + trk


def layout(text, size, two_lines, spec, trk_ratio):
    def split_two(t):
        cut = HARD_BREAK.get(t)
        if cut is None or not (0 < cut < len(t)):
            cut = len(t) // 2
        return [t[:cut], t[cut:]]

    font, _ = load_font(size, spec)
    lines = split_two(text) if two_lines else [text]
    while size > 60 and max(text_width(ln, font, trk_ratio * size) for ln in lines) > MAXW:
        size -= 2
        font, _ = load_font(size, spec)
    return lines, size


def build_lines(text, size, two_lines, spec, trk_ratio):
    lines, size = layout(text, size, two_lines, spec, trk_ratio)
    font, font_path = load_font(size, spec)
    trk  = trk_ratio * size
    line_h = int(size * LINE_GAP)
    y0 = (H - line_h * len(lines)) // 2

    result = []
    for i, ln in enumerate(lines):
        mask = Image.new("L", (W, H), 0)
        draw_line(
            ImageDraw.Draw(mask),
            ((W - text_width(ln, font, trk)) / 2, y0 + i * line_h),
            ln, font, 255, trk,
        )
        result.append({
            "mask":   np.asarray(mask, dtype=np.float32) / 255.0,
            "shadow": np.asarray(
                mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)),
                dtype=np.float32,
            ) / 255.0,
        })
    print(f"   {len(lines)}行 / 字号{size} / 字距{trk:.1f}px / "
          f"{font_path.split(chr(92))[-1]} : {text}")
    return result


def seg_alpha(kind, t):
    if kind == "title":
        a_in  = min(1.0, t / TITLE_FADE)
        a_out = 1.0 if t <= TITLE_FADE + TITLE_HOLD else \
                max(0.0, (TITLE_DUR - t) / TITLE_FADE_OUT)
        a = min(a_in, a_out)
        return [(0, a * a * (3 - 2 * a))]

    out_start = SENT_DUR - SENT_FADE_OUT
    a_out = 1.0 if t <= out_start else max(0.0, (SENT_DUR - t) / SENT_FADE_OUT)
    res = []
    for i in range(2):
        start = i * LINE_STAGGER
        a_in  = 0.0 if t < start else min(1.0, (t - start) / LINE_FADE)
        a     = min(a_in, a_out)
        res.append((i, a * a * (3 - 2 * a)))
    return res


def render_frame(bg, lines_meta, kind, t):
    arr = None
    for i, a in seg_alpha(kind, t):
        if a <= 0.002:
            continue
        info = lines_meta[i]
        rise = int(LINE_RISE * (1 - a))
        ta   = np.roll(info["mask"],   rise, axis=0)
        sh   = np.roll(info["shadow"], rise, axis=0)
        if rise:
            ta[:rise] = 0
            sh[:rise] = 0
        if arr is None:
            arr = np.asarray(bg).astype(np.float32)
        arr = arr * (1 - (sh * SHADOW_STRENGTH * a)[..., None])
        arr = arr * (1 - (ta * a)[..., None]) + \
              np.array(TEXT_RGB, np.float32) * (ta * a)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)) if arr is not None else bg


def main():
    segs = (
        [("title", TITLE, TITLE_DUR, TITLE_SIZE, False, TITLE_TRACK)] +
        [("body",  t,     SENT_DUR,  BODY_SIZE,  True,  BODY_TRACK) for t in BODY]
    )
    metas = [build_lines(t, s, two, FONT_SPEC, trk) for _, t, _, s, two, trk in segs]
    per_seg = [int(round(d * FPS)) for _, _, d, _, _, _ in segs]
    total_frames  = sum(per_seg)
    total_seconds = total_frames / FPS
    starts = np.cumsum([0] + per_seg)

    print(f"   标题{TITLE_DUR:.2f}s + 正文{SENT_DUR:.2f}s × {len(BODY)} = "
          f"{total_seconds:.2f}s（{total_frames}帧）")

    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", BG,
         "-t", f"{total_seconds}",
         "-vf", f"fps={FPS},scale={W}:{H}:flags=lanczos",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE,
    )
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-crf", "18", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an",
         OUT],
        stdin=subprocess.PIPE,
    )

    frame_bytes = W * H * 3
    for k in range(total_frames):
        raw = dec.stdout.read(frame_bytes)
        if len(raw) < frame_bytes:
            raw += b"\x00" * (frame_bytes - len(raw))
        bg  = Image.frombytes("RGB", (W, H), raw)
        idx = int(np.searchsorted(starts, k, side="right") - 1)
        t   = (k - starts[idx]) / FPS
        enc.stdin.write(render_frame(bg, metas[idx], segs[idx][0], t).tobytes())
        if k % 90 == 0:
            print(f"   帧 {k}/{total_frames}  ({k/FPS:.1f}s)", flush=True)

    dec.stdout.close(); dec.wait()
    enc.stdin.close();  enc.wait()
    print(f"视频生成完成 -> {OUT}")

    # ── 加音乐 ───────────────────────────────────────────────
    print("处理背景音乐...")
    import json, re, os, tempfile
    MUSIC_START = 148.9   # g.mp3 从 148.9s 开始截
    FADE_IN     = 1.2
    FADE_OUT    = 1.8
    BGM         = TEXTVIDEO + "\\g.mp3"
    OUT_MUSIC   = TEXTVIDEO + "\\output_music.mp4"
    tmp_dir     = tempfile.mkdtemp()
    clip_wav    = os.path.join(tmp_dir, "clip.wav")
    mix_wav     = os.path.join(tmp_dir, "mix.wav")

    # 截取对应时长
    subprocess.run(["ffmpeg", "-y", "-v", "error",
        "-ss", str(MUSIC_START), "-t", str(total_seconds),
        "-i", BGM, "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", clip_wav],
        check=True)

    # 测响度
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", clip_wav,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\}", p.stdout + p.stderr, re.S)
    loud = json.loads(m.group(0))
    in_i = float(loud["input_i"])
    gain = -14.0 - in_i
    print(f"   响度 {in_i:.1f} LUFS → 增益 {gain:+.1f} dB")

    # 归一 + 淡入淡出
    filt = (f"volume={gain:.2f}dB,"
            f"afade=t=in:st=0:d={FADE_IN}:curve=qsin,"
            f"afade=t=out:st={total_seconds - FADE_OUT}:d={FADE_OUT}:curve=qsin,"
            f"alimiter=limit=0.891:level=disabled")
    subprocess.run(["ffmpeg", "-y", "-v", "error",
        "-i", clip_wav, "-af", filt,
        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", mix_wav], check=True)

    # 合流（视频 copy，不重编码）
    subprocess.run(["ffmpeg", "-y", "-v", "error",
        "-i", OUT, "-i", mix_wav,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest", OUT_MUSIC], check=True)

    # 清理
    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"✅ 完成（含音乐）-> {OUT_MUSIC}")


if __name__ == "__main__":
    main()

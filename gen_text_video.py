"""
gen_text_video.py  v8
新增：
  1. 快捷输入 QUICK_INPUT —— 直接粘贴整段文案，自动解析标题/句子/来源
  2. 来源标注 —— 每句右下角显示 ——《越人歌》，无来源则不显示
"""

import os, re, json, subprocess, tempfile, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ── 目录 ──────────────────────────────────────────────────────
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
VS        = os.path.join(BASE_DIR, "video_source")   # 素材目录

# ── 画面参数 ──────────────────────────────────────────────────
W, H, FPS = 1920, 1080, 30
MARGIN    = 60
MAXW      = W - MARGIN * 2

TITLE_SIZE   = 130
BODY_SIZE    = 118
SOURCE_SIZE  = 46    # 来源字号
LINE_GAP     = 1.52

FONT_SPEC   = (r"C:\Windows\Fonts\NotoSerifSC-VF.ttf", [900])
TITLE_TRACK = 0.05
BODY_TRACK  = 0.05

TEXT_RGB        = (252, 252, 248)
SOURCE_OPACITY  = 0.72            # 来源文字稍暗
SHADOW_STRENGTH = 0.55
SHADOW_BLUR     = 22

# ── 节奏 ──────────────────────────────────────────────────────
LINE_FADE     = 0.85
LINE_STAGGER  = 1.05
LINE_RISE     = 12
SENT_FADE_OUT = 0.90
HOLD_AFTER    = 2.00

TITLE_FADE     = 1.00
TITLE_FADE_OUT = 0.75
TITLE_HOLD     = 1.80
TITLE_DUR      = TITLE_FADE + TITLE_HOLD + TITLE_FADE_OUT

SOURCE_PAD_R = 80   # 来源距右边距
SOURCE_PAD_B = 70   # 来源距下边距

FALLBACK_FONTS = (
    (r"C:\Windows\Fonts\msyhbd.ttc", None),
    (r"C:\Windows\Fonts\simhei.ttf", None),
)

# ══════════════════════════════════════════════════════════════
#  快捷输入 —— 直接粘贴整段文案，留空则使用 main() 里的手动配置
#
#  格式：
#    第一行 = 标题
#    空行分隔
#    正文每行 = 句子（可含标点）  [空格]（来源）
#    来源写在行末括号里，没有就不写
#
# ══════════════════════════════════════════════════════════════
QUICK_INPUT = """
古人是怎么说"我想你"的

山有木兮木有枝，心悦君兮君不知。　（《越人歌》）

晓看天色暮看云，行也思君，坐也思君。　（明·唐寅《一剪梅》）

玲珑骰子安红豆，入骨相思知不知。　（唐·温庭筠《南歌子词》）

早知如此绊人心，何如当初莫相识。　（唐·李白《秋风词》）
"""

# ══════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════

def _strip_punct(text):
    return re.sub(
        r'[，。！？、；：\u2018\u2019\u201c\u201d「」【】…—～,.!?;:\'"《》（）()\[\]\u3000\s]+',
        '', text
    ).strip()

def _split_by_comma(text):
    parts = re.split(r'[,，]', text)
    return [_strip_punct(p) for p in parts if _strip_punct(p)]

def load_font(size, spec=None):
    for path, axes in ([spec] if spec else []) + list(FALLBACK_FONTS):
        try:
            f = ImageFont.truetype(path, size)
            if axes is not None:
                try: f.set_variation_by_axes(axes)
                except: pass
            return f, path
        except: continue
    raise RuntimeError("找不到可用的中文字体")

def _measure(text, font, trk):
    if not text: return 0
    if trk <= 0 or len(text) < 2: return font.getlength(text)
    return sum(font.getlength(c) for c in text) + trk * (len(text) - 1)

def _draw_line(draw, x, y, text, font, trk):
    for ch in text:
        draw.text((x, y), ch, font=font, fill=255)
        x += font.getlength(ch) + trk

def _scene_dur(n_lines):
    return (n_lines - 1) * LINE_STAGGER + LINE_FADE + HOLD_AFTER + SENT_FADE_OUT

# ══════════════════════════════════════════════════════════════
#  快捷输入解析
# ══════════════════════════════════════════════════════════════

def parse_quick_input(raw):
    """
    解析 QUICK_INPUT 格式，返回 (title, scenes)
    scenes = [{"lines": [str,...], "source": str}, ...]
    """
    lines = [l.strip() for l in raw.strip().splitlines()]
    # 过滤空行，得到段落（连续非空行合并）
    paras = []
    buf = []
    for l in lines:
        if l:
            buf.append(l)
        else:
            if buf:
                paras.append(' '.join(buf))
                buf = []
    if buf:
        paras.append(' '.join(buf))

    if not paras:
        return None, []

    title  = paras[0]
    scenes = []

    for para in paras[1:]:
        # 提取来源：行末括号内
        src_match = re.search(r'[（(]([^）)]+)[）)]\s*$', para)
        source = ''
        if src_match:
            source = src_match.group(1).strip()
            para   = para[:src_match.start()].strip()

        # 按逗号分行（去标点）
        scene_lines = _split_by_comma(para)
        if not scene_lines:
            continue

        # 如果分出来只有1行（无逗号），尝试从中间切
        if len(scene_lines) == 1 and len(scene_lines[0]) >= 10:
            t = scene_lines[0]
            mid = len(t) // 2
            scene_lines = [t[:mid], t[mid:]]

        scenes.append({"lines": scene_lines, "source": source})

    return title, scenes

# ══════════════════════════════════════════════════════════════
#  预渲染
# ══════════════════════════════════════════════════════════════

def build_scene(lines_input, max_size, spec, trk_ratio, source='', log_fn=None):
    """渲染一个画面的所有行 mask，以及可选的来源 mask"""
    size = max_size
    while size > 48:
        font, _ = load_font(size, spec)
        trk = trk_ratio * size
        if max(_measure(ln, font, trk) for ln in lines_input) <= MAXW:
            break
        size -= 2

    font, font_path = load_font(size, spec)
    trk    = trk_ratio * size
    line_h = int(size * LINE_GAP)
    n      = len(lines_input)
    y0     = (H - line_h * n) // 2

    result_lines = []
    for i, ln in enumerate(lines_input):
        w  = _measure(ln, font, trk)
        x0 = max(MARGIN, (W - w) / 2)
        mask = Image.new("L", (W, H), 0)
        _draw_line(ImageDraw.Draw(mask), x0, y0 + i * line_h, ln, font, trk)
        result_lines.append({
            "mask":   np.asarray(mask, dtype=np.float32) / 255.0,
            "shadow": np.asarray(mask.filter(ImageFilter.GaussianBlur(SHADOW_BLUR)),
                                 dtype=np.float32) / 255.0,
        })

    if log_fn:
        src_tag = f"  [{source}]" if source else ""
        log_fn(f"   {n}行 / 字号{size} / {font_path.split(os.sep)[-1]} : "
               f"{' | '.join(lines_input)}{src_tag}")

    # ── 来源 mask（右下角）────────────────────────────────────
    source_data = None
    if source:
        src_font, _ = load_font(SOURCE_SIZE, spec)
        src_text    = f"——{source}"
        src_w       = _measure(src_text, src_font, 0)
        src_h       = SOURCE_SIZE
        x_src = W - src_w - SOURCE_PAD_R
        y_src = H - src_h - SOURCE_PAD_B

        src_mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(src_mask).text((x_src, y_src), src_text, font=src_font, fill=255)
        source_data = {
            "mask":   np.asarray(src_mask, dtype=np.float32) / 255.0,
            "shadow": np.asarray(src_mask.filter(ImageFilter.GaussianBlur(14)),
                                 dtype=np.float32) / 255.0,
        }

    return {"lines": result_lines, "source": source_data}

def seg_alpha(kind, t, n_lines, scene_dur):
    if kind == "title":
        a_in  = min(1.0, t / TITLE_FADE)
        a_out = 1.0 if t <= TITLE_FADE + TITLE_HOLD else \
                max(0.0, (TITLE_DUR - t) / TITLE_FADE_OUT)
        a = a_in * a_out
        return [(0, a * a * (3 - 2 * a))]

    out_start = scene_dur - SENT_FADE_OUT
    a_out     = 1.0 if t <= out_start else max(0.0, (scene_dur - t) / SENT_FADE_OUT)
    res = []
    for i in range(n_lines):
        t0   = i * LINE_STAGGER
        a_in = 0.0 if t < t0 else min(1.0, (t - t0) / LINE_FADE)
        a    = a_in * a_out
        res.append((i, a * a * (3 - 2 * a)))
    return res

def render_frame(bg_img, meta, kind, t, scene_dur):
    """meta = {"lines": [...], "source": {...} or None}"""
    lines_meta = meta["lines"]
    n          = len(lines_meta)
    arr        = None

    alphas = seg_alpha(kind, t, n, scene_dur)

    for i, a in alphas:
        if i >= n or a < 0.002: continue
        info = lines_meta[i]
        rise = int(LINE_RISE * (1 - a))
        ta   = np.roll(info["mask"],   rise, axis=0)
        sh   = np.roll(info["shadow"], rise, axis=0)
        if rise > 0: ta[:rise] = 0; sh[:rise] = 0
        if arr is None:
            arr = np.asarray(bg_img).astype(np.float32)
        arr -= (sh * SHADOW_STRENGTH * a)[..., None] * arr
        arr  = arr * (1 - (ta * a)[..., None]) + \
               np.array(TEXT_RGB, np.float32) * (ta * a)[..., None]

    # ── 来源：跟随最后一行的 alpha ──────────────────────────
    src_data = meta.get("source")
    if src_data is not None and alphas:
        a_src = alphas[-1][1]   # 最后一行的 alpha
        if a_src >= 0.002:
            if arr is None:
                arr = np.asarray(bg_img).astype(np.float32)
            a_eff = a_src * SOURCE_OPACITY
            rise  = int(LINE_RISE * (1 - a_src))
            ta    = np.roll(src_data["mask"],   rise, axis=0)
            sh    = np.roll(src_data["shadow"], rise, axis=0)
            if rise > 0: ta[:rise] = 0; sh[:rise] = 0
            arr -= (sh * SHADOW_STRENGTH * a_eff)[..., None] * arr
            arr  = arr * (1 - (ta * a_eff)[..., None]) + \
                   np.array(TEXT_RGB, np.float32) * (ta * a_eff)[..., None]

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)) if arr is not None else bg_img

# ══════════════════════════════════════════════════════════════
#  主渲染函数
# ══════════════════════════════════════════════════════════════

def generate_video(title, body_scenes, bg_video, bg_music, music_start,
                   out_dir, log_fn=print, ffmpeg_bin=None):
    """
    body_scenes: list of dict {"lines": [str,...], "source": str}
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = "ffmpeg"

    clean_title = _strip_punct(title) or "无标题"

    log_fn(f"标题: {clean_title}")
    log_fn(f"共 {len(body_scenes)} 个画面")

    segs = [("title", [clean_title], TITLE_DUR, TITLE_SIZE, TITLE_TRACK, '')]
    for sc in body_scenes:
        lines  = [_strip_punct(l) for l in sc["lines"] if _strip_punct(l)]
        source = sc.get("source", "")
        if not lines: continue
        dur = _scene_dur(len(lines))
        segs.append(("body", lines, dur, BODY_SIZE, BODY_TRACK, source))

    log_fn("预渲染字体...")
    metas = []
    for kind, lines, dur, sz, trk, src in segs:
        metas.append(build_scene(lines, sz, FONT_SPEC, trk, source=src, log_fn=log_fn))

    durations     = [s[2] for s in segs]
    per_seg       = [int(round(d * FPS)) for d in durations]
    total_frames  = sum(per_seg)
    total_seconds = total_frames / FPS
    starts        = np.cumsum([0] + per_seg)

    log_fn(f"总时长: {total_seconds:.2f}s ({total_frames} 帧)")

    os.makedirs(out_dir, exist_ok=True)
    safe_name   = re.sub(r'[^\w\u4e00-\u9fff]', '_', clean_title)[:20]
    out_nomusic = os.path.join(out_dir, f"{safe_name}_视频.mp4")
    out_final   = os.path.join(out_dir, f"{safe_name}_最终.mp4")

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
            log_fn(f"渲染 {k}/{total_frames} ({k/FPS:.1f}s)")

    dec.stdout.close(); dec.wait()
    enc.stdin.close();  enc.wait()

    # ── 加音乐 ────────────────────────────────────────────────
    if not bg_music or not os.path.isfile(bg_music):
        log_fn(f"✅ 输出: {out_nomusic}")
        return out_nomusic

    log_fn("处理背景音乐...")
    FADE_IN, FADE_OUT = 1.2, 1.8
    tmp_dir = tempfile.mkdtemp()
    try:
        clip_wav = os.path.join(tmp_dir, "clip.wav")
        mix_wav  = os.path.join(tmp_dir, "mix.wav")

        subprocess.run([ffmpeg_bin, "-y", "-v", "error",
            "-ss", str(music_start), "-t", str(total_seconds),
            "-i", bg_music, "-ac", "2", "-ar", "48000",
            "-c:a", "pcm_s16le", clip_wav], check=True)

        p = subprocess.run([ffmpeg_bin, "-v", "info", "-i", clip_wav,
            "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
            "-f", "null", "-"], capture_output=True)
        merged = (p.stdout or b'').decode('utf-8','ignore') + \
                 (p.stderr or b'').decode('utf-8','ignore')
        m = re.search(r"\{[^{}]*\}", merged, re.S)
        gain = (-14.0 - float(json.loads(m.group(0))["input_i"])) if m else 0.0
        log_fn(f"音频增益: {gain:+.1f} dB")

        filt = (f"volume={gain:.2f}dB,"
                f"afade=t=in:st=0:d={FADE_IN}:curve=qsin,"
                f"afade=t=out:st={total_seconds-FADE_OUT}:d={FADE_OUT}:curve=qsin,"
                f"alimiter=limit=0.891:level=disabled")
        subprocess.run([ffmpeg_bin, "-y", "-v", "error",
            "-i", clip_wav, "-af", filt,
            "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", mix_wav], check=True)

        subprocess.run([ffmpeg_bin, "-y", "-v", "error",
            "-i", out_nomusic, "-i", mix_wav,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "256k",
            "-ar", "48000", "-movflags", "+faststart", "-shortest",
            out_final], check=True)

        if os.path.exists(out_nomusic):
            os.remove(out_nomusic)
        log_fn(f"✅ 完成 -> {out_final}")
        return out_final
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

# ══════════════════════════════════════════════════════════════
#  命令行入口
# ══════════════════════════════════════════════════════════════

def main():
    # ── 优先使用 QUICK_INPUT ──────────────────────────────────
    if QUICK_INPUT.strip():
        title, body_scenes = parse_quick_input(QUICK_INPUT)
        if not title or not body_scenes:
            print("QUICK_INPUT 解析失败，请检查格式")
            return
        print(f"[快捷输入] 标题: {title}，{len(body_scenes)} 句")
    else:
        # ── 手动配置（QUICK_INPUT 留空时使用）────────────────
        title = "第一次读就很震撼的诗词"
        body_scenes = [
            {"lines": _split_by_comma("春风得意马蹄疾，一日看尽长安花"), "source": ""},
            {"lines": _split_by_comma("欲买桂花同载酒，终不似少年游"),   "source": ""},
            {"lines": _split_by_comma("须知少时凌云志，曾许人间第一流"), "source": ""},
        ]

    generate_video(
        title       = title,
        body_scenes = body_scenes,
        bg_video    = os.path.join(VS, "bg_ink_long.mp4"),
        bg_music    = os.path.join(VS, "bgm_piano.mp3"),
        music_start = 148.9,
        out_dir     = VS,
        log_fn      = print,
        ffmpeg_bin  = os.path.join(BASE_DIR, "ffmpeg.exe"),
    )


if __name__ == "__main__":
    main()

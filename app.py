import subprocess
import os
import json
import threading
import uuid
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context

app = Flask(__name__, static_folder='static')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
YT_DLP = os.path.join(BASE_DIR, 'yt-dlp.exe')
FFMPEG = os.path.join(BASE_DIR, 'ffmpeg.exe')
DOWNLOAD_DIR = os.path.join(BASE_DIR, 'downloads')
COOKIES_FILE = os.path.join(BASE_DIR, 'cookies.txt')

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Base args: always use node as JS runtime, and point to local ffmpeg
BASE_ARGS = ['--js-runtimes', 'node']

def find_ffmpeg():
    """查找 ffmpeg：先找同目录，再找 PATH，再找 winget 安装目录"""
    # 1. 同目录
    local = os.path.join(BASE_DIR, 'ffmpeg.exe')
    if os.path.isfile(local):
        return local
    # 2. 系统 PATH
    import shutil
    sys_ffmpeg = shutil.which('ffmpeg')
    if sys_ffmpeg:
        return sys_ffmpeg
    # 3. winget 常见安装路径
    import glob
    patterns = [
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\ffmpeg.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe'),
    ]
    for p in patterns:
        matches = glob.glob(p, recursive=True)
        if matches:
            return matches[0]
    return None

def get_base_args():
    args = list(BASE_ARGS)
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        args += ['--ffmpeg-location', ffmpeg]
    if os.path.isfile(COOKIES_FILE):
        args += ['--cookies', COOKIES_FILE]
    return args

# Store active tasks
tasks = {}


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/info', methods=['POST'])
def get_info():
    data = request.json
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': '请输入有效的 URL'}), 400

    try:
        base = get_base_args()
        result = subprocess.run(
            [YT_DLP] + base + ['--dump-json', '--no-playlist', url],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace'
        )
        if result.returncode != 0:
            # Try with playlist
            result = subprocess.run(
                [YT_DLP] + base + ['--dump-json', '--flat-playlist', url],
                capture_output=True, text=True, timeout=30,
                encoding='utf-8', errors='replace'
            )

        if result.stdout:
            # May have multiple JSON lines (playlist)
            lines = [l for l in result.stdout.strip().split('\n') if l.strip()]
            videos = []
            for line in lines:
                try:
                    videos.append(json.loads(line))
                except:
                    pass
            if videos:
                return jsonify({'videos': videos})

        err = result.stderr or result.stdout or '无法获取视频信息'
        return jsonify({'error': err}), 400

    except subprocess.TimeoutExpired:
        return jsonify({'error': '请求超时，请检查 URL 是否有效'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/formats', methods=['POST'])
def get_formats():
    data = request.json
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': '请输入有效的 URL'}), 400

    try:
        base = get_base_args()
        result = subprocess.run(
            [YT_DLP] + base + ['-J', '--no-playlist', url],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace'
        )
        if result.returncode == 0 and result.stdout:
            info = json.loads(result.stdout)
            formats = info.get('formats', [])
            # Filter and simplify formats
            simplified = []
            for f in formats:
                simplified.append({
                    'format_id': f.get('format_id'),
                    'ext': f.get('ext'),
                    'resolution': f.get('resolution') or f.get('format_note', ''),
                    'fps': f.get('fps'),
                    'vcodec': f.get('vcodec', 'none'),
                    'acodec': f.get('acodec', 'none'),
                    'filesize': f.get('filesize') or f.get('filesize_approx'),
                    'tbr': f.get('tbr'),
                    'format_note': f.get('format_note', ''),
                })
            return jsonify({
                'formats': simplified,
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
            })
        return jsonify({'error': result.stderr or result.stdout or '无法获取格式信息'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cookies-status', methods=['GET'])
def cookies_status():
    exists = os.path.isfile(COOKIES_FILE)
    return jsonify({'exists': exists, 'path': COOKIES_FILE})


@app.route('/api/cookies-from-browser', methods=['POST'])
def cookies_from_browser():
    data = request.json
    browser = data.get('browser', 'chrome')
    try:
        result = subprocess.run(
            [YT_DLP, '--cookies-from-browser', browser,
             '--cookies', COOKIES_FILE,
             '-J', '--no-playlist', 'https://www.youtube.com'],
            capture_output=True, text=True, timeout=30,
            encoding='utf-8', errors='replace'
        )
        if os.path.isfile(COOKIES_FILE):
            return jsonify({'ok': True, 'msg': f'已从 {browser} 导出 cookies'})
        return jsonify({'ok': False, 'msg': result.stderr or '导出失败'}), 400
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)}), 500


@app.route('/api/upload-cookies', methods=['POST'])
def upload_cookies():
    f = request.files.get('file')
    if not f:
        return jsonify({'ok': False, 'msg': '未上传文件'}), 400
    f.save(COOKIES_FILE)
    return jsonify({'ok': True, 'msg': 'cookies.txt 已保存'})


@app.route('/api/download', methods=['POST'])
def start_download():
    data = request.json
    url = data.get('url', '').strip()
    format_id = data.get('format_id', 'bestvideo+bestaudio/best')
    output_name = data.get('output', '%(title)s.%(ext)s')
    extra_args = data.get('extra_args', [])

    if not url:
        return jsonify({'error': '请输入有效的 URL'}), 400

    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        'status': 'running',
        'output': [],
        'progress': 0,
        'speed': '',
        'eta': '',
        'filename': '',
    }

    def run_download():
        cmd = [
            YT_DLP,
        ] + get_base_args() + [
            '-f', format_id,
            '-o', os.path.join(DOWNLOAD_DIR, output_name),
            '--newline',
            '--progress',
        ] + extra_args + [url]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1
        )
        tasks[task_id]['pid'] = proc.pid

        for line in proc.stdout:
            line = line.rstrip()
            tasks[task_id]['output'].append(line)
            # Parse progress
            if '[download]' in line and '%' in line:
                try:
                    parts = line.split()
                    for i, p in enumerate(parts):
                        if '%' in p:
                            pct = float(p.replace('%', ''))
                            tasks[task_id]['progress'] = pct
                        if p == 'at':
                            tasks[task_id]['speed'] = parts[i+1] if i+1 < len(parts) else ''
                        if p == 'ETA':
                            tasks[task_id]['eta'] = parts[i+1] if i+1 < len(parts) else ''
                except:
                    pass
            if 'Destination:' in line or 'has already been downloaded' in line:
                tasks[task_id]['filename'] = line

        proc.wait()
        tasks[task_id]['status'] = 'done' if proc.returncode == 0 else 'error'
        tasks[task_id]['progress'] = 100 if proc.returncode == 0 else tasks[task_id]['progress']

    t = threading.Thread(target=run_download, daemon=True)
    t.start()

    return jsonify({'task_id': task_id})


@app.route('/api/task/<task_id>', methods=['GET'])
def get_task(task_id):
    task = tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    # Only send last 50 lines to keep response small
    result = dict(task)
    result['output'] = task['output'][-50:]
    return jsonify(result)


@app.route('/api/downloads', methods=['GET'])
def list_downloads():
    files = []
    for f in os.listdir(DOWNLOAD_DIR):
        fp = os.path.join(DOWNLOAD_DIR, f)
        if os.path.isfile(fp):
            files.append({
                'name': f,
                'size': os.path.getsize(fp),
                'mtime': os.path.getmtime(fp),
            })
    files.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify({'files': files})


@app.route('/downloads/<path:filename>')
def serve_download(filename):
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


@app.route('/api/open-folder', methods=['POST'])
def open_folder():
    import subprocess as sp
    sp.Popen(f'explorer "{DOWNLOAD_DIR}"')
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# 自动流水线 API
# ─────────────────────────────────────────────
pipeline_tasks = {}

def run_pipeline(task_id, url):
    import re
    from datetime import datetime

    task = pipeline_tasks[task_id]

    def log(msg):
        task['logs'].append(msg)

    def fail(msg):
        task['status'] = 'error'
        task['error'] = msg
        log('❌ ' + msg)

    try:
        # 今天的输出目录
        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = os.path.join(DOWNLOAD_DIR, today)
        os.makedirs(out_dir, exist_ok=True)
        task['output_dir'] = out_dir

        # ── 第一步：下载 ──────────────────────────────
        task['step'] = 1
        log('=' * 45)
        log('▶ 第一步：下载视频')
        log(f'链接: {url}')
        log(f'保存目录: {out_dir}')

        cmd = [
            YT_DLP,
            '--js-runtimes', 'node',
            '--ffmpeg-location', FFMPEG,
            '-f', 'bestvideo+bestaudio/best',
            '-o', os.path.join(out_dir, '%(title)s.%(ext)s'),
            '--newline', '--no-playlist',
        ]
        if os.path.isfile(COOKIES_FILE):
            cmd += ['--cookies', COOKIES_FILE]
        cmd.append(url)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace')
        downloaded_file = None
        for line in proc.stdout:
            line = line.rstrip()
            log(line)
            m = re.search(r'\[ffmpeg\] Merging formats into "(.+)"', line)
            if m:
                downloaded_file = m.group(1).strip()
            m2 = re.search(r'\[download\] Destination: (.+)', line)
            if m2 and not downloaded_file:
                downloaded_file = m2.group(1).strip()
        proc.wait()

        if proc.returncode != 0:
            fail('下载失败，请检查链接或 Cookies')
            return

        # 没捕获到就找最新视频文件
        if not downloaded_file or not os.path.isfile(downloaded_file):
            video_exts = ('.webm', '.mp4', '.mkv', '.mov', '.avi')
            files = sorted(
                [os.path.join(out_dir, f) for f in os.listdir(out_dir)],
                key=os.path.getmtime, reverse=True
            )
            for f in files:
                if any(f.endswith(e) for e in video_exts) and '_字幕' not in f:
                    downloaded_file = f
                    break

        if not downloaded_file:
            fail('找不到下载的视频文件')
            return

        log(f'✅ 下载完成: {os.path.basename(downloaded_file)}')

        # ── 第二步：Whisper 识别 + 翻译 ─────────────────
        task['step'] = 2
        log('')
        log('=' * 45)
        log('▶ 第二步：语音识别 + 中文翻译')

        try:
            import whisper
        except ImportError:
            fail('未安装 whisper，请运行: pip install openai-whisper')
            return

        log('加载 Whisper base 模型...')
        model = whisper.load_model("base")
        log('识别中，请稍候...')
        result = model.transcribe(downloaded_file, task="translate", language="en", verbose=False)
        segments = result["segments"]
        log(f'识别完成，共 {len(segments)} 段字幕')

        try:
            from deep_translator import GoogleTranslator
            log('翻译成中文...')
            translator = GoogleTranslator(source="en", target="zh-CN")
            for seg in segments:
                try:
                    seg["text"] = translator.translate(seg["text"].strip())
                except Exception:
                    pass
            log('✅ 中文翻译完成')
        except ImportError:
            log('⚠️ 未安装 deep-translator，保留英文字幕')

        # 生成 SRT
        def fmt_time(s):
            ms = int((s % 1) * 1000)
            return f"{int(s)//3600:02d}:{int(s)//60%60:02d}:{int(s)%60:02d},{ms:03d}"

        base_name = os.path.splitext(downloaded_file)[0]
        srt_path = base_name + "_zh.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                f.write(f"{i}\n{fmt_time(seg['start'])} --> {fmt_time(seg['end'])}\n{seg['text'].strip()}\n\n")
        log(f'✅ 字幕文件: {os.path.basename(srt_path)}')

        # ── 第三步：烧录字幕 ──────────────────────────────
        task['step'] = 3
        log('')
        log('=' * 45)
        log('▶ 第三步：烧录字幕到视频')

        output_file = base_name + "_字幕.mp4"
        srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")

        cmd2 = [
            FFMPEG,
            "-i", downloaded_file,
            "-vf", (
                f"subtitles='{srt_escaped}':"
                "force_style='FontName=Arial,FontSize=14,"
                "PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2'"
            ),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "128k",
            "-y", output_file
        ]

        proc2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace')
        for line in proc2.stdout:
            line = line.rstrip()
            if 'frame=' in line or 'error' in line.lower():
                log(line)
        proc2.wait()

        if proc2.returncode != 0:
            fail('字幕烧录失败')
            return

        size_mb = os.path.getsize(output_file) / 1024 / 1024
        log(f'✅ 烧录完成: {os.path.basename(output_file)} ({size_mb:.1f} MB)')

        # ── 清理中间文件 ──────────────────────────────────
        for tmp in [downloaded_file, srt_path]:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
                    log(f'🗑 已删除: {os.path.basename(tmp)}')
            except Exception:
                pass

        # ── 完成 ─────────────────────────────────────────
        task['step'] = 4
        task['status'] = 'done'
        task['output_file'] = output_file
        log('')
        log('🎉 全部完成！')
        log(f'📁 目录: {out_dir}')
        log(f'🎬 视频: {output_file}')

    except Exception as e:
        fail(str(e))


@app.route('/api/pipeline/start', methods=['POST'])
def pipeline_start():
    data = request.json
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': '请输入有效的 URL'}), 400

    task_id = str(uuid.uuid4())
    pipeline_tasks[task_id] = {
        'status': 'running',
        'step': 0,
        'logs': [],
        'output_dir': '',
        'output_file': '',
        'error': '',
    }
    t = threading.Thread(target=run_pipeline, args=(task_id, url), daemon=True)
    t.start()
    return jsonify({'task_id': task_id})


@app.route('/api/pipeline/task/<task_id>', methods=['GET'])
def pipeline_task(task_id):
    task = pipeline_tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify({
        'status': task['status'],
        'step': task['step'],
        'logs': task['logs'],
        'output_dir': task['output_dir'],
        'output_file': task['output_file'],
        'error': task['error'],
    })


# ─────────────────────────────────────────────
# 去水印 API
# ─────────────────────────────────────────────
dewatermark_tasks = {}

def detect_watermark(video_path, sample_count=30):
    """通过帧差分自动检测静止水印区域，返回 (x, y, w, h) 或 None"""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total < 10:
        cap.release()
        return None

    frames = []
    step = max(1, total // sample_count)
    for i in range(0, min(total, sample_count * step), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            frames.append(frame.astype(np.float32))
    cap.release()

    if len(frames) < 5:
        return None

    # 计算每个像素的标准差，低标准差 = 静止区域 = 水印
    stack = np.stack(frames, axis=0)
    std_map = np.std(stack, axis=0)
    # 转成灰度
    std_gray = np.mean(std_map, axis=2)

    # 二值化：标准差低于阈值的区域
    threshold = np.percentile(std_gray, 15)
    mask = (std_gray < threshold).astype(np.uint8) * 255

    # 腐蚀膨胀去噪
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 20))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    # 找最大连通区域（水印通常是一整块）
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # 过滤掉太大（整个画面）和太小的区域
    h_total, w_total = std_gray.shape
    valid = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        total_area = w_total * h_total
        if 0.001 * total_area < area < 0.25 * total_area:
            valid.append((x, y, w, h, area))

    if not valid:
        return None

    # 取面积最大的
    valid.sort(key=lambda v: v[4], reverse=True)
    x, y, w, h, _ = valid[0]

    # 稍微扩展边界
    pad = 10
    x = max(0, x - pad)
    y = max(0, y - pad)
    w = min(w_total - x, w + pad * 2)
    h = min(h_total - y, h + pad * 2)

    return x, y, w, h


def run_dewatermark(task_id, video_path, method, x, y, w, h):
    import cv2
    import numpy as np
    import shutil

    task = dewatermark_tasks[task_id]

    def log(msg):
        task['logs'].append(msg)

    try:
        log(f'📂 输入文件: {os.path.basename(video_path)}')

        # 如果坐标全为0，自动检测
        if x == y == w == h == 0:
            log('🔍 自动检测水印位置...')
            result = detect_watermark(video_path)
            if result is None:
                task['status'] = 'error'
                task['error'] = '未能自动检测到水印，请手动输入坐标'
                log('❌ 检测失败，请手动输入坐标')
                return
            x, y, w, h = result
            log(f'✅ 检测到水印: x={x}, y={y}, w={w}, h={h}')
        else:
            log(f'📍 使用手动坐标: x={x}, y={y}, w={w}, h={h}')

        task['watermark'] = {'x': x, 'y': y, 'w': w, 'h': h}

        cap = cv2.VideoCapture(video_path)
        fps   = cap.get(cv2.CAP_PROP_FPS)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log(f'📹 视频信息: {fw}x{fh}, {fps:.1f}fps, 共{total}帧')

        base        = os.path.splitext(video_path)[0]
        frames_dir  = os.path.join(BASE_DIR, '_tmp_frames')
        output_path = base + '_去水印.mp4'
        os.makedirs(frames_dir, exist_ok=True)

        mask = np.zeros((fh, fw), dtype=np.uint8)
        mask[y:y+h, x:x+w] = 255

        cv_method   = cv2.INPAINT_TELEA if method == 'telea' else cv2.INPAINT_NS
        method_name = 'Telea' if method == 'telea' else 'Navier-Stokes'
        log(f'🔧 算法: {method_name}，开始逐帧处理...')

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            fixed = cv2.inpaint(frame, mask, inpaintRadius=7, flags=cv_method)
            cv2.imwrite(os.path.join(frames_dir, f'f{frame_idx:06d}.png'), fixed)
            frame_idx += 1
            if frame_idx % 50 == 0:
                pct = int(frame_idx / total * 90)
                task['progress'] = pct
                log(f'  处理帧: {frame_idx}/{total} ({pct}%)')

        cap.release()
        log(f'✅ 帧处理完成，共 {frame_idx} 帧，开始合成视频...')

        # FFmpeg 合成
        cmd = [
            FFMPEG, '-y',
            '-framerate', str(fps),
            '-i', os.path.join(frames_dir, 'f%06d.png'),
            '-i', video_path,
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '18',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-map', '0:v:0',
            '-map', '1:a:0?',
            '-shortest',
            output_path
        ]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace')

        shutil.rmtree(frames_dir, ignore_errors=True)

        if r.returncode != 0:
            task['status'] = 'error'
            task['error'] = 'FFmpeg 合成失败'
            log(f'❌ FFmpeg 失败: {r.stderr[-300:]}')
            return

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        log(f'🎉 完成！{os.path.basename(output_path)} ({size_mb:.1f} MB)')
        task['status']      = 'done'
        task['output_file'] = output_path
        task['progress']    = 100

    except Exception as e:
        import traceback
        task['status'] = 'error'
        task['error']  = str(e)
        task['logs'].append(f'❌ 错误: {traceback.format_exc()}')


@app.route('/api/dewatermark/preview', methods=['POST'])
def dewatermark_preview():
    """提取第一帧并标出检测到的水印区域，返回 base64 图片"""
    import cv2, numpy as np, base64
    data = request.json
    filename = data.get('filename', '').strip()
    if os.path.isabs(filename):
        video_path = filename
    else:
        video_path = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.isfile(video_path):
        return jsonify({'error': '文件不存在'}), 400

    cap = cv2.VideoCapture(video_path)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return jsonify({'error': '无法读取视频帧'}), 400

    result = detect_watermark(video_path)
    wm = None
    if result:
        x, y, w, h = result
        wm = {'x': x, 'y': y, 'w': w, 'h': h}
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 0, 255), 4)
        cv2.putText(frame, f'Watermark? ({x},{y},{w},{h})',
                    (x, max(y-10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0,0,255), 3)

    # 缩小到宽度540预览
    h_orig, w_orig = frame.shape[:2]
    scale = 540 / w_orig
    preview = cv2.resize(frame, (540, int(h_orig * scale)))
    _, buf = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
    img_b64 = base64.b64encode(buf).decode('utf-8')

    # 把 watermark 坐标也按同样比例缩放，让前端直接在预览图坐标系下画框
    wm_preview = None
    if result:
        x, y, w, h = result
        wm_preview = {
            'x': round(x * scale), 'y': round(y * scale),
            'w': round(w * scale), 'h': round(h * scale)
        }

    return jsonify({
        'image': img_b64,
        'watermark': wm_preview,          # 预览图坐标系
        'watermark_orig': {'x':result[0],'y':result[1],'w':result[2],'h':result[3]} if result else None,
        'scale': scale,                    # 缩放比例，前端换算用
        'preview_w': 540,
        'preview_h': int(h_orig * scale),
    })


@app.route('/api/dewatermark/start', methods=['POST'])
def dewatermark_start():
    data = request.json
    filename = data.get('filename', '').strip()
    method   = data.get('method', 'telea')
    x = int(data.get('x', 0))
    y = int(data.get('y', 0))
    w = int(data.get('w', 0))
    h = int(data.get('h', 0))

    if not filename:
        return jsonify({'error': '请选择文件'}), 400

    # 支持直接传完整路径或文件名
    if os.path.isabs(filename):
        video_path = filename
    else:
        video_path = os.path.join(DOWNLOAD_DIR, filename)

    if not os.path.isfile(video_path):
        return jsonify({'error': f'文件不存在: {video_path}'}), 400

    task_id = str(uuid.uuid4())
    dewatermark_tasks[task_id] = {
        'status': 'running', 'progress': 0,
        'logs': [], 'output_file': '', 'error': '',
        'watermark': None,
    }
    t = threading.Thread(target=run_dewatermark,
                         args=(task_id, video_path, method, x, y, w, h), daemon=True)
    t.start()
    return jsonify({'task_id': task_id})


@app.route('/api/dewatermark/task/<task_id>', methods=['GET'])
def dewatermark_task(task_id):
    task = dewatermark_tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(task)


@app.route('/api/dewatermark/files', methods=['GET'])
def dewatermark_files():
    """递归列出 downloads 下所有视频文件"""
    video_exts = ('.mp4', '.webm', '.mkv', '.mov', '.avi')
    result = []
    for root, dirs, files in os.walk(DOWNLOAD_DIR):
        for f in files:
            if any(f.lower().endswith(e) for e in video_exts):
                full = os.path.join(root, f)
                rel  = os.path.relpath(full, DOWNLOAD_DIR)
                result.append({'name': f, 'rel': rel, 'full': full,
                                'size': os.path.getsize(full)})
    result.sort(key=lambda x: x['name'])
    return jsonify({'files': result})


# ─────────────────────────────────────────────
# 自动抓取 API
# ─────────────────────────────────────────────
crawl_tasks = {}

def has_watermark(video_path, sample=8, threshold=0.12):
    """
    快速检测视频是否有水印
    取少量帧，计算低标准差（静止）区域占比
    超过阈值说明有固定水印
    """
    import cv2, numpy as np
    try:
        cap = cv2.VideoCapture(video_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        frames = []
        step = max(1, total // sample)
        for i in range(0, min(total, sample * step), step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, f = cap.read()
            if ret:
                frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32))
        cap.release()
        if len(frames) < 4:
            return False
        stack = np.stack(frames, axis=0)
        std_map = np.std(stack, axis=0)
        # 静止像素占比
        static_ratio = (std_map < 5).sum() / (fw * fh)
        return static_ratio > threshold
    except:
        return False


def run_crawl(task_id, keyword, count, auto_pipeline):
    from datetime import datetime
    import re

    task = crawl_tasks[task_id]

    def log(msg):
        task['logs'].append(msg)

    try:
        log(f'🔍 搜索关键词: {keyword}')
        log(f'目标数量: {count} 个无水印视频')

        # yt-dlp 搜索
        search_query = f'ytsearch{count * 3}:{keyword} motivational short'
        cmd = [YT_DLP] + get_base_args() + [
            '--dump-json', '--flat-playlist',
            '--no-playlist',
            search_query
        ]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=60)

        videos = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
                url = info.get('url') or info.get('webpage_url', '')
                if url and not url.startswith('http'):
                    url = 'https://www.youtube.com/watch?v=' + url
                title = info.get('title', '未知标题')
                duration = info.get('duration', 0)
                # 只要短视频（10秒~5分钟）
                if duration and (duration < 10 or duration > 300):
                    continue
                if url:
                    videos.append({'url': url, 'title': title, 'duration': duration})
            except:
                continue

        log(f'找到 {len(videos)} 个候选视频')

        if not videos:
            task['status'] = 'error'
            task['error'] = '未找到相关视频'
            return

        today = datetime.now().strftime("%Y-%m-%d")
        out_dir = os.path.join(DOWNLOAD_DIR, today)
        os.makedirs(out_dir, exist_ok=True)

        processed = 0
        skipped_wm = 0

        for i, video in enumerate(videos):
            if processed >= count:
                break

            url   = video['url']
            title = video['title']
            log(f'\n[{i+1}/{len(videos)}] {title}')

            # ── 先下载视频 ──
            log(f'  ⬇ 下载中...')
            tmp_name = f'_tmp_{task_id}_{i}'
            tmp_path = os.path.join(out_dir, tmp_name + '.%(ext)s')
            dl_cmd = [YT_DLP] + get_base_args() + [
                '-f', 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
                '-o', tmp_path,
                '--no-playlist', url
            ]
            r = subprocess.run(dl_cmd, capture_output=True, text=True,
                               encoding='utf-8', errors='replace', timeout=120)

            # 找下载的文件（扫描目录找前缀匹配）
            downloaded = None
            for f in sorted(os.listdir(out_dir)):
                if f.startswith(tmp_name) and not f.endswith('.part'):
                    downloaded = os.path.join(out_dir, f)
                    break

            if r.returncode != 0 or not downloaded:
                log(f'  ❌ 下载失败，跳过')
                if r.returncode != 0:
                    # 打印前200字符错误
                    err = (r.stderr or r.stdout or '')[-200:]
                    log(f'  错误: {err}')
                continue

            # ── 检测水印 ──
            log(f'  🔍 检测水印...')
            wm = has_watermark(downloaded)
            if wm:
                os.remove(downloaded)
                skipped_wm += 1
                task['skipped'] = skipped_wm
                log(f'  ⚠️ 检测到水印，已跳过')
                continue

            log(f'  ✅ 无水印')

            if auto_pipeline:
                # ── 跑字幕流水线 ──
                log(f'  📝 生成字幕...')
                try:
                    import whisper
                    model = whisper.load_model("base")
                    res = model.transcribe(downloaded, task="translate", language="en", verbose=False)
                    segments = res["segments"]

                    try:
                        from deep_translator import GoogleTranslator
                        translator = GoogleTranslator(source="en", target="zh-CN")
                        for seg in segments:
                            try:
                                seg["text"] = translator.translate(seg["text"].strip())
                            except:
                                pass
                    except ImportError:
                        pass

                    def ft(s):
                        ms = int((s%1)*1000)
                        return f"{int(s)//3600:02d}:{int(s)//60%60:02d}:{int(s)%60:02d},{ms:03d}"

                    base_name = os.path.splitext(downloaded)[0]
                    srt_path  = base_name + '_zh.srt'
                    with open(srt_path, 'w', encoding='utf-8') as sf:
                        for idx, seg in enumerate(segments, 1):
                            sf.write(f"{idx}\n{ft(seg['start'])} --> {ft(seg['end'])}\n{seg['text'].strip()}\n\n")

                    output_file  = base_name + '_字幕.mp4'
                    srt_escaped  = srt_path.replace('\\', '/').replace(':', '\\:')
                    ffcmd = [
                        FFMPEG, '-y', '-i', downloaded,
                        '-vf', f"subtitles='{srt_escaped}':force_style='FontName=Arial,FontSize=14,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2'",
                        '-c:v', 'libx264', '-c:a', 'aac', '-b:a', '128k',
                        '-pix_fmt', 'yuv420p', '-y', output_file
                    ]
                    subprocess.run(ffcmd, capture_output=True)
                    for tmp in [downloaded, srt_path]:
                        if os.path.isfile(tmp):
                            os.remove(tmp)
                    log(f'  🎬 完成: {os.path.basename(output_file)}')
                    task['results'].append(output_file)
                except Exception as e:
                    log(f'  ⚠️ 字幕失败: {e}，保留原视频')
                    task['results'].append(downloaded)
            else:
                task['results'].append(downloaded)
                log(f'  💾 已保存: {os.path.basename(downloaded)}')

            processed += 1
            task['processed'] = processed

        log(f'\n🎉 完成！共处理 {processed} 个视频，跳过水印 {skipped_wm} 个')
        log(f'📁 保存目录: {out_dir}')
        task['status'] = 'done'
        task['output_dir'] = out_dir

    except Exception as e:
        import traceback
        task['status'] = 'error'
        task['error'] = str(e)
        task['logs'].append(f'❌ {traceback.format_exc()}')


@app.route('/api/crawl/start', methods=['POST'])
def crawl_start():
    data = request.json
    keyword       = data.get('keyword', '').strip()
    count         = min(int(data.get('count', 5)), 20)
    auto_pipeline = data.get('auto_pipeline', True)

    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    task_id = str(uuid.uuid4())
    crawl_tasks[task_id] = {
        'status': 'running', 'logs': [],
        'processed': 0, 'skipped': 0,
        'results': [], 'output_dir': '', 'error': ''
    }
    t = threading.Thread(target=run_crawl,
                         args=(task_id, keyword, count, auto_pipeline), daemon=True)
    t.start()
    return jsonify({'task_id': task_id})


@app.route('/api/crawl/task/<task_id>', methods=['GET'])
def crawl_task_status(task_id):
    task = crawl_tasks.get(task_id)
    if not task:
        return jsonify({'error': '任务不存在'}), 404
    return jsonify(task)
if __name__ == '__main__':
    print(f"yt-dlp 路径: {YT_DLP}")
    print(f"下载目录: {DOWNLOAD_DIR}")
    print("启动服务: http://127.0.0.1:5000")
    app.run(debug=False, host='127.0.0.1', port=5000)

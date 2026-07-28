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


if __name__ == '__main__':
    print(f"yt-dlp 路径: {YT_DLP}")
    print(f"下载目录: {DOWNLOAD_DIR}")
    print("启动服务: http://127.0.0.1:5000")
    app.run(debug=False, host='127.0.0.1', port=5000)

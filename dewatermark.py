"""
参考帧修复去水印
原理：水印区域在其他帧的同一位置背景是可见的，
      取多个参考帧该区域的中位数像素来填充，比 inpaint 更准确。
用法：python dewatermark.py <视频> <x> <y> <w> <h>
"""

import cv2
import numpy as np
import subprocess
import os
import sys
import shutil

FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ffmpeg.exe')


def remove_watermark(video_path, x, y, w, h, ref_count=15):
    """
    ref_count: 取多少帧做参考（越多越准，越慢）
    """
    cap = cv2.VideoCapture(video_path)
    fps   = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f'视频: {fw}x{fh}, {fps:.1f}fps, {total}帧')
    print(f'水印区域: x={x}, y={y}, w={w}, h={h}')

    # ── 第一步：采样参考帧，提取水印区域的背景 ──────────────────
    # 均匀取 ref_count 帧，取水印区域的像素中位数
    # 中位数能过滤掉运动前景，保留静止背景
    print(f'采样 {ref_count} 帧构建背景参考...')
    step = max(1, total // ref_count)
    patches = []
    for i in range(0, min(total, ref_count * step), step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if ret:
            patches.append(frame[y:y+h, x:x+w].astype(np.float32))

    # 中位数背景：逐像素取中位数，去掉水印和前景动作
    bg_patch = np.median(np.stack(patches, axis=0), axis=0).astype(np.uint8)
    print(f'背景参考构建完成，使用了 {len(patches)} 帧')

    # ── 第二步：逐帧替换水印区域 ────────────────────────────────
    # 直接用 bg_patch 替换水印区域，效果干净
    # 但边缘可能有硬切感，所以做一个羽化过渡

    # 生成羽化遮罩（边缘10px渐变）
    feather = 12
    mask_f = np.ones((h, w), dtype=np.float32)
    for i in range(feather):
        alpha = i / feather
        if i < h: mask_f[i, :] *= alpha
        if h-1-i >= 0: mask_f[h-1-i, :] *= alpha
        if i < w: mask_f[:, i] *= alpha
        if w-1-i >= 0: mask_f[:, w-1-i] *= alpha
    mask_f = np.stack([mask_f]*3, axis=2)

    base        = os.path.splitext(video_path)[0]
    frames_dir  = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp_frames')
    output_path = base + '_去水印.mp4'
    os.makedirs(frames_dir, exist_ok=True)

    print('逐帧处理...')
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        orig_patch = frame[y:y+h, x:x+w].astype(np.float32)
        # 羽化混合：边缘保留原始，中心用背景替换
        blended = bg_patch.astype(np.float32) * mask_f + orig_patch * (1 - mask_f)
        frame[y:y+h, x:x+w] = blended.astype(np.uint8)

        cv2.imwrite(os.path.join(frames_dir, f'f{frame_idx:06d}.png'), frame)
        frame_idx += 1
        if frame_idx % 50 == 0:
            print(f'  {frame_idx}/{total} ({int(frame_idx/total*100)}%)')

    cap.release()
    print(f'帧处理完成: {frame_idx} 帧，合成视频...')

    # ── 第三步：FFmpeg 合成 ──────────────────────────────────────
    cmd = [
        FFMPEG, '-y',
        '-framerate', str(fps),
        '-i', os.path.join(frames_dir, 'f%06d.png'),
        '-i', video_path,
        '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        '-map', '0:v:0', '-map', '1:a:0?', '-shortest',
        output_path
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    shutil.rmtree(frames_dir, ignore_errors=True)

    if r.returncode != 0:
        print(f'FFmpeg 失败: {r.stderr[-400:]}')
        return None

    size_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f'完成: {output_path} ({size_mb:.1f} MB)')
    return output_path


if __name__ == '__main__':
    if len(sys.argv) < 6:
        print('用法: python dewatermark.py <视频> <x> <y> <w> <h>')
        sys.exit(1)
    video = sys.argv[1]
    x, y, w, h = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    remove_watermark(video, x, y, w, h)

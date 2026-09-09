"""
字幕翻译烧录脚本
用法: python subtitle.py <视频文件路径>
功能: 自动识别英文语音 → 翻译成中文 → 烧录进视频
"""

import sys
import os
import subprocess
import whisper

def format_srt_time(seconds):
    """把秒数转成 SRT 时间格式 00:00:00,000"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds) % 60
    m = int(seconds) // 60 % 60
    h = int(seconds) // 3600
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def generate_srt(segments, output_path):
    """生成 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = format_srt_time(seg["start"])
            end = format_srt_time(seg["end"])
            text = seg["text"].strip()
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
    print(f"字幕已保存: {output_path}")

def burn_subtitles(video_path, srt_path, output_path, ffmpeg_path="ffmpeg.exe"):
    """用 FFmpeg 把字幕烧录进视频"""
    # 处理路径中的特殊字符
    srt_escaped = srt_path.replace("\\", "/").replace(":", "\\:")
    
    cmd = [
        ffmpeg_path,
        "-i", video_path,
        "-vf", f"subtitles='{srt_escaped}':force_style='FontName=Arial,FontSize=14,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Alignment=2'",
        "-c:a", "aac",       # 转成 aac，微信/手机通用
        "-b:a", "128k",
        "-y",
        output_path
    ]
    
    print("正在烧录字幕，请稍候...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        print(f"完成！输出文件: {output_path}")
    else:
        print("烧录失败，错误信息:")
        print(result.stderr[-2000:])

def main():
    if len(sys.argv) < 2:
        print("用法: python subtitle.py <视频文件路径>")
        print("例如: python subtitle.py downloads/video.webm")
        sys.exit(1)
    
    video_path = sys.argv[1]
    if not os.path.exists(video_path):
        print(f"文件不存在: {video_path}")
        sys.exit(1)
    
    base = os.path.splitext(video_path)[0]
    srt_path = base + "_zh.srt"
    output_path = base + "_字幕.mp4"
    
    # 1. 加载 Whisper 模型（base 够用，large 更准但慢）
    print("加载 Whisper 模型...")
    model = whisper.load_model("base")
    
    # 2. 识别音频并直接翻译成中文
    print("正在识别并翻译字幕（这可能需要几分钟）...")
    result = model.transcribe(
        video_path,
        task="translate",      # translate = 直接翻译成英文中间结果再输出
        language="en",         # 源语言英文
        verbose=False
    )
    
    # 注意：whisper 的 translate task 输出英文，不是中文
    # 如果要中文需要安装额外翻译库，这里先输出英文字幕
    # 如果需要中文翻译，取消下面的注释并安装: pip install deep-translator
    
    segments = result["segments"]
    
    # 尝试中文翻译
    try:
        from deep_translator import GoogleTranslator
        print("正在翻译成中文...")
        translator = GoogleTranslator(source="en", target="zh-CN")
        MAX_CHARS = 490
        ok = fail = 0
        for seg in segments:
            raw = seg["text"].strip()
            if not raw:
                continue
            text = raw[:MAX_CHARS] if len(raw) > MAX_CHARS else raw
            translated = None
            for attempt in range(3):
                try:
                    translated = GoogleTranslator(source="en", target="zh-CN").translate(text)
                    if translated:
                        break
                except Exception as e:
                    if attempt == 2:
                        print(f"  ⚠️ 翻译失败（保留原文）: {e}")
            if translated:
                seg["text"] = translated
                ok += 1
            else:
                fail += 1
        print(f"翻译完成：{ok} 段成功" + (f"，{fail} 段保留原文" if fail else ""))
    except ImportError:
        print("提示：未安装 deep-translator，字幕将保留英文。")
        print("安装中文翻译: pip install deep-translator")
    
    # 3. 生成 SRT 文件
    generate_srt(segments, srt_path)
    
    # 4. 烧录字幕
    burn_subtitles(video_path, srt_path, output_path)

if __name__ == "__main__":
    main()

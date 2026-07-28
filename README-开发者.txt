yt-dlp 可视化下载器 - 开发者说明
=====================================

## 项目结构

  yt-dlp/
  ├── app.py              # Flask 后端，提供 API 接口
  ├── yt-dlp.exe          # yt-dlp 主程序
  ├── ffmpeg.exe          # 用于合并视频/音频流
  ├── cookies.txt         # YouTube cookies（自行导出，不随项目分发）
  ├── static/
  │   └── index.html      # 前端页面（纯 HTML+JS，无框架）
  ├── downloads/          # 下载文件存放目录（自动创建）
  ├── 启动.bat
  └── 一键安装环境.bat


## 依赖环境

  - Python 3.x
  - Node.js（yt-dlp 解析 YouTube 需要 JS 运行时）
  - Flask：pip install flask


## 启动方式

  python app.py
  # 服务运行在 http://127.0.0.1:5000


## API 接口

  POST /api/formats         解析视频，返回可用格式列表
  POST /api/download        启动下载任务，返回 task_id
  GET  /api/task/<task_id>  查询任务进度和日志
  GET  /api/downloads       列出已下载文件
  GET  /api/cookies-status  检查 cookies.txt 是否存在
  POST /api/upload-cookies  上传 cookies.txt 文件
  POST /api/cookies-from-browser  从浏览器导出 cookies


## YouTube 认证

  YouTube 需要登录 cookies 才能下载。
  推荐使用浏览器插件 "Get cookies.txt LOCALLY" 导出，
  保存为 cookies.txt 放在项目根目录即可自动生效。


## 注意

  - yt-dlp.exe 和 ffmpeg.exe 需要和 app.py 在同一目录
  - 下载文件保存在 ./downloads/ 目录
  - cookies.txt 包含账号信息，请勿泄露或提交到代码仓库

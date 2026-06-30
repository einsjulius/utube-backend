from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp
import tempfile
import os
import re

app = Flask(__name__)
CORS(app)  # Allow requests from GitHub Pages

QUALITY_MAP_VIDEO = {
    '4k':   'bestvideo[height<=2160]+bestaudio/best[height<=2160]',
    '1440': 'bestvideo[height<=1440]+bestaudio/best[height<=1440]',
    '1080': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
    '720':  'bestvideo[height<=720]+bestaudio/best[height<=720]',
    '480':  'bestvideo[height<=480]+bestaudio/best[height<=480]',
    '360':  'bestvideo[height<=360]+bestaudio/best[height<=360]',
}

QUALITY_MAP_AUDIO = {
    '320': '320', '256': '256', '192': '192',
    '128': '128', '96': '96',   '64':  '64',
}


@app.route('/')
def index():
    return jsonify({'status': 'U Tube Video Loader backend running ✓'})


@app.route('/download', methods=['POST'])
def download():
    data = request.json
    url         = data.get('url')
    fmt         = data.get('format', 'mp4')
    quality     = data.get('quality', '1080')
    embed_meta  = data.get('embed_metadata', True)
    embed_subs  = data.get('embed_subs', False)
    write_thumb = data.get('write_thumbnail', False)

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    tmpdir = tempfile.mkdtemp()

    if fmt == 'mp3':
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': QUALITY_MAP_AUDIO.get(quality, '192'),
            }],
            'addmetadata': embed_meta,
            'writethumbnail': write_thumb,
        }
        if embed_subs:
            ydl_opts['writesubtitles'] = True
    else:
        ydl_opts = {
            'format': QUALITY_MAP_VIDEO.get(quality, 'bestvideo+bestaudio/best'),
            'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
            'merge_output_format': 'mp4',
            'addmetadata': embed_meta,
            'writethumbnail': write_thumb,
        }
        if embed_subs:
            ydl_opts['writesubtitles'] = True
            ydl_opts['embedsubtitles'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Find the output file
    files = [
        f for f in os.listdir(tmpdir)
        if f.endswith(('.mp4', '.mp3', '.webm', '.m4a', '.ogg'))
    ]
    if not files:
        return jsonify({'error': 'No output file was created'}), 500

    filepath  = os.path.join(tmpdir, files[0])
    safe_name = re.sub(r'[^\w\s\-.]', '', files[0]).strip()
    mime      = 'audio/mpeg' if fmt == 'mp3' else 'video/mp4'

    return send_file(
        filepath,
        as_attachment=True,
        download_name=safe_name,
        mimetype=mime,
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

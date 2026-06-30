from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
import yt_dlp
import tempfile
import os
import re

app = Flask(__name__)
CORS(app)

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

COOKIE_FILE = None

def setup_cookies():
    """Write YOUTUBE_COOKIES env var to a temp file once on startup."""
    global COOKIE_FILE
    cookie_content = os.environ.get('YOUTUBE_COOKIES', '').strip()
    if not cookie_content:
        print("⚠ No YOUTUBE_COOKIES env var found — YouTube may block requests.")
        return
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.txt', delete=False, prefix='yt_cookies_'
    )
    tmp.write(cookie_content)
    tmp.close()
    COOKIE_FILE = tmp.name
    print(f"✓ Cookies loaded from env → {COOKIE_FILE}")

setup_cookies()


@app.route('/')
def index():
    cookie_status = "loaded" if COOKIE_FILE else "missing (downloads may fail)"
    return jsonify({
        'status': 'U Tube Video Loader backend running ✓',
        'cookies': cookie_status
    })


@app.route('/download', methods=['POST'])
def download():
    data        = request.json
    url         = data.get('url')
    fmt         = data.get('format', 'mp4')
    quality     = data.get('quality', '1080')
    embed_meta  = data.get('embed_metadata', True)
    embed_subs  = data.get('embed_subs', False)
    write_thumb = data.get('write_thumbnail', False)

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    tmpdir = tempfile.mkdtemp()

    # Base options — always include cookies if available
    base_opts = {
        'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
        'addmetadata': embed_meta,
        'writethumbnail': write_thumb,
        # Mimic a real browser to reduce bot detection
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        },
    }

    if COOKIE_FILE:
        base_opts['cookiefile'] = COOKIE_FILE

    if fmt == 'mp3':
        ydl_opts = {
            **base_opts,
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': QUALITY_MAP_AUDIO.get(quality, '192'),
            }],
        }
        if embed_subs:
            ydl_opts['writesubtitles'] = True
    else:
        ydl_opts = {
            **base_opts,
            'format': QUALITY_MAP_VIDEO.get(quality, 'bestvideo+bestaudio/best'),
            'merge_output_format': 'mp4',
        }
        if embed_subs:
            ydl_opts['writesubtitles'] = True
            ydl_opts['embedsubtitles'] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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

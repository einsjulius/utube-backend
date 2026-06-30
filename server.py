from flask import Flask, request, send_file, jsonify, make_response
from flask_cors import CORS, cross_origin
import yt_dlp
import tempfile
import os
import re

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False)

# Each entry is a list of formats tried in order until one works
QUALITY_MAP_VIDEO = {
    '4k':   'bestvideo[height<=2160]+bestaudio/bestvideo[height<=1080]+bestaudio/best',
    '1440': 'bestvideo[height<=1440]+bestaudio/bestvideo[height<=1080]+bestaudio/best',
    '1080': 'bestvideo[height<=1080]+bestaudio/bestvideo[height<=720]+bestaudio/best',
    '720':  'bestvideo[height<=720]+bestaudio/bestvideo[height<=480]+bestaudio/best',
    '480':  'bestvideo[height<=480]+bestaudio/bestvideo[height<=360]+bestaudio/best',
    '360':  'bestvideo[height<=360]+bestaudio/best',
}

QUALITY_MAP_AUDIO = {
    '320': '320', '256': '256', '192': '192',
    '128': '128', '96': '96',   '64':  '64',
}

COOKIE_FILE = None

def setup_cookies():
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
@cross_origin()
def index():
    cookie_status = "loaded" if COOKIE_FILE else "missing"
    return jsonify({
        'status': 'U Tube Video Loader backend running ✓',
        'cookies': cookie_status
    })


@app.route('/download', methods=['POST', 'OPTIONS'])
@cross_origin()
def download():
    if request.method == 'OPTIONS':
        response = make_response()
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        return response, 204

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

    base_opts = {
        'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
        'addmetadata': embed_meta,
        'writethumbnail': write_thumb,
        'ignoreerrors': False,
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
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        # If requested quality not available, retry with best available
        if 'Requested format is not available' in error_msg or 'format' in error_msg.lower():
            try:
                fallback_opts = {
                    **ydl_opts,
                    'format': 'bestaudio/best' if fmt == 'mp3' else 'bestvideo+bestaudio/best',
                }
                with yt_dlp.YoutubeDL(fallback_opts) as ydl2:
                    ydl2.download([url])
            except Exception as e2:
                return jsonify({'error': str(e2)}), 500
        else:
            return jsonify({'error': error_msg}), 500
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

    response = make_response(send_file(
        filepath,
        as_attachment=True,
        download_name=safe_name,
        mimetype=mime,
    ))
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

from flask import Flask, request, send_file, jsonify, make_response
from flask_cors import CORS, cross_origin
import tempfile
import os
import re
import subprocess
import sys

# Force-upgrade yt-dlp to the latest version BEFORE importing it.
# YouTube changes its site frequently, and an outdated yt-dlp build
# is the most common cause of "Requested format is not available"
# or sudden extraction failures. This must run before `import yt_dlp`
# so the freshly installed version is the one actually loaded.
try:
    subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '--upgrade', '--quiet', 'yt-dlp'],
        check=True, timeout=90
    )
    print('✓ yt-dlp upgraded to latest version')
except Exception as e:
    print(f'⚠ Could not auto-upgrade yt-dlp: {e}')

import yt_dlp

app = Flask(__name__)
CORS(app, origins="*", supports_credentials=False)

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

BASE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    )
}

# Target heights for each requested quality tier, highest first
TARGET_HEIGHTS = {
    '4k':   2160,
    '1440': 1440,
    '1080': 1080,
    '720':  720,
    '480':  480,
    '360':  360,
}

QUALITY_MAP_AUDIO = {
    '320': '320', '256': '256', '192': '192',
    '128': '128', '96': '96',   '64':  '64',
}


@app.route('/')
@cross_origin()
def index():
    cookie_status = "loaded" if COOKIE_FILE else "missing"
    return jsonify({
        'status': 'U Tube Video Loader backend running ✓',
        'cookies': cookie_status,
        'yt_dlp_version': yt_dlp.version.__version__
    })


def build_video_format_string(target_height):
    """
    Build a format selector that picks the best video stream at or below
    the target height, falling back gracefully if that exact bucket
    isn't available for this particular video.
    """
    return (
        f'bestvideo[height<={target_height}]+bestaudio/'
        f'best[height<={target_height}]/'
        f'bestvideo+bestaudio/'
        f'best'
    )


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
        'http_headers': BASE_HEADERS,
        'noplaylist': True,
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
        target_height = TARGET_HEIGHTS.get(quality, 1080)
        ydl_opts = {
            **base_opts,
            'format': build_video_format_string(target_height),
            'merge_output_format': 'mp4',
        }
        if embed_subs:
            ydl_opts['writesubtitles'] = True
            ydl_opts['embedsubtitles'] = True

    # Try the requested format chain, then progressively simpler fallbacks
    attempts = [ydl_opts]

    if fmt != 'mp3':
        # Fallback 1: plain best video+audio combo, no height filter at all
        attempts.append({**ydl_opts, 'format': 'bestvideo+bestaudio/best'})
        # Fallback 2: single progressive stream (already muxed, most compatible)
        attempts.append({**ydl_opts, 'format': 'best'})
    else:
        attempts.append({**ydl_opts, 'format': 'best'})

    last_error = None
    succeeded = False

    for attempt_opts in attempts:
        try:
            with yt_dlp.YoutubeDL(attempt_opts) as ydl:
                ydl.download([url])
            succeeded = True
            break
        except yt_dlp.utils.DownloadError as e:
            last_error = str(e)
            continue
        except Exception as e:
            last_error = str(e)
            continue

    if not succeeded:
        return jsonify({'error': last_error or 'Download failed for unknown reasons'}), 500

    files = [
        f for f in os.listdir(tmpdir)
        if f.endswith(('.mp4', '.mp3', '.webm', '.m4a', '.ogg', '.mkv'))
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

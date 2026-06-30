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
import imageio_ffmpeg

# imageio-ffmpeg ships a self-contained ffmpeg binary, so we don't depend
# on apt-get / system packages being available in the build environment.
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
print(f"✓ Using bundled ffmpeg at {FFMPEG_PATH}")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

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


@app.route('/debug', methods=['POST', 'OPTIONS'])
@cross_origin()
def debug_formats():
    """Returns the raw list of formats yt-dlp can see for a given URL,
    plus the exact error if extraction fails. Use this to diagnose
    'Requested format is not available' errors."""
    if request.method == 'OPTIONS':
        return make_response(), 204

    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    opts = {
        'http_headers': BASE_HEADERS,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': False,
        'ffmpeg_location': FFMPEG_PATH,
    }
    if COOKIE_FILE:
        opts['cookiefile'] = COOKIE_FILE

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        formats = [
            {
                'format_id': f.get('format_id'),
                'ext': f.get('ext'),
                'height': f.get('height'),
                'vcodec': f.get('vcodec'),
                'acodec': f.get('acodec'),
                'filesize': f.get('filesize'),
            }
            for f in info.get('formats', [])
        ]
        return jsonify({
            'title': info.get('title'),
            'extractor': info.get('extractor'),
            'format_count': len(formats),
            'formats': formats,
            'cookie_file_used': bool(COOKIE_FILE),
        })
    except Exception as e:
        return jsonify({
            'error': str(e),
            'error_type': type(e).__name__,
            'cookie_file_used': bool(COOKIE_FILE),
        }), 500


@app.route('/download', methods=['POST', 'OPTIONS'])
@cross_origin()
def download():
    if request.method == 'OPTIONS':
        return make_response(), 204

    data        = request.json
    url         = data.get('url')
    fmt         = data.get('format', 'mp4')
    quality     = data.get('quality', '1080')
    embed_meta  = data.get('embed_metadata', True)
    embed_subs  = data.get('embed_subs', False)
    write_thumb = data.get('write_thumbnail', False)

    if not url:
        return jsonify({'error': 'No URL provided'}), 400

    base_opts_template = {
        'addmetadata': embed_meta,
        'writethumbnail': write_thumb,
        'http_headers': BASE_HEADERS,
        'noplaylist': True,
        'ffmpeg_location': FFMPEG_PATH,
    }
    if COOKIE_FILE:
        base_opts_template['cookiefile'] = COOKIE_FILE

    if fmt == 'mp3':
        format_attempts = ['bestaudio/best', 'best']
    else:
        target_height = TARGET_HEIGHTS.get(quality, 1080)
        # Each string here is tried in full before moving to the next.
        # yt-dlp evaluates "/" as "try left, if it fails try right" WITHIN
        # one string, but mixing a merge selector or already-failed temp
        # files across separate YoutubeDL() calls can cause false failures,
        # so we give each attempt its own clean temp dir.
        format_attempts = [
            f'bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]',
            'bestvideo+bestaudio/best',
            'best',
        ]

    last_error = None
    succeeded = False
    final_tmpdir = None

    for fmt_string in format_attempts:
        tmpdir = tempfile.mkdtemp()

        ydl_opts = {
            **base_opts_template,
            'outtmpl': os.path.join(tmpdir, '%(title)s.%(ext)s'),
            'format': fmt_string,
        }

        if fmt == 'mp3':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': QUALITY_MAP_AUDIO.get(quality, '192'),
            }]
            if embed_subs:
                ydl_opts['writesubtitles'] = True
        else:
            ydl_opts['merge_output_format'] = 'mp4'
            # Force a remux/merge postprocessor as a safety net — in some
            # environments yt-dlp picks an already-progressive format (one
            # file, video+audio together) and merge_output_format alone
            # doesn't trigger ffmpeg, which is fine; but if it instead
            # grabs separate video-only + audio-only streams, this ensures
            # ffmpeg is actually invoked to combine them losslessly.
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }]
            if embed_subs:
                ydl_opts['writesubtitles'] = True
                ydl_opts['embedsubtitles'] = True

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                chosen_format = info.get('format_id', 'unknown')
                chosen_acodec = info.get('acodec', 'unknown')
                print(f"✓ Downloaded format_id={chosen_format} acodec={chosen_acodec}")
            succeeded = True
            final_tmpdir = tmpdir
            break
        except Exception as e:
            last_error = str(e)
            continue

    if not succeeded:
        return jsonify({'error': last_error or 'Download failed for unknown reasons'}), 500

    files = [
        f for f in os.listdir(final_tmpdir)
        if f.endswith(('.mp4', '.mp3', '.webm', '.m4a', '.ogg', '.mkv'))
    ]
    if not files:
        return jsonify({'error': 'No output file was created'}), 500

    filepath  = os.path.join(final_tmpdir, files[0])
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

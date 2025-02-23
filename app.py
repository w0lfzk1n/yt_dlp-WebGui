from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    Response,
    current_app,
    send_from_directory,
)
from flask_httpauth import HTTPBasicAuth
import json
import os
import yt_dlp
import re
import unicodedata
import shutil
import time
from werkzeug.security import check_password_hash
import threading
import queue
import datetime
import logging
import hashlib
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.id3 import ID3, TIT2, TALB, TPE1, TPE2

app = Flask(__name__)
auth = HTTPBasicAuth()

DEBUG = False

# Constants
datafolder = os.path.join(os.getcwd(), "data")
IP = "0.0.0.0"
PORT = 3011
USERS_FILE = os.path.join(datafolder, "users.json")
DEFAULT_USER = "admin"
DEFAULT_LANG = "en"
OUTPUT_DIR = os.path.join(os.getcwd(), "output")
DEFAULT_OUTPUT_DIR = os.path.join(os.getcwd(), "final_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
CACHE_DIR = os.path.join(os.getcwd(), "cache")
FOLDERS_FILE = os.path.join(datafolder, "folders.json")
COOKIES = os.path.join(datafolder, "cookies.txt")
LOGS_DIR = "logs"
# The user and group the files should be assigned to for file/folder permissions
PERM_USER = "carn1v0re"
PERM_GROUP = "bunk3rGroup"

# Globals
current_download = None
progress_queue = queue.Queue()
cancel_flag = False
is_converting = False
last_percentage = None
folder_paths = {}
to_download = 0
done_download = 1
conv_heart = None

# --- Logging Setup ---
os.makedirs(LOGS_DIR, exist_ok=True)
timestamp = datetime.datetime.now().strftime("%H-%M--%d-%m-%Y")
hasher = hashlib.md5()
hasher.update(str(datetime.datetime.now()).encode("utf-8"))
hash_str = hasher.hexdigest()[:6]
log_filename = os.path.join(LOGS_DIR, f"{timestamp}--{hash_str}.log")

logging.basicConfig(
    filename=log_filename,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)


# --- Helper Functions ---
def log_message(message, box=True, framed=False):
    """Logs a message to the console, the log file, and the progress queue."""
    message = re.sub(r"\x1b\[[0-9;]*m", "", message)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    if framed:
        full_message = (20 * "-") + "<br>" + full_message + "<br>" + (20 * "-")
    if box:
        progress_queue.put(full_message)
    full_message = (
        full_message.replace("<br>", "\n")
        .replace("<strong>", "")
        .replace("</strong>", "")
    )
    print(full_message)
    logging.info(full_message)
    time.sleep(0.25)


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}


def load_folders():
    global folder_paths
    if os.path.exists(FOLDERS_FILE):
        with open(FOLDERS_FILE, "r") as f:
            folder_paths = json.load(f)["folders"]
            return folder_paths
    else:
        log_message("Error: folders.json not found, using Defaultpaths.")
        return {
            "admin": "final_output",
        }


users = load_users()
folder_paths = load_folders()


def is_playlist(url):
    return "playlist?list=" in url or "youtu.be/playlist?" in url


def is_valid_youtube_url(url):
    youtube_regex = (
        r"(https?://)?(www\.)?"
        "(youtube|youtu|youtube-nocookie)\.(com|be)/"
        "(playlist\?list=|watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11,})"
    )
    youtube_regex_match = re.match(youtube_regex, url)
    return bool(youtube_regex_match)


def my_hook(d):
    global last_percentage, is_converting, to_download, done_download, conv_heart
    if d["status"] == "info":
        log_message(d["msg"])
    elif d["status"] == "downloading":
        is_converting = False
        if conv_heart:
            conv_heart.join()
        percent_str = re.sub(r"\x1b\[[0-9;]*m", "", d["_percent_str"])
        rate_str = re.sub(r"\x1b\[[0-9;]*m", "", d.get("_speed_str", "N/A"))
        total_bytes_str = d.get("_total_bytes_str", "N/A")

        progress_message = f"Download [{done_download:>2}/{to_download:<2}] | {percent_str:<6} of {total_bytes_str:<6} | {rate_str:<6}"

        if percent_str != last_percentage:
            last_percentage = percent_str
            log_message(progress_message)
    elif d["status"] == "finished":
        is_converting = True
        done_download += 1
        log_message(
            "Download of media finished. Start Converting...<br>⚠️ This could take a while, depending on size...",
            True,
            True,
        )
        conv_heart = threading.Thread(target=send_conv_heartbeat, daemon=True)
        conv_heart.start()
    elif d["status"] == "failed":
        log_message(f"Error: {d['msg']}")
    elif d["status"] == "test":
        log_message(d["msg"])
    elif d["status"] == "moving":
        log_message(d["msg"])
    elif d["status"] == "complete":
        log_message(d["msg"], True, True)


def send_conv_heartbeat():
    """Sends periodic heartbeat messages while conversion is ongoing."""
    global is_converting
    start_time = time.time()
    while is_converting:
        elapsed = int(time.time() - start_time)
        if elapsed <= 5:
            continue
        log_message(f"⏳ Still working... {elapsed}s elapsed", True)
        time.sleep(10)
    return


def set_owner_recursive(path, user, group):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            shutil.chown(os.path.join(root, d), user, group)
            os.chmod(os.path.join(root, d), 0o775)
        for f in files:
            shutil.chown(os.path.join(root, f), user, group)
            os.chmod(os.path.join(root, f), 0o664)
    shutil.chown(path, user, group)
    os.chmod(path, 0o775)


def clear_output_folder(folder):
    for filename in os.listdir(folder):
        file_path = os.path.join(folder, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            log_message(f"Delete of '{file_path}' failed. Reason: {e}")


def load_translations(language=DEFAULT_LANG):
    with open(f"lang/{language}.json", "r", encoding="utf-8") as f:
        return json.load(f)


def clean_filename(filename: str) -> str:
    filename = unicodedata.normalize("NFKD", filename)
    filename = "".join(
        c
        for c in filename
        if c.isprintable() and not unicodedata.category(c).startswith("So")
    )
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F⧸©]', "", filename)
    filename = filename.replace("  ", " ")
    filename = filename.strip().strip(".")
    return filename


def fix_mp3(file_path):
    """Attempt to fix MP3 files with missing headers or broken metadata."""
    try:
        audio = MP3(file_path, ID3=ID3)
        if audio.tags is None:
            audio.add_tags()
        audio.save()
        print(f"🔧 Fixed MP3 file: {file_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to fix MP3 file {file_path}: {e}")
        return False


def update_metadata(username, file_path, album):
    """Update metadata for MP3 and MP4 files."""
    try:
        album = f"{username.capitalize()} - {album}"
        if file_path.endswith(".mp3"):
            audio = MP3(file_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()

            audio.tags[TALB] = TALB(encoding=3, text=album)
            audio.tags[TPE2] = TPE2(encoding=3, text=album)

            audio.save()
        elif file_path.endswith(".mp4"):
            audio = MP4(file_path)
            audio.tags["\xa9alb"] = [album]
            audio.tags["aART"] = [album]
            audio.save()
        log_message(f"✅ Updated metadata for: {file_path}")
    except Exception as e:
        log_message(f"❌ Error while updating metadata for {file_path}: {e}")
        if file_path.endswith(".mp3") and fix_mp3(file_path):
            update_metadata(username, file_path, album)


def get_cache_filename(url):
    """Generate a hash-based filename for a given URL."""
    hash_str = hashlib.md5(url.encode()).hexdigest()[:10]
    return os.path.join(CACHE_DIR, f"{hash_str}.json")


def save_to_cache(url, info):
    """Save the retrieved info to a JSON cache file."""
    cache_file = get_cache_filename(url)
    cache_hash = os.path.basename(cache_file).replace(".json", "")
    log_message(f"💾 Cached Hash: {cache_hash}")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({"url": url, "info": info}, f, indent=4)


def load_from_cache(url):
    """Load cached info if it exists."""
    cache_file = get_cache_filename(url)
    if os.path.exists(cache_file):
        cache_hash = os.path.basename(cache_file).replace(".json", "")
        log_message(f"💾 Cached Hash: {cache_hash}")
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)["info"]
    return None


def my_retry_sleep(retry_count, last_error=None):
    """
    Returns the sleep time before retrying and logs the reason.
    """
    base_sleep = min(10 * (2 ** (retry_count - 1)), 60)
    sleep_time = base_sleep

    if last_error:
        error_message = str(last_error).lower()

        if "cannot assign requested address" in error_message:
            sleep_time = 30
            log_message(
                f"⚠️ Network issue detected. Retrying in {sleep_time} seconds...",
                True,
                True,
            )

        elif "429" in error_message or "too many requests" in error_message:
            sleep_time = 60
            log_message(
                f"🚨 Rate limit hit (HTTP 429). Retrying in {sleep_time} seconds...",
                True,
                True,
            )

        elif "http error 403" in error_message:
            sleep_time = 5
            log_message(
                f"🔒 HTTP 403 Forbidden. Retrying in {sleep_time} seconds...",
                True,
                True,
            )

        else:
            log_message(
                f"⚠️ Unknown error: {last_error}. Retrying in {sleep_time} seconds...",
                True,
                True,
            )

    else:
        log_message(f"🔄 Retrying in {sleep_time} seconds...", True, True)

    return sleep_time


# --- Authentication ---
@auth.verify_password
def verify_password(username, password):
    if username in users and check_password_hash(users[username]["p"], password):
        log_message(f"User '{username}' logged in.", False)
        return username
    return None


@app.route("/logout", methods=["GET"])
def logout():
    response = jsonify({"message": "Logged out"})
    response.headers["WWW-Authenticate"] = 'Basic realm="Login Required"'
    response.status_code = 401  # Forces the browser to forget credentials
    return response


# --- Routes ---
@app.route("/")
@auth.login_required
def index():
    username = auth.current_user()
    default_folder = users[username].get("default_folder", DEFAULT_USER)
    translations = load_translations(language=DEFAULT_LANG)
    folder = default_folder
    base_path = folder_paths[folder]
    subfolders = ["New"] + [
        f
        for f in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, f)) and f != "Others"
    ]
    available_folders = list(folder_paths.keys())
    return render_template(
        "index.html",
        translations=translations,
        initial_subfolders=subfolders,
        available_folders=available_folders,
    )


@app.route("/get_subfolders", methods=["POST"])
@auth.login_required
def get_subfolders():
    folder = request.form["folder"]
    base_path = folder_paths[folder]
    subfolders = ["New"] + [
        f
        for f in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, f)) and f != "Others"
    ]
    return jsonify(subfolders)


@app.route("/lang/<lang>.json")
def serve_lang_json(lang):
    lang_file_path = os.path.join(app.root_path, "lang", f"{lang}.json")
    if os.path.exists(lang_file_path):
        return send_from_directory("lang", f"{lang}.json")
    else:
        return "File not found", 404


@app.route("/download", methods=["POST"])
@auth.login_required
def download():
    global cancel_flag
    cancel_flag = False
    urls = request.form["url"].strip().split("\n")
    urls = [url.strip() for url in urls if url.strip()]
    folder = request.form["folder"]
    custom_filename = request.form["custom_filename"]
    format_type = request.form["format_type"]
    subfolder = request.form["subfolder"]
    use_cache = request.form["use_cache"]

    invalid_urls = [url for url in urls if not is_valid_youtube_url(url)]
    if invalid_urls:
        return jsonify({"error": f"Invalid URLs found: {', '.join(invalid_urls)}"}), 400
    if folder not in folder_paths:
        return jsonify({"error": "Invalid folder selected! ⚠️"}), 400
    if format_type not in ["mp3", "mp4"]:
        return jsonify({"error": f'Invalid format selected! "{format_type}" ⚠️'}), 400
    if subfolder == "New" and not request.form.get("new_subfolder"):
        return jsonify({"error": "You have not set a name for the new folder! ⚠️"}), 400

    if subfolder == "New":
        subfolder = request.form["new_subfolder"]

    playlist_urls = [url for url in urls if is_playlist(url)]
    if playlist_urls and not subfolder:
        return jsonify({"error": "Playlists require a subfolder! ⚠️"}), 400

    if playlist_urls and request.form.get("custom_filename"):
        return jsonify({"error": "Playlists do not support custom filenames! ⚠️"}), 400

    log_message(f"❇️ Received {len(urls)} YouTube URLs", True)

    log_message(
        f"Preparing download...<br>⚠️ INFO ⚠️<br>Site can be closed, process will complete in background.",
        True,
        True,
    )
    time.sleep(0.3)
    username = auth.current_user()
    def process_downloads_sequentially(urls, folder, custom_filename, format_type, subfolder, username, use_cache, app):
        global complete_msg
        complete_msg = ''
        for i, url in enumerate(urls, start=1):
            log_message(f"🔹 [{i}/{len(urls)}] Starte Download für:<br>{url}", True, True)

            thread = threading.Thread(
                target=download_task,
                args=(url, folder, custom_filename, format_type, subfolder, username, use_cache, app),
            )
            thread.start()
            thread.join()
        my_hook({"status": "complete", "msg": complete_msg})

    threading.Thread(
        target=process_downloads_sequentially,
        args=(urls, folder, custom_filename, format_type, subfolder, username, use_cache, current_app._get_current_object()),
        daemon=True
    ).start()

    return jsonify({"success": True, "message": "Download started..."})


@app.route("/cancel", methods=["POST"])
@auth.login_required
def cancel_download():
    global current_download, cancel_flag
    cancel_flag = True
    if current_download:
        current_download.cancel()
        current_download = None
        log_message("Download canceled (by yt-dlp).")
    else:
        log_message("No active download to cancel (But have set 'Cancel-Flag').")
    return jsonify({"success": True, "message": "Download canceled (by User)"})


@app.route("/progress")
def progress():
    def generate():
        while True:
            try:
                message = progress_queue.get(timeout=1)
                if "[HEARTBEAT]" not in message:
                    yield f"data: {message}\n\n"
            except queue.Empty:
                yield f"data: \n\n"
                time.sleep(2)

    return Response(generate(), mimetype="text/event-stream")


def download_task(
    url, folder, custom_filename, format_type, subfolder, username, use_cache, app
):
    with app.app_context():
        global current_download, cancel_flag, is_converting, last_percentage, to_download, done_download, complete_msg
        time.sleep(0.2)
        log_message(f"URL set: {url}")
        time.sleep(0.2)
        base_path = folder_paths[folder]

        if subfolder == "New":
            log_message("Did not set name for new folder!", False)
            return {"error": "Did not set name for new folder!"}
        elif not subfolder or subfolder == "Others":
            target_folder = os.path.join(base_path, "Others")
        else:
            target_folder = os.path.join(base_path, subfolder)

        log_message(f"📂 Folder set: {target_folder}")

        os.makedirs(target_folder, exist_ok=True)
        output_path = os.path.join(OUTPUT_DIR, username)
        os.makedirs(output_path, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        clear_output_folder(output_path)
        time.sleep(0.2)
        log_message(f"Folder CHK/CLEAN for: '{username}' complete!")
        time.sleep(0.2)

        existing_files = set(os.listdir(target_folder))

        use_cookies = os.path.exists(COOKIES) and os.path.getsize(COOKIES) > 0

        ydl_opts = {
            "outtmpl": os.path.join(output_path, "%(title)s.%(ext)s"),
            "socket_timeout": 60,
            "no_cache_dir": True,
            "progress_hooks": [my_hook],
            "flat_playlist": True,
            "ignoreerrors": True,
            "retries": 5,
            "sleep_interval": 5,
            "retry_sleep_functions": {
                "http": my_retry_sleep,
                "fragment": my_retry_sleep,
                "file_access": my_retry_sleep,
                "extractor": my_retry_sleep,
            },
            "writethumbnail": True,
            "postprocessors": [
                {
                    "key": "EmbedThumbnail",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "addmetadata": True,
        }

        if use_cookies:
            ydl_opts["cookiefile"] = COOKIES

        if format_type == "mp3":
            ydl_opts["format"] = "bestaudio/best"
            ydl_opts["postprocessors"].insert(
                0,
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                },
            )
        else:
            ydl_opts["format"] = (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            )

        if custom_filename:
            ydl_opts["outtmpl"] = os.path.join(
                output_path, f"{custom_filename}.%(ext)s"
            )

        try:
            time.sleep(0.2)
            log_message(
                f"♻️ Retrieving Video Details...<br>❗️ This could take a while, please wait...<br>{(20*'-')}",
                True,
            )

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                current_download = ydl
                retrieving = True
                start_time = time.time()

                def send_heartbeat():
                    while retrieving:
                        elapsed = int(time.time() - start_time)
                        if elapsed <= 5:
                            continue
                        log_message(f"⏳ Still retrieving... {elapsed}s elapsed", True)
                        time.sleep(10)

                if use_cache == "true" or use_cache == True:
                    cached_info = load_from_cache(url)
                    if cached_info:
                        log_message("✅ Using cached info. Skipping retrieval.", True)
                        info = cached_info
                    else:
                        log_message("♻️ No cache found. Retrieving info...", True)
                        heartbeat_thread = threading.Thread(
                            target=send_heartbeat, daemon=True
                        )
                        heartbeat_thread.start()

                        info = ydl.extract_info(url, download=False)
                        retrieving = False
                        heartbeat_thread.join()
                        save_to_cache(url, info)
                else:
                    heartbeat_thread = threading.Thread(
                        target=send_heartbeat, daemon=True
                    )
                    heartbeat_thread.start()

                    info = ydl.extract_info(url, download=False)
                    retrieving = False
                    heartbeat_thread.join()
                    log_message("♻️ Caching retrieved information...", True)
                    save_to_cache(url, info)

                available_videos = []
                unavailable_videos = []
                expected_files = []
                moved_files = []
                missing_files = []

                videos_to_download = []
                videos_existing = []

                # Check for available and unavailable videos before starting the download
                if info and "entries" in info:  # Playlist
                    for entry in info["entries"]:
                        if entry:
                            if entry.get("availability", "") == "unavailable":
                                unavailable_videos.append(
                                    entry.get("title", "Unknown Video")
                                )
                            else:
                                filename = ydl.prepare_filename(entry)
                                final_filename = (
                                    os.path.splitext(filename)[0].strip()
                                    + "."
                                    + (format_type if format_type == "mp3" else "mp4")
                                )
                                if (
                                    clean_filename(os.path.basename(final_filename))
                                    not in existing_files
                                ):
                                    videos_to_download.append(entry["original_url"])
                                    expected_files.append(final_filename)
                                    available_videos.append(
                                        entry.get("title", "Unknown Video")
                                    )
                                else:
                                    videos_existing.append(final_filename)
                        else:
                            unavailable_videos.append("Unknown Video")
                    to_download = len(videos_to_download)
                    if format_type == "mp4":
                        to_download = to_download * 2

                else:  # Single Video
                    if not info or info.get("availability", "") == "unavailable":
                        unavailable_videos.append(
                            info.get("title", "Unknown Video")
                            if info
                            else "Unknown Video"
                        )
                    else:
                        filename = ydl.prepare_filename(info)
                        final_filename = (
                            os.path.splitext(filename)[0].strip()
                            + "."
                            + (format_type if format_type == "mp3" else "mp4")
                        )
                        if (
                            clean_filename(os.path.basename(final_filename))
                            not in existing_files
                        ):
                            videos_to_download.append(info["original_url"])
                            expected_files.append(final_filename)
                            available_videos.append(info.get("title", "Unknown Video"))
                        else:
                            videos_existing.append(final_filename)
                        to_download = len(videos_to_download)
                        if format_type == "mp4":
                            to_download = to_download * 2

                if not videos_to_download:
                    log_message("✅ All videos are already downloaded.", True, True)
                    time.sleep(0.2)
                    return {
                        "success": True,
                        "message": "All videos are already downloaded.",
                    }

                info_msg = ""
                if available_videos:
                    info_msg += (
                        "✅ The following videos are available for download:<br> - "
                        + "<br> - ".join(available_videos)
                        + f"<br> Total: {len(available_videos)}"
                    )
                if videos_existing:
                    info_msg += f"<br><br>🆗 There are <strong>{len(videos_existing)}</strong> Videos that are already downloaded."
                if unavailable_videos:
                    info_msg += f"<br><br>❌ There are <strong>{len(unavailable_videos)}</strong> Videos that can not be downloaded."

                time.sleep(0.2)
                log_message(info_msg, True, True)
                time.sleep(0.2)
                retrieving = False
                log_message("⏬ Starting Download ⏬", True)
                time.sleep(0.2)

                ydl.download(videos_to_download)
                is_converting = False

                album_name = os.path.basename(target_folder)
                log_message(f"⚒ Managing Metadata<br>{20*'-'}<br>", True)
                for final_filename in expected_files:
                    cleaned_filename = clean_filename(os.path.basename(final_filename))
                    if os.path.exists(final_filename):
                        destination = os.path.join(target_folder, cleaned_filename)
                        shutil.move(final_filename, destination)
                        moved_files.append(cleaned_filename)

                        update_metadata(username, destination, album_name)
                    else:
                        missing_files.append(cleaned_filename)

                image_extensions = [".jpg", ".png", ".webp"]

                # Check for any remaining images in the folder
                remaining_files = os.listdir(output_path)
                image_files = [
                    f
                    for f in remaining_files
                    if any(f.lower().endswith(ext) for ext in image_extensions)
                ]

                for image_file in image_files:
                    poster_path = os.path.join(
                        target_folder, "poster" + os.path.splitext(image_file)[1]
                    )
                    shutil.move(os.path.join(output_path, image_file), poster_path)

                set_owner_recursive(target_folder, "carn1v0re", "bunk3rGroup")
                clear_output_folder(output_path)

                if moved_files:
                    complete_msg += f"✅ Download completed:<br>{'<br>'.join(moved_files)}<br>Total: {len(moved_files)}<br>"
                if missing_files:
                    complete_msg += f"<br>❓ Download failed:<br>{'<br>'.join(missing_files)}<br>Total: {len(missing_files)}<br>"
                if unavailable_videos:
                    complete_msg += f"<br>❌ There are <strong>{len(unavailable_videos)}</strong> Videos that cant be downloaded."

                my_hook({"status": "complete", "msg": complete_msg})
                current_download = None
                last_percentage = None
                to_download = 0
                done_download = 1
                time.sleep(3)

            return {"success": True, "message": complete_msg}

        except Exception as e:
            current_download = None
            last_percentage = None
            to_download = 0
            done_download = 1
            if any(
                substring in str(e) for substring in ["Video unavailable", "private"]
            ):
                if retrieving:
                    return
                log_message(f"⚠️ INFO ⚠️ Error:<br>{str(e)}", True, True)

            else:
                cookie_error = ""
                if "format cookies file" in str(e):
                    cookie_error = "<br>🍪 Cookies are missing or invalid. Please check your data/cookies.txt file."
                log_message(f"⚠️ INFO ⚠️ Error:<br>{str(e)}{cookie_error}", True, True)
                return {"error": str(e)}


if __name__ == "__main__":
    clear_output_folder(OUTPUT_DIR)
    print("🚀 Starting Server...")
    if IP == "0.0.0.0":
        print(f"🔗 Access the server at http://localhost:{PORT}")
    else:
        print(f"🔗 Access the server at http://{IP}:{PORT}")
    app.run(host=IP, port=PORT, debug=DEBUG)

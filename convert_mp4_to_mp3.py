import os
import subprocess
import questionary

# Hardcoded root path to start from
rootpath = '/Path/To/Your/Folder'  # Change this to your root directory path
if rootpath == '/Path/To/Your/Folder':
    print("\n⚠️ Please change the root path in the script to your desired rootpath! 🔧")
    exit()

def convert_to_mp3(file_path):
    """ Converts .mp4 or .m4a files to .mp3 """
    mp3_file = file_path.rsplit('.', 1)[0] + '.mp3'
    subprocess.run(['ffmpeg', '-i', file_path, '-q:a', '2', '-map', 'a', mp3_file, '-loglevel', 'quiet'])
    print(f'Converted {file_path} to {mp3_file}')

def extract_first_frame(mp4_file):
    """ Extracts the first frame of an MP4 file and saves it as a JPG """
    jpg_file = mp4_file.replace('.mp4', '-poster.jpg')
    subprocess.run(['ffmpeg', '-i', mp4_file, '-vframes', '1', '-q:v', '2', jpg_file, '-loglevel', 'quiet'])
    print(f'Extracted first frame from {mp4_file} to {jpg_file}')

def process_file(file_path):
    """ Processes individual files: Convert to MP3, extract frames for MP4, and delete original """
    if file_path.endswith(('.mp4', '.m4a')):
        convert_to_mp3(file_path)
        if file_path.endswith('.mp4'):
            extract_first_frame(file_path)
        os.remove(file_path)
        print(f'Removed {file_path}')

def process_directory(directory):
    """ Processes all MP4 and M4A files in a directory recursively """
    for root, _, files in os.walk(directory):
        for file in files:
            file_path = os.path.join(root, file)
            process_file(file_path)

def select_folder_or_file(path):
    """ Interactive selection of folders or files """
    items = [item for item in os.listdir(path) if not item.startswith('.')]
    choices = []

    for item in items:
        full_path = os.path.join(path, item)
        if os.path.isdir(full_path):
            choices.append(f"[Folder] {item}")
        elif os.path.isfile(full_path) and item.endswith(('.mp4', '.m4a')):
            choices.append(f"[File] {item}")

    if not choices:
        print("No valid files or folders found.")
        return

    selected_item = questionary.select(
        f"Choose a folder or file from {path}",
        choices=choices
    ).ask()

    selected_path = os.path.join(path, selected_item.split('] ')[1])

    if os.path.isdir(selected_path):
        action = questionary.select(
            f"Options for folder {selected_item.split('] ')[1]}",
            choices=["Enter", "Select"]
        ).ask()

        if action == "Enter":
            select_folder_or_file(selected_path)
        elif action == "Select":
            process_directory(selected_path)
    elif os.path.isfile(selected_path):
        process_file(selected_path)

def main():
    select_folder_or_file(rootpath)

if __name__ == "__main__":
    main()

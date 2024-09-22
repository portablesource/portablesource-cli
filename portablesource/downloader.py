import os
import requests
import subprocess
from tqdm import tqdm
from urllib.parse import urlparse
import winreg
import locale

links = [
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/python.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/ffmpeg.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/git.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe",
]

def get_localized_text(language, key):
    texts = {
        "en": {
            "choose_language": "Choose a language (en/ru): ",
            "which_path": "Select a installation path or enter your reference, default C:\:",
            "error_creating_directory": "Error creating directory!",
        },
        "ru": {
            "choose_language": "Выберите язык (en/ru): ",
            "which_path": "Выберите путь установки, дефолтный C:\ :",
            "error_creating_directory": "Ошибка создания директории!",
        }
    }
    return texts[language].get(key, "")

def get_system_language():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International")
        language = winreg.QueryValueEx(key, "LocaleName")[0]
        winreg.CloseKey(key)
        lang_code = language.split('-')[0].lower()
        return "ru" if lang_code == "ru" else "en"
    except WindowsError:
        lang_code = locale.getdefaultlocale()[0].split('_')[0].lower()
        return "ru" if lang_code == "ru" else "en"

def get_available_drives():
    drives = []
    for drive in range(65, 91):  # ASCII коды от A (65) до Z (90)
        drive_letter = f"{chr(drive)}:\\"
        if os.path.exists(drive_letter):
            drives.append(drive_letter)
    return drives

def get_path_for_install():
    language = get_system_language()
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"

    default_path = "C:\\"
    user_input = input(get_localized_text(language, "which_path") + f" ({default_path}): ").strip()

    install_path = user_input if user_input else default_path

    full_path = os.path.join(install_path, 'portablesource')
    if not os.path.exists(full_path):
        try:
            os.makedirs(full_path)
        except OSError:
            print(get_localized_text(language, "error_creating_directory"))
            return get_path_for_install()

    with open(os.path.join(full_path, 'installed.txt'), 'w') as f:
        f.write('installed')

    return full_path

def get_install_path():
    drives = get_available_drives()
    install_path = None
    for drive in drives:
        possible_path = os.path.join(drive, 'portablesource', 'installed.txt')
        if os.path.exists(possible_path):
            install_path = os.path.dirname(possible_path)
            return install_path
        else:
            install_path = get_path_for_install()
            return install_path
    return install_path

def download_file(url, output_dir='system'):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(urlparse(url).path)
    output_path = os.path.join(output_dir, filename)
    response = requests.get(url, stream=True)
    file_size = int(response.headers.get('content-length', 0))

    with open(output_path, 'wb') as out_file, tqdm(
        desc=filename,
        total=file_size,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as pbar:
        for data in response.iter_content(chunk_size=1024):
            size = out_file.write(data)
            pbar.update(size)
    return output_path

def extract_7z(archive_path, output_dir, seven_zip_path):
    command = [seven_zip_path, 'x', archive_path, f'-o{output_dir}', '-y']
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError as e:
        return False

def download_extract_and_cleanup(links, output_dir='system'):
    required_folders = ['python', 'ffmpeg', 'git']
    missing_folders = [folder for folder in required_folders if not os.path.exists(os.path.join(output_dir, folder))]

    if not missing_folders:
            return

    seven_zip_path = os.path.join(output_dir, '7z.exe')
    if not os.path.exists(seven_zip_path):
        seven_zip_path = download_file(links[-1], output_dir)

    archives_to_extract = []

    for link in links[:-1]:
        folder_name = os.path.splitext(os.path.basename(link))[0]
        if folder_name in missing_folders:
            file_path = download_file(link, output_dir)
            archives_to_extract.append(file_path)

    for archive in archives_to_extract:
        if extract_7z(archive, output_dir, seven_zip_path):
            os.remove(archive)

def download_for_main():
    possible_path_20 = get_install_path()
    system = os.path.join(possible_path_20, "system")
    download_extract_and_cleanup(links, output_dir=system)
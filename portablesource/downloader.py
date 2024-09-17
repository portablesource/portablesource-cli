import os
import urllib3
import subprocess
from tqdm import tqdm
from urllib.parse import urlparse

links = [
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/python.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/ffmpeg.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/git.7z",
    "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe",
]

def download_file(url, output_dir='s'):
    os.makedirs(output_dir, exist_ok=True)
    filename = os.path.basename(urlparse(url).path)
    output_path = os.path.join(output_dir, filename)
    http = urllib3.PoolManager()

    with http.request('HEAD', url, preload_content=False) as response:
            file_size = int(response.headers.get('Content-Length', 0))

    with http.request('GET', url, preload_content=False) as response, open(output_path, 'wb') as out_file:
            with tqdm(total=file_size, unit='B', unit_scale=True, unit_divisor=1024) as pbar:
                for chunk in response.stream(1024):
                    out_file.write(chunk)
                    pbar.update(len(chunk))
    return output_path

def extract_7z(archive_path, output_dir, seven_zip_path):
    archive_name = os.path.splitext(os.path.basename(archive_path))[0]
    extract_dir = os.path.join(output_dir, archive_name)

    command = [seven_zip_path, 'x', archive_path, f'-o{extract_dir}', '-y']

    try:
            subprocess.run(command, check=True)
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

    os.remove(seven_zip_path)

if __name__ == "__main__":
    download_extract_and_cleanup(links)

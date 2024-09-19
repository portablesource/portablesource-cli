import os
import subprocess
import re
import urllib3
import zipfile
import sys
import shutil
import locale
import winreg
from .downloader import get_install_path

git_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'git', 'cmd', 'git.exe')
python = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'python', 'python.exe')
ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'ffmpeg')
git_cmd = git_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'git', 'cmd')
python_scripts = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'python', 'Scripts')

repos = [
    "https://github.com/facefusion/facefusion",
    "https://github.com/KwaiVGI/LivePortrait",
    "https://github.com/lllyasviel/stable-diffusion-webui-forge",
    "https://github.com/comfyanonymous/ComfyUI",
    "https://github.com/hacksider/Deep-Live-Cam",
    "https://github.com/argenspin/Rope-Live",
]

for i, repo in enumerate(repos, 1):
    print(f"{i}. {repo}")

def get_uv_path():
    if sys.platform.startswith('win'):
        scripts_dir = os.path.join(os.path.dirname(python), 'Scripts')
        uv_executable = os.path.join(scripts_dir, "uv.exe")
    else:
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(python)), 'bin')
        uv_executable = os.path.join(scripts_dir, "uv")
    return uv_executable

uv_executable = get_uv_path()

def install_uv():
    if shutil.which("uv") or os.path.exists(uv_executable):
        return uv_executable
    else:
        subprocess.run([python, "-m", "pip", "install", "uv"], check=True)

def get_localized_text(language, key):
    texts = {
        "en": {
            "choose_language": "Choose a language (en/ru): ",
            "select_repo": "Select a repository number or enter your reference:",
            "enter_requirements_filename": "Enter the name of the requirements file (press Enter for 'requirements.txt'): ",
        },
        "ru": {
            "choose_language": "Выберите язык (en/ru): ",
            "select_repo": "Выберите номер репозитория или введите свою ссылку: ",
            "enter_requirements_filename": "Введите имя файла с библиотеками (нажмите Enter для 'requirements.txt'): ",
             
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

def install_from_source(language):
    language = get_system_language()
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"
    choice = input(get_localized_text(language, "select_repo")).strip()

    if choice.isdigit() and 1 <= int(choice) <= len(repos):
        repo_url = repos[int(choice) - 1]
    else:
        repo_url = choice

    repo_name = repo_url.split('/')[-1].replace('.git', '')
    abs_path = get_install_path()
    sources_path = abs_path+"sources"
    repo_home = sources_path+repo_name
    os.makedirs(repo_home, exist_ok=True)

    if not os.path.exists(os.path.join(repo_home)) and not repo_name=="facefusion":
        os.chdir(sources_path)
        subprocess.run([git_exe, "clone", repo_url, repo_name], check=True)

    venv_path = os.path.join(repo_name, "venv")
    if not os.path.exists(venv_path):
        subprocess.run([python, "-m", "venv", venv_path], check=True)

    activate_script = os.path.join(venv_path, "Scripts", "activate.bat")

    requirements_filename = input(get_localized_text(language, "enter_requirements_filename")).strip()
    if not requirements_filename:
        requirements_filename = "requirements.txt"

    requirements_file = os.path.join(repo_name, requirements_filename)

    if repo_name == "facefusion":
        os.chdir(repo_home)
        tmp = repo_home + "tmp"
        os.makedirs(tmp)
        bat_content = '''@echo off
setlocal enabledelayedexpansion
for /d %%i in (tmp\\tmp*,tmp\\pip*) do rd /s /q "%%i" 2>nul || ("%%i" && exit /b 1) & del /q tmp\\tmp* > nul 2>&1 & rd /s /q pip\\cache 2>nul

set "appdata={tmp}"
set "userprofile={tmp}"
set "temp={tmp}"
set "PATH=%PATH%;{git_cmd};{python};{python_scripts};{ffmpeg}"

set "CUDA_MODULE_LOADING=LAZY"

call {activate_script} && {python} updater_facefusion.py
pause

REM by dony
'''

        with open('start_nvidia.bat', 'w') as bat_file:
            bat_file.write(bat_content)
        updater_facefusion_url = "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/updater_facefusion.py"
        updater_facefusion_name = "updater_facefusion.py"
        with http.request('GET', updater_facefusion_url, preload_content=False) as resp, open(updater_facefusion_name, 'wb') as out_file:
            while True:
                data = resp.read(1024)
                if not data:
                    break
                out_file.write(data)

    else:
        os.chdir(repo_home)
        tmp = repo_home + "tmp"
        os.makedirs(tmp)
        app_path = repo_home + "app.py"
        if not os.path.exists(app_path):
            print("not found app.py! please check correctly start_nvidia.bat! maybe he dont work.")
        else:
            bat_content = '''@echo off
setlocal enabledelayedexpansion
for /d %%i in (tmp\\tmp*,tmp\\pip*) do rd /s /q "%%i" 2>nul || ("%%i" && exit /b 1) & del /q tmp\\tmp* > nul 2>&1 & rd /s /q pip\\cache 2>nul

set "appdata={tmp}"
set "userprofile={tmp}"
set "temp={tmp}"
set "PATH=%PATH%;{git_cmd};{python};{python_scripts};{ffmpeg}"

set "CUDA_MODULE_LOADING=LAZY"

call {activate_script} && {python} app.py
pause

REM by dony
'''
            with open('start_nvidia.bat', 'w') as bat_file:
                bat_file.write(bat_content)
    
    if os.path.exists(requirements_file):
        installed_flag = os.path.join(venv_path, ".libraries_installed")
        if not os.path.exists(installed_flag):
            with open(requirements_file, 'r') as f:
                requirements = f.read()
        
        torch_packages = re.findall(r'(torch|torchvision|torchaudio)', requirements)
        cuda_version = re.search(r'\+cu(\d+)', requirements)
        cuda_version = cuda_version.group(1) if cuda_version else None
        requirements = re.sub(r'(insightface).*\n', '', requirements)
        onnx_gpu = re.search(r'onnxruntime-gpu', requirements)
        
        with open(requirements_file, 'w') as f:
            f.write(requirements)

        if torch_packages:
            torch_cmd = f'"{activate_script}" && "{python}" -m {uv_executable} pip install torch==2.4.0 torchvision==0.19.0 torchaudio==2.4.0 --index-url https://download.pytorch.org/whl/{cuda_version}'
            subprocess.run(torch_cmd, shell=True, check=True)

        if onnx_gpu:
            onnx_url = "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/onnxruntime-gpu.zip"
            onnx_zip = os.path.join(repo_name, "onnxruntime-gpu.zip")
            venv_lib_path = os.path.join(repo_name, "venv", "Lib", "site-packages")
            http = urllib3.PoolManager()

            with http.request('GET', onnx_url, preload_content=False) as resp, open(onnx_zip, 'wb') as out_file:
                while True:
                    data = resp.read(1024)
                    if not data:
                        break
                    out_file.write(data)

                    with zipfile.ZipFile(onnx_zip, 'r') as zip_ref:
                        zip_ref.extractall(venv_lib_path)
                    os.remove(onnx_zip)
        
        install_cmd = f'"{activate_script}" && "{python}" -m {uv_executable} pip install -r "{requirements_file}"'
        subprocess.run(install_cmd, shell=True, check=True)
        insightface_cmd = f'"{activate_script}" && "{python}" -m {uv_executable} pip install https://huggingface.co/hanamizuki-ai/insightface-releases/resolve/main/insightface-0.7.3-cp310-cp310-win_amd64.whl"'
        subprocess.run(insightface_cmd, shell=True, check=True)
        open(installed_flag, 'w').close()

    if repo_name == "Deep-Live-Cam":
        models_dir = os.path.join(repo_name, "models")
        os.makedirs(models_dir, exist_ok=True)
        model_to_download_urls = [
            "https://huggingface.co/hacksider/deep-live-cam/resolve/main/GFPGANv1.4.pth",
            "https://github.com/facefusion/facefusion-assets/releases/download/models/inswapper_128_fp16.onnx"
        ]
        for url in model_to_download_urls:
            filename = url.split('/')[-1]
            local_path = os.path.join(models_dir, filename)
            if not os.path.exists(local_path):
                http = urllib3.PoolManager()
                with http.request('GET', url, preload_content=False) as resp, open(local_path, 'wb') as out_file:
                    while True:
                        data = resp.read(1024)
                        if not data:
                            break
                        out_file.write(data)

    if repo_name == "facefusion":
        ff_path = sources_path+"facefusion"
        if not os.path.exists(ff_path):
            os.mkdir(ff_path)
            os.chdir(ff_path)
            subprocess.run([git_exe, "clone", repo_url, 'master', '-b', 'master'], check=True)
            subprocess.run([git_exe, "clone", repo_url, 'next', '-b', 'next'], check=True)

def installed():
    language = get_system_language()
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"
    install_from_source()
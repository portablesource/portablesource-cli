import os
import subprocess

git_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'git', 'cmd', 'git.exe')
python = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'python', 'python.exe')
ffmpeg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'system', 'ffmpeg')

repos = [
    "https://github.com/username/repo1.git",
    "https://github.com/username/repo2.git",
    "https://github.com/username/repo3.git",
    "https://github.com/username/repo4.git",
    "https://github.com/username/repo5.git"
]

for i, repo in enumerate(repos, 1):
    print(f"{i}. {repo}")

def get_localized_text(language, key):
    texts = {
        "en": {
              "select_repo": "Select a repository number or enter your reference:",
              "enter_requirements_filename": "Enter the name of the requirements file (press Enter for 'requirements.txt'): ",
        },
        "ru": {
             "select_repo": "Выберите номер репозитория или введите свою ссылку: ",
             "enter_requirements_filename": "Введите имя файла с библиотеками (нажмите Enter для 'requirements.txt'): ",
        }
    }
    return texts[language].get(key, "")

def read_language_from_file(file_path):
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as file:
            language = file.read().strip().lower()
            if language in ["en", "ru"]:
                return language
    return None

def write_language_to_file(file_path, language):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write(language)

def install_from_source(language):
    choice = input(get_localized_text(language, "select_repo")).strip()

    if choice.isdigit() and 1 <= int(choice) <= len(repos):
        repo_url = repos[int(choice) - 1]
    else:
        repo_url = choice

    repo_name = repo_url.split('/')[-1].replace('.git', '')
    os.makedirs(repo_name, exist_ok=True)

    if not os.path.exists(os.path.join(repo_name, '.git')):
        subprocess.run([git_exe, "clone", repo_url, repo_name], check=True)

    venv_path = os.path.join(repo_name, "venv")
    if not os.path.exists(venv_path):
        subprocess.run([python, "-m", "venv", venv_path], check=True)

    activate_script = os.path.join(venv_path, "Scripts", "activate.bat")

    requirements_filename = input(get_localized_text(language, "enter_requirements_filename")).strip()
    if not requirements_filename:
        requirements_filename = "requirements.txt"

    requirements_file = os.path.join(repo_name, requirements_filename)

    if os.path.exists(requirements_file):
        installed_flag = os.path.join(venv_path, ".libraries_installed")
        if not os.path.exists(installed_flag):
            install_cmd = f'"{activate_script}" && "{python}" -m pip install -r "{requirements_file}"'
            subprocess.run(install_cmd, shell=True, check=True)
            open(installed_flag, 'w').close()

def installed():
    #print(
#    """
#    .______   ____    ____
#    |   _  \  \   \  /   /
#    |  |_)  |  \   \/   /
#    |   _  <    \_    _/
#    |  |_)  |     |  |
#    |______/      |__|
#
    #.__   __.  _______  __    __  .______        ______    _______   ______   .__   __.  __    __
    #|  \ |  | |   ____||  |  |  | |   _  \      /  __  \  |       \ /  __  \  |  \ |  | |  |  |  |
    #|   \|  | |  |__   |  |  |  | |  |_)  |    |  |  |  | |  .--.  |  |  |  | |   \|  | |  |  |  |
    #|  . `  | |   __|  |  |  |  | |      /     |  |  |  | |  |  |  |  |  |  | |  . `  | |  |  |  |
    #|  |\   | |  |____ |  `--'  | |  |\  \----.|  `--'  | |  '--'  |  `--'  | |  |\   | |  `--'  |
    #|__| \__| |_______| \______/  | _| `._____| \______/  |_______/ \______/  |__| \__|  \______/
    #"""
    #)
    file_path = 'lang.txt'
    language = read_language_from_file(file_path)
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"
        write_language_to_file(file_path, language)
    install_from_source()


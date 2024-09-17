import os
import subprocess
import sys

git = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'system', 'git', 'cmd', 'git.exe')
ff_obs = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facefusion')
python = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'system', 'python', 'python.exe')

files = [
    ff_obs + "\\next" + "\\facefusion\\content_analyser.py",
    ff_obs + "\\master" + "\\facefusion\\content_analyser.py",
]

def get_uv_path():
    if sys.platform.startswith('win'):
        scripts_dir = os.path.join(os.path.dirname(python), 'Scripts')
        uv_executable = os.path.join(scripts_dir, "uv.exe")
    else:
        scripts_dir = os.path.join(os.path.dirname(os.path.dirname(python)), 'bin')
        uv_executable = os.path.join(scripts_dir, "uv")
    return uv_executable

uv_executable = get_uv_path()

def gradio_version(branch):
    if branch=="master":
        subprocess.run([python], ["-m", {uv_executable}, "pip", "install", "gradio==3.50.2"])
    if branch=="next":
        subprocess.run([python], ["-m", {uv_executable}, "pip", "install", "gradio==4.40.0"])
    
def process_file_master(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()
    with open(file_path, 'w') as f:
        inside_function = False
        for line in lines:
            if 'def analyse_frame(' in line:
                inside_function = True
                f.write(line)
                f.write('    return False\n') 
            elif inside_function:
                if line.startswith('def ') or line.strip() == '':
                    inside_function = False
                    f.write(line)
            else:
                f.write(line)

def process_file_next(file_path):
    with open(file_path, 'r') as f:
        lines = f.readlines()

    with open(file_path, 'w') as f:
        inside_function = False
        current_function = None

        for line in lines:
            if 'def analyse_frame(' in line:
                inside_function = True
                current_function = 'analyse_frame'
                f.write(line)
                f.write('    return False\n\n')
            elif 'def forward(' in line:
                inside_function = True
                current_function = 'forward'
                f.write(line)
                f.write('    return 0\n\n')
            elif inside_function:
                if line.startswith('def '):
                    inside_function = False
                    current_function = None
                    f.write(line)
                elif line.strip() == '':
                    continue
            else:
                f.write(line)

def process_files(files):
    for file_path in files:
        if '\\next\\' in file_path:
            process_file_next(file_path)
        elif '\\master\\' in file_path:
            process_file_master(file_path)

def run_git_command(args):
    subprocess.run([git] + args, check=True)

def update_branch(branch):
    if branch=="master":
        os.chdir(ff_obs + "\\master")
    if branch=="next":
        os.chdir(ff_obs + "\\next")
    run_git_command(['reset', '--hard'])
    run_git_command(['checkout', branch])
    run_git_command(['pull', 'origin', branch, '--rebase'])

def start_ff(branch, webcam_mode=False):
    if branch=="master":
        path_to_branch = os.path.join(ff_obs + "\\master")
    if branch=="next":
        path_to_branch = os.path.join(ff_obs + "\\next")
    
    if branch=="next":
        py_files = [f for f in os.listdir(path_to_branch) if f.endswith('.py')]
        if len(py_files) != 2:
            return
        second_file = [f for f in py_files if f != 'installer.py'][0]
    if branch=="master":
        second_file = "run.py"

    if webcam_mode:
        if branch=="next":
            args_next = ["run"]
            args = ["--open-browser", "--ui-layouts", "webcam"]
            args = args_next + args
        if branch=="master":
            args = ["--open-browser", "--ui-layouts", "webcam"]
    else:
        if branch=="next":
            args_next = ["run"]
            args = ["--open-browser"]
            args = args_next+args
        if branch=="master":
            args = ["--open-browser"]

        subprocess.run([python, os.path.join(path_to_branch, second_file)] + args)

def get_localized_text(language, key):
    texts = {
        "en": {
            "choose_action": "Choose an action:",
            "update_master": "1. Update to the master branch and start it",
            "update_next": "2. Update to the next branch and start it",
            "start_facefusion": "3. Start facefusion",
            "enter_choice": "Enter the number of the action: ",
            "invalid_choice": "Invalid choice, please try again.",
            "choose_language": "Choose a language (en/ru): ",
            "enable_webcam": "Enable webcam mode? (Y/N): ",
        },
        "ru": {
            "choose_action": "Выберите действие:",
            "update_master": "1. Обновить до обычной ветки и запустить ее (master)",
            "update_next": "2. Обновить до ветки next и запустить ее",
            "start_facefusion": "3. Запустить facefusion",
            "enter_choice": "Введите номер действия: ",
            "invalid_choice": "Неверный выбор, попробуйте снова.",
            "choose_language": "Выберите язык (en/ru): ",
            "enable_webcam": "Включить режим вебкамеры? (Y/N): ",
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

def ask_webcam_mode(language):
    webcam_choice = input(get_localized_text(language, "enable_webcam")).strip().lower()
    return webcam_choice == 'y'

def facefusion():
    file_path = 'lang.txt'
    language = read_language_from_file(file_path)
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"
        write_language_to_file(file_path, language)

    while True:
        print(get_localized_text(language, "choose_action"))
        print(get_localized_text(language, "update_master"))
        print(get_localized_text(language, "update_next"))

        choice = input(get_localized_text(language, "enter_choice")).strip()

        if choice == '1':
            update_branch('master')
            process_files(files)
            gradio_version('master')
            webcam_mode = ask_webcam_mode(language)
            start_ff("master", webcam_mode)
        elif choice == '2':
            update_branch('next')
            process_files(files)
            gradio_version('next')
            webcam_mode = ask_webcam_mode(language)
            start_ff("next", webcam_mode)
        else:
            print(get_localized_text(language, "invalid_choice"))

if __name__ == "__main__":
    facefusion()
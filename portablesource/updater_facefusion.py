import os
import subprocess
import sys
import locale
import winreg

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

abs_path = get_install_path()
git = os.path.join(abs_path, "system", "git", "cmd", "git.exe")
ff_obs = os.path.join(abs_path, "sources", "facefusion")
master_path = os.path.join(ff_obs, "master")
python = os.path.join(abs_path, "sources", "facefusion", "venv", "Scripts", "python.exe")

def run_git_command(args):
    subprocess.run([git] + args, check=True)

def update_branch(branch):
    os.chdir(master_path)
    run_git_command(['reset', '--hard'])
    run_git_command(['checkout', 'master'])
    run_git_command(['pull', 'origin', 'master', '--rebase'])

def start_ff(branch, webcam_mode=False):
    path_to_branch = master_path
    second_file = os.path.join(path_to_branch, "facefusion.py")

    if webcam_mode:
        args = ["run", "--open-browser", "--ui-layouts", "webcam"]
    else:
        args = ["run"]

    cmd = f'"{python}" "{second_file}" {" ".join(args)}'
    
    subprocess.run(cmd, shell=True, check=True)


def get_localized_text(language, key):
    texts = {
        "en": {
            "choose_action": "Choose an action:",
            "update_master": "1. Update to the master branch and start it",
            "enter_choice": "Enter the number of the action: ",
            "invalid_choice": "Invalid choice, please try again.",
            "enable_webcam": "Enable webcam mode? (Y/N): ",
        },
        "ru": {
            "choose_action": "Выберите действие:",
            "update_master": "1. Обновить до обычной ветки и запустить ее (master)",
            "enter_choice": "Введите номер действия: ",
            "invalid_choice": "Неверный выбор, попробуйте снова.",
            "enable_webcam": "Включить режим вебкамеры? (Y/N): ",
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

def ask_webcam_mode(language):
    webcam_choice = input(get_localized_text(language, "enable_webcam")).strip().lower()
    return webcam_choice == 'n'

def facefusion():
    language = get_system_language()
    if not language:
        language = input(get_localized_text("en", "choose_language")).strip().lower()
        if language not in ["en", "ru"]:
            language = "en"
    while True:
        print(get_localized_text(language, "choose_action"))
        print(get_localized_text(language, "update_master"))
        choice = input(get_localized_text(language, "enter_choice")).strip()

        if choice == '1':
            update_branch('master')
            webcam_mode = ask_webcam_mode(language)
            start_ff("master", webcam_mode)
        else:
            print(get_localized_text(language, "invalid_choice"))

if __name__ == "__main__":
    facefusion()
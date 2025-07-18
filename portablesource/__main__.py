#!/usr/bin/env python3
"""
PortableSource - главный файл запуска
Эмулирует поведение скомпилированного .exe файла
"""

import os
import sys
import logging
import argparse
from pathlib import Path
from typing import Optional
import winreg

# Относительные импорты
from portablesource.get_gpu import GPUDetector
from portablesource.config import ConfigManager, SERVER_DOMAIN
from portablesource.envs_manager import EnvironmentManager, EnvironmentSpec
from portablesource.repository_installer import RepositoryInstaller

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

class PortableSourceApp:
    """Главное приложение PortableSource"""
    
    def __init__(self):
        self.install_path: Optional[Path] = None
        self.config_manager: Optional[ConfigManager] = None
        self.environment_manager: Optional[EnvironmentManager] = None
        self.repository_installer: Optional[RepositoryInstaller] = None
        self.gpu_detector = GPUDetector()
        
    def initialize(self, install_path: Optional[str] = None):
        """Инициализация приложения"""
        logger.info("Инициализация PortableSource...")
        
        # Определяем путь установки
        if install_path:
            self.install_path = Path(install_path).resolve()
            # Сохраняем переданный путь в реестр
            self._save_install_path_to_registry(self.install_path)
        else:
            # Запрашиваем путь у пользователя
            self.install_path = self._get_installation_path()
        
        logger.info(f"Путь установки: {self.install_path}")
        
        # Создаем структуру папок
        self._create_directory_structure()
        
        # Инициализируем менеджер окружений
        self._initialize_environment_manager()
        
        # Проверяем целостность окружения при запуске
        self._check_environment_on_startup()
        
        # Инициализируем конфигурацию
        self._initialize_config()
        
        # Инициализируем установщик репозиториев
        self._initialize_repository_installer()
        
        logger.info("Инициализация завершена")
    
    def _save_install_path_to_registry(self, install_path: Path) -> bool:
        """Сохраняет путь установки в реестр Windows"""
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
            winreg.SetValueEx(key, "InstallPath", 0, winreg.REG_SZ, str(install_path))
            winreg.CloseKey(key)
            logger.info(f"Путь установки сохранен в реестр: {install_path}")
            return True
        except Exception as e:
            logger.warning(f"Не удалось сохранить путь в реестр: {e}")
            return False
    
    def _load_install_path_from_registry(self) -> Optional[Path]:
        """Загружает путь установки из реестра Windows"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
            install_path_str, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            
            install_path = Path(install_path_str)
            logger.info(f"Путь установки загружен из реестра: {install_path}")
            
            # Возвращаем путь из реестра без проверки существования
            # Папка может не существовать, но это нормально - она будет создана
            return install_path
        except FileNotFoundError:
            logger.info("Путь установки не найден в реестре")
            return None
        except Exception as e:
            logger.warning(f"Ошибка при загрузке пути из реестра: {e}")
            return None

    def _get_installation_path(self) -> Path:
        """Запрашивает путь установки у пользователя"""
        # Сначала пытаемся загрузить из реестра
        registry_path = self._load_install_path_from_registry()
        
        if registry_path:
            # Если путь найден в реестре и существует, используем его автоматически
            logger.info(f"Используется путь из реестра: {registry_path}")
            return registry_path
        
        # Если пути нет в реестре, запрашиваем у пользователя
        print("\n" + "="*60)
        print("НАСТРОЙКА ПУТИ УСТАНОВКИ PORTABLESOURCE")
        print("="*60)
        
        # Предлагаем варианты
        default_path = Path("C:/PortableSource")
        
        print(f"\nПо умолчанию будет использован путь: {default_path}")
        print("\nВы можете:")
        print("1. Нажать Enter для использования пути по умолчанию")
        print("2. Ввести свой путь установки")
        
        user_input = input("\nВведите путь установки (или Enter для значения по умолчанию): ").strip()
        
        if not user_input:
            chosen_path = default_path
        else:
            chosen_path = self._validate_and_get_path(user_input)
        
        print(f"\nВыбран путь установки: {chosen_path}")
        
        # Проверяем, существует ли путь и не пустой ли он
        if chosen_path.exists() and any(chosen_path.iterdir()):
            print(f"\nВнимание: Папка {chosen_path} уже существует и не пуста.")
            while True:
                confirm = input("Продолжить? (y/n): ").strip().lower()
                if confirm in ['y', 'yes', 'д', 'да']:
                    break
                elif confirm in ['n', 'no', 'н', 'нет']:
                    print("Установка отменена.")
                    sys.exit(1)
                else:
                    print("Пожалуйста, введите 'y' или 'n'")
        
        # Сохраняем путь в реестр
        self._save_install_path_to_registry(chosen_path)
        
        return chosen_path
    
    def _validate_and_get_path(self, user_input: str) -> Path:
        """Валидирует и возвращает путь от пользователя"""
        while True:
            try:
                chosen_path = Path(user_input).resolve()
                
                # Проверяем, что путь валидный
                if chosen_path.is_absolute():
                    return chosen_path
                else:
                    print(f"Ошибка: Путь должен быть абсолютным. Попробуйте еще раз.")
                    user_input = input("Введите корректный путь: ").strip()
                    continue
                    
            except Exception as e:
                print(f"Ошибка: Неверный путь '{user_input}'. Попробуйте еще раз.")
                user_input = input("Введите корректный путь: ").strip()
                continue
    
    def _create_directory_structure(self):
        """Создает структуру папок"""
        if not self.install_path:
            raise ValueError("Install path is not set")
            
        directories = [
            self.install_path,
            self.install_path / "miniconda",       # Miniconda
            self.install_path / "repos",           # Репозитории
            self.install_path / "envs",            # Окружения conda
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            logger.info(f"Создана папка: {directory}")
    
    def _initialize_environment_manager(self):
        """Инициализация менеджера окружений"""
        if not self.install_path:
            raise ValueError("Install path is not set")
        self.environment_manager = EnvironmentManager(self.install_path)
    
    def _check_environment_on_startup(self):
        """Проверяет целостность окружения при запуске и переустанавливает при необходимости"""
        if not self.environment_manager:
            return
        
        # Проверяем наличие Miniconda
        if not self.environment_manager.ensure_miniconda():
            logger.warning("Miniconda не найдена или повреждена")
            return
        
        # Проверяем целостность базового окружения
        if not self.install_path is None:
            conda_env_path = self.install_path / "miniconda" / "envs" / "portablesource"
        if conda_env_path.exists():
            if not self.environment_manager.check_base_environment_integrity():
                logger.warning("Базовое окружение повреждено, выполняется автоматическая переустановка...")
                if self.environment_manager.create_base_environment():
                    logger.info("✅ Базовое окружение успешно переустановлено")
                else:
                    logger.error("❌ Не удалось переустановить базовое окружение")
            else:
                logger.info("✅ Базовое окружение работает корректно")
        else:
            logger.info("Базовое окружение не найдено (будет создано при необходимости)")
    
    def _initialize_config(self):
        """Инициализация конфигурации"""
        if not self.install_path:
            raise ValueError("Install path is not set")
            
        # Для новой архитектуры конфигурация упрощена
        # Передаем правильный путь для конфигурации
        config_path = self.install_path / "portablesource_config.json"
        self.config_manager = ConfigManager(config_path)
        
        # Настройка пути установки (должно быть до configure_gpu)
        self.config_manager.configure_install_path(str(self.install_path))
        
        # Автоматическое определение GPU
        gpu_info = self.gpu_detector.get_gpu_info()
        if gpu_info:
            primary_gpu = gpu_info[0]
            logger.info(f"Обнаружен GPU: {primary_gpu.name}")
            self.config_manager.configure_gpu(primary_gpu.name)
        else:
            logger.warning("GPU не обнаружен, используется CPU режим")
        
        # Не сохраняем конфигурацию - она генерируется динамически
    
    def _initialize_repository_installer(self):
        """Инициализация установщика репозиториев"""
        self.repository_installer = RepositoryInstaller(
            config_manager=self.config_manager,
            server_url=f"http://{SERVER_DOMAIN}"
        )
    
    def check_miniconda_availability(self) -> bool:
        """Проверяет доступность Miniconda"""
        if not self.install_path:
            return False
        
        conda_exe = self.install_path / "miniconda" / "Scripts" / "conda.exe"
        return conda_exe.exists()
    
    def setup_environment(self):
        """Настройка окружения (Miniconda + базовое окружение)"""
        logger.info("Настройка окружения...")
        
        if not self.environment_manager:
            logger.error("Менеджер окружений не инициализирован")
            return False
        
        # Устанавливаем Miniconda
        if not self.environment_manager.ensure_miniconda():
            logger.error("Ошибка установки Miniconda")
            return False
        
        # Создаем базовое окружение (с проверкой целостности)
        if not self.environment_manager.create_base_environment():
            logger.error("Ошибка создания базового окружения")
            return False
        
        # Дополнительная проверка целостности после создания
        if not self.environment_manager.check_base_environment_integrity():
            logger.error("Базовое окружение создано, но проверка целостности не пройдена")
            return False
        
        logger.info("Окружение настроено успешно")
        return True
    
    def install_repository(self, repo_url_or_name: str) -> bool:
        """Установка репозитория"""
        logger.info(f"Установка репозитория: {repo_url_or_name}")
        
        if not self.repository_installer:
            logger.error("Установщик репозиториев не инициализирован")
            return False
        
        if not self.environment_manager:
            logger.error("Менеджер окружений не инициализирован")
            return False
        
        # Путь для установки репозитория
        if not self.install_path:
            logger.error("Install path is not set")
            return False
            
        repo_install_path = self.install_path / "repos"
        
        # Устанавливаем репозиторий
        success = self.repository_installer.install_repository(
            repo_url_or_name, 
            str(repo_install_path)
        )
        
        if success:
            # Создаем окружение для репозитория
            repo_name = self._extract_repo_name(repo_url_or_name)
            repo_path = repo_install_path / repo_name
            env_spec = EnvironmentSpec(name=repo_name)
            
            if self.environment_manager.create_repository_environment(repo_name, env_spec):
                logger.info(f"Окружение для {repo_name} создано")
                
                # Находим главный файл
                main_file = self._find_main_file(repo_path, repo_name, repo_url_or_name)
                if main_file:
                    # Получаем информацию о репозитории из базы данных
                    full_repo_info = self.repository_installer._get_repository_info(repo_name)
                    program_args = full_repo_info.get('program_args', '') if full_repo_info else ''
                    
                    # Создаем батник запуска в папке репозитория
                    repo_info = {
                        'main_file': main_file,
                        'url': repo_url_or_name,
                        'program_args': program_args
                    }
                    success = self.repository_installer._generate_startup_script(repo_path, repo_info)
                    if success:
                        logger.info(f"Создан скрипт запуска для {repo_name}")
                    else:
                        logger.warning(f"Не удалось создать скрипт запуска для {repo_name}")
                else:
                    logger.warning(f"Не удалось найти главный файл для {repo_name}")
            else:
                logger.warning(f"Не удалось создать окружение для {repo_name}")
        
        return success
    
    def update_repository(self, repo_name: str) -> bool:
        """Обновление репозитория"""
        logger.info(f"Обновление репозитория: {repo_name}")
        
        if not self.repository_installer:
            logger.error("Установщик репозиториев не инициализирован")
            return False
        
        # Путь к репозиторию
        if not self.install_path:
            logger.error("Install path is not set")
            return False
            
        repo_install_path = self.install_path / "repos"
        repo_path = repo_install_path / repo_name
        
        # Проверяем, существует ли репозиторий
        if not repo_path.exists():
            logger.error(f"Репозиторий {repo_name} не найден в {repo_path}")
            logger.info("Доступные репозитории:")
            repos = self.list_installed_repositories()
            for repo in repos:
                logger.info(f"  - {repo['name']}")
            return False
        
        # Проверяем, является ли это git репозиторием
        if not (repo_path / ".git").exists():
            logger.error(f"Папка {repo_path} не является git репозиторием")
            return False
        
        # Обновляем репозиторий с помощью repository_installer
        success = self.repository_installer._update_repository_with_fixes(
            self.repository_installer._get_git_executable(), 
            repo_path
        )
        
        if success:
            logger.info(f"✅ Репозиторий {repo_name} успешно обновлен")
        else:
            logger.error(f"❌ Не удалось обновить репозиторий {repo_name}")
        
        return success
    
    def _extract_repo_name(self, repo_url_or_name: str) -> str:
        """Извлекает имя репозитория из URL или названия"""
        if "/" in repo_url_or_name:
            return repo_url_or_name.split("/")[-1].replace(".git", "")
        return repo_url_or_name
    
    def _find_main_file(self, repo_path, repo_name, repo_url) -> str:
        """Находит главный файл репозитория"""
        # Используем MainFileFinder из repository_installer
        from .repository_installer import MainFileFinder, ServerAPIClient
        
        server_client = ServerAPIClient()
        main_file_finder = MainFileFinder(server_client)
        
        main_file = main_file_finder.find_main_file(repo_name, repo_path, repo_url)
        
        # Если не найден, используем fallback
        if not main_file:
            common_names = ["main.py", "app.py", "run.py", "start.py"]
            for name in common_names:
                if (repo_path / name).exists():
                    main_file = name
                    break
        
        return str(main_file)
    
    def list_installed_repositories(self):
        """Список установленных репозиториев"""
        if not self.install_path:
            logger.error("Install path is not set")
            return []
            
        repos_path = self.install_path / "repos"
        if not repos_path.exists():
            logger.info("Папка репозиториев не найдена")
            return []
        
        repos = []
        for item in repos_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Проверяем, есть ли батник запуска
                bat_file = item / f"start_{item.name}.bat"
                sh_file = item / f"start_{item.name}.sh"
                has_launcher = bat_file.exists() or sh_file.exists()
                
                repo_info = {
                    'name': item.name,
                    'path': str(item),
                    'has_launcher': has_launcher
                }
                repos.append(repo_info)
        
        logger.info(f"Найдено репозиториев: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"  - {repo['name']} {launcher_status}")
        
        return repos
    
    def setup_registry(self):
        """Регистрирует путь установки в реестре Windows"""
        if not self.install_path:
            logger.error("Путь установки не определен")
            return False
        
        logger.info("Регистрация пути установки в реестре Windows...")
        
        success = self._save_install_path_to_registry(self.install_path)
        
        if success:
            logger.info("✅ Путь установки успешно зарегистрирован в реестре")
            logger.info(f"Путь: {self.install_path}")
            logger.info("Теперь PortableSource будет автоматически использовать этот путь")
        else:
            logger.error("❌ Не удалось зарегистрировать путь в реестре")
        
        return success
    
    def change_installation_path(self):
        """Изменяет путь установки"""
        print("\n" + "="*60)
        print("ИЗМЕНЕНИЕ ПУТИ УСТАНОВКИ PORTABLESOURCE")
        print("="*60)
        
        # Показываем текущий путь
        current_path = self._load_install_path_from_registry()
        if current_path:
            print(f"\nТекущий путь установки: {current_path}")
        else:
            print("\nТекущий путь установки не найден в реестре")
        
        # Предлагаем варианты
        default_path = Path("C:/PortableSource")
        
        print(f"\nПо умолчанию будет использован путь: {default_path}")
        print("\nВы можете:")
        print("1. Нажать Enter для использования пути по умолчанию")
        print("2. Ввести свой путь установки")
        
        user_input = input("\nВведите новый путь установки (или Enter для значения по умолчанию): ").strip()
        
        if not user_input:
            new_path = default_path
        else:
            new_path = self._validate_and_get_path(user_input)
        
        print(f"\nНовый путь установки: {new_path}")
        
        # Проверяем, существует ли путь и не пустой ли он
        if new_path.exists() and any(new_path.iterdir()):
            print(f"\nВнимание: Папка {new_path} уже существует и не пуста.")
            while True:
                confirm = input("Продолжить? (y/n): ").strip().lower()
                if confirm in ['y', 'yes', 'д', 'да']:
                    break
                elif confirm in ['n', 'no', 'н', 'нет']:
                    print("Изменение пути отменено.")
                    return False
                else:
                    print("Пожалуйста, введите 'y' или 'n'")
        
        # Сохраняем новый путь в реестр
        success = self._save_install_path_to_registry(new_path)
        
        if success:
            logger.info("✅ Путь установки успешно изменен")
            logger.info(f"Новый путь: {new_path}")
            logger.info("Перезапустите PortableSource для применения изменений")
            
            # Обновляем текущий путь в приложении
            self.install_path = new_path
        else:
            logger.error("❌ Не удалось сохранить новый путь в реестре")
        
        return success
    
    def show_system_info(self):
        """Показать информацию о системе"""
        logger.info("PortableSource - Информация о системе:")
        logger.info(f"  - Путь установки: {self.install_path}")
        logger.info(f"  - Операционная система: {self.gpu_detector.system}")
        
        # Структура папок
        logger.info("  - Структура папок:")
        logger.info(f"    * {self.install_path}/miniconda")
        logger.info(f"    * {self.install_path}/repos")
        logger.info(f"    * {self.install_path}/envs")
        
        gpu_info = self.gpu_detector.get_gpu_info()
        if gpu_info:
            logger.info(f"  - GPU: {gpu_info[0].name}")
            logger.info(f"  - Тип GPU: {gpu_info[0].gpu_type.value}")
            if gpu_info[0].cuda_version:
                logger.info(f"  - CUDA версия: {gpu_info[0].cuda_version.value}")
        
        # Статус Miniconda
        miniconda_status = "Установлена" if self.check_miniconda_availability() else "Не установлена"
        logger.info(f"  - Miniconda: {miniconda_status}")
        
        # Conda окружения (общие инструменты)
        if self.environment_manager and self.check_miniconda_availability():
            try:
                import json
                result = self.environment_manager.run_conda_command(["env", "list", "--json"])
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    conda_envs = []
                    for env_path in data.get("envs", []):
                        env_name = Path(env_path).name
                        conda_envs.append(env_name)
                    logger.info(f"  - Conda окружения (общие инструменты): {len(conda_envs)}")
                    for env in conda_envs:
                        logger.info(f"    * {env}")
            except Exception as e:
                logger.warning(f"Не удалось получить список conda окружений: {e}")
        
        # Venv окружения (специфичные для репозиториев)
        if self.environment_manager:
            venv_envs = self.environment_manager.list_environments()
            logger.info(f"  - Venv окружения (для репозиториев): {len(venv_envs)}")
            for env in venv_envs:
                logger.info(f"    * {env}")
        
        # Статус репозиториев
        repos = self.list_installed_repositories()
        logger.info(f"  - Установленных репозиториев: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"    * {repo['name']} {launcher_status}")

def main():
    """Главная функция"""
    parser = argparse.ArgumentParser(description="PortableSource - Portable AI/ML Environment")
    parser.add_argument("--install-path", type=str, help="Путь для установки")
    parser.add_argument("--setup-env", action="store_true", help="Настроить окружение (Miniconda)")
    parser.add_argument("--setup-reg", action="store_true", help="Зарегистрировать путь установки в реестре")
    parser.add_argument("--change-path", action="store_true", help="Изменить путь установки")
    parser.add_argument("--install-repo", type=str, help="Установить репозиторий")
    parser.add_argument("--update-repo", type=str, help="Обновить репозиторий")
    parser.add_argument("--list-repos", action="store_true", help="Показать установленные репозитории")
    parser.add_argument("--system-info", action="store_true", help="Показать информацию о системе")
    
    args = parser.parse_args()
    
    # Создаем приложение
    app = PortableSourceApp()
    
    # Для команды изменения пути не нужна полная инициализация
    if args.change_path:
        app.change_installation_path()
        return
    
    # Инициализируем для остальных команд
    app.initialize(args.install_path)
    
    # Выполняем команды
    if args.setup_env:
        app.setup_environment()
    
    if args.setup_reg:
        app.setup_registry()
    
    if args.install_repo:
        app.install_repository(args.install_repo)
    
    if args.update_repo:
        app.update_repository(args.update_repo)
    
    if args.list_repos:
        app.list_installed_repositories()
    
    if args.system_info:
        app.show_system_info()
    
    # Если нет аргументов, показываем справку
    if len(sys.argv) == 1:
        app.show_system_info()
        print("\n" + "="*50)
        print("Доступные команды:")
        print("  --setup-env             Настроить окружение")
        print("  --setup-reg             Зарегистрировать путь в реестре")
        print("  --change-path           Изменить путь установки")
        print("  --install-repo <url>    Установить репозиторий")
        print("  --update-repo <name>    Обновить репозиторий")
        print("  --list-repos            Показать репозитории")
        print("  --system-info           Информация о системе")
        print("  --install-path <path>   Путь установки")
        print("="*50)

if __name__ == "__main__":
    main()
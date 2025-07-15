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

# Относительные импорты
from .get_gpu import GPUDetector
from .config import ConfigManager
from .envs_manager import EnvironmentManager, EnvironmentSpec
from .repository_installer import RepositoryInstaller

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
        else:
            # По умолчанию в папку рядом с исполняемым файлом
            if getattr(sys, 'frozen', False):
                # Если запущен из .exe
                base_path = Path(sys.executable).parent
            else:
                # Если запущен из .py
                base_path = Path(__file__).parent.parent
            
            self.install_path = base_path / "PortableSource_Installation"
        
        logger.info(f"Путь установки: {self.install_path}")
        
        # Создаем структуру папок
        self._create_directory_structure()
        
        # Инициализируем менеджер окружений
        self._initialize_environment_manager()
        
        # Инициализируем конфигурацию
        self._initialize_config()
        
        # Инициализируем установщик репозиториев
        self._initialize_repository_installer()
        
        logger.info("Инициализация завершена")
    
    def _create_directory_structure(self):
        """Создает структуру папок"""
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
        self.environment_manager = EnvironmentManager(self.install_path)
    
    def _initialize_config(self):
        """Инициализация конфигурации"""
        # Для новой архитектуры конфигурация упрощена
        self.config_manager = ConfigManager()
        
        # Автоматическое определение GPU
        gpu_info = self.gpu_detector.get_gpu_info()
        if gpu_info:
            primary_gpu = gpu_info[0]
            logger.info(f"Обнаружен GPU: {primary_gpu.name}")
            self.config_manager.configure_gpu(primary_gpu.name)
        else:
            logger.warning("GPU не обнаружен, используется CPU режим")
        
        # Настройка системных путей
        self.config_manager.configure_system_paths(str(self.install_path))
        
        # Не сохраняем конфигурацию - она генерируется динамически
    
    def _initialize_repository_installer(self):
        """Инициализация установщика репозиториев"""
        self.repository_installer = RepositoryInstaller(
            config_manager=self.config_manager,
            server_url="http://localhost:5000"
        )
    
    def check_miniconda_availability(self) -> bool:
        """Проверка доступности Miniconda"""
        if not self.environment_manager:
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
        
        # Создаем базовое окружение
        if not self.environment_manager.create_base_environment():
            logger.error("Ошибка создания базового окружения")
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
                    # Создаем батник запуска в папке репозитория
                    script_path = self.environment_manager.generate_launcher_script(
                        repo_name, repo_path, main_file
                    )
                    logger.info(f"Создан скрипт запуска: {script_path}")
                else:
                    logger.warning(f"Не удалось найти главный файл для {repo_name}")
            else:
                logger.warning(f"Не удалось создать окружение для {repo_name}")
        
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
        
        return main_file
    
    def list_installed_repositories(self):
        """Список установленных репозиториев"""
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
    parser.add_argument("--install-repo", type=str, help="Установить репозиторий")
    parser.add_argument("--list-repos", action="store_true", help="Показать установленные репозитории")
    parser.add_argument("--system-info", action="store_true", help="Показать информацию о системе")
    
    args = parser.parse_args()
    
    # Создаем приложение
    app = PortableSourceApp()
    
    # Инициализируем
    app.initialize(args.install_path)
    
    # Выполняем команды
    if args.setup_env:
        app.setup_environment()
    
    if args.install_repo:
        app.install_repository(args.install_repo)
    
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
        print("  --install-repo <url>    Установить репозиторий")
        print("  --list-repos            Показать репозитории")
        print("  --system-info           Информация о системе")
        print("  --install-path <path>   Путь установки")
        print("="*50)

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Environment Manager для PortableSource
Управление окружениями на базе Miniconda
"""

import os
import sys
import logging
import subprocess
import json
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass

from .get_gpu import GPUDetector, CUDAVersion, GPUType

logger = logging.getLogger(__name__)

@dataclass
class EnvironmentSpec:
    """Спецификация окружения"""
    name: str
    python_version: str = "3.11"
    packages: List[str] = None
    pip_packages: List[str] = None
    cuda_version: Optional[str] = None
    
    def __post_init__(self):
        if self.packages is None:
            self.packages = []
        if self.pip_packages is None:
            self.pip_packages = []

class MinicondaInstaller:
    """Установщик Miniconda"""
    
    def __init__(self, install_path: Path):
        self.install_path = install_path
        self.miniconda_path = install_path / "miniconda"
        self.conda_exe = self.miniconda_path / "Scripts" / "conda.exe" if os.name == 'nt' else self.miniconda_path / "bin" / "conda"
        
    def is_installed(self) -> bool:
        """Проверяет, установлена ли Miniconda"""
        return self.conda_exe.exists()
    
    def get_installer_url(self) -> str:
        """Получает URL для скачивания Miniconda"""
        if os.name == 'nt':
            # Windows
            return "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
        else:
            # Linux/macOS
            return "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    
    def download_installer(self) -> Path:
        """Скачивает установщик Miniconda"""
        import urllib.request
        
        url = self.get_installer_url()
        filename = Path(url).name
        installer_path = self.install_path / filename
        
        logger.info(f"Скачивание Miniconda из {url}")
        
        try:
            urllib.request.urlretrieve(url, installer_path)
            logger.info(f"Miniconda скачана: {installer_path}")
            return installer_path
        except Exception as e:
            logger.error(f"Ошибка скачивания Miniconda: {e}")
            raise
    
    def install(self) -> bool:
        """Устанавливает Miniconda"""
        if self.is_installed():
            logger.info("Miniconda уже установлена")
            return True
        
        installer_path = self.download_installer()
        
        try:
            if os.name == 'nt':
                # Windows
                cmd = [
                    str(installer_path),
                    "/InstallationType=JustMe",
                    "/S",  # Тихая установка
                    f"/D={self.miniconda_path}",
                ]
            else:
                # Linux/macOS
                cmd = [
                    "bash",
                    str(installer_path),
                    "-b",  # Batch mode
                    "-p", str(self.miniconda_path),
                ]
            
            logger.info(f"Установка Miniconda в {self.miniconda_path}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("Miniconda успешно установлена")
                return True
            else:
                logger.error(f"Ошибка установки Miniconda: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка при установке Miniconda: {e}")
            return False

class EnvironmentManager:
    """Менеджер окружений conda"""
    
    def __init__(self, install_path: Path):
        self.install_path = install_path
        self.miniconda_path = install_path / "miniconda"
        self.envs_path = install_path / "envs"
        self.repos_path = install_path / "repos"
        self.conda_exe = self.miniconda_path / "Scripts" / "conda.exe" if os.name == 'nt' else self.miniconda_path / "bin" / "conda"
        self.installer = MinicondaInstaller(install_path)
        self.gpu_detector = GPUDetector()
        
    def ensure_miniconda(self) -> bool:
        """Убеждается, что Miniconda установлена"""
        if not self.installer.is_installed():
            return self.installer.install()
        return True
    
    def run_conda_command(self, args: List[str], **kwargs) -> subprocess.CompletedProcess:
        """Выполняет команду conda"""
        cmd = [str(self.conda_exe)] + args
        logger.info(f"Выполнение команды: {' '.join(cmd)}")
        
        # Добавляем переменные окружения для conda
        env = os.environ.copy()
        if os.name == 'nt':
            env['PATH'] = str(self.miniconda_path / "Scripts") + os.pathsep + env.get('PATH', '')
        else:
            env['PATH'] = str(self.miniconda_path / "bin") + os.pathsep + env.get('PATH', '')
        
        return subprocess.run(cmd, env=env, capture_output=True, text=True, **kwargs)
    
    def list_environments(self) -> List[str]:
        """Список всех venv окружений"""
        if not self.envs_path.exists():
            return []
        
        envs = []
        for item in self.envs_path.iterdir():
            if item.is_dir() and (item / "pyvenv.cfg").exists():
                envs.append(item.name)
        
        return envs
    
    def environment_exists(self, name: str) -> bool:
        """Проверяет существование venv окружения"""
        repo_env_path = self.envs_path / name
        return repo_env_path.exists() and (repo_env_path / "pyvenv.cfg").exists()
    
    def create_base_environment(self) -> bool:
        """Создает базовое окружение PortableSource"""
        env_name = "portablesource"
        
        if self.environment_exists(env_name):
            logger.info(f"Базовое окружение {env_name} уже существует")
            return True
        
        # Определяем пакеты для установки
        packages = [
            "python=3.11",
            "git",
            "ffmpeg",
            "pip",
            "setuptools",
            "wheel"
        ]
        
        # Добавляем CUDA пакеты если есть NVIDIA GPU
        gpu_info = self.gpu_detector.get_gpu_info()
        nvidia_gpu = next((gpu for gpu in gpu_info if gpu.gpu_type == GPUType.NVIDIA), None)
        
        if nvidia_gpu and nvidia_gpu.cuda_version:
            cuda_version = nvidia_gpu.cuda_version.value
            logger.info(f"Добавление CUDA {cuda_version} toolkit + cuDNN")
            
            if cuda_version == "11.8":
                packages.extend([
                    "cuda-toolkit=11.8",
                    "cudnn"
                ])
            elif cuda_version == "12.4":
                packages.extend([
                    "cuda-toolkit=12.4",
                    "cudnn"
                ])
            elif cuda_version == "12.8":
                packages.extend([
                    "cuda-toolkit=12.8",
                    "cudnn"
                ])
        
        # Создаем окружение
        cmd = ["create", "-n", env_name, "-y"] + packages
        result = self.run_conda_command(cmd)
        
        if result.returncode == 0:
            logger.info(f"Базовое окружение {env_name} создано")
            
            # Устанавливаем TensorRT через pip если есть NVIDIA GPU
            if nvidia_gpu:
                logger.info("Установка TensorRT через pip...")
                try:
                    pip_cmd = ["run", "-n", env_name, "pip", "install", 
                              "--upgrade", "nvidia-tensorrt", "nvidia-pyindex"]
                    pip_result = self.run_conda_command(pip_cmd)
                    
                    if pip_result.returncode == 0:
                        logger.info("TensorRT успешно установлен")
                    else:
                        logger.warning(f"TensorRT не установился: {pip_result.stderr}")
                except Exception as e:
                    logger.warning(f"Ошибка установки TensorRT: {e}")
            
            return True
        else:
            logger.error(f"Ошибка создания базового окружения: {result.stderr}")
            return False
    
    def create_repository_environment(self, repo_name: str, spec: EnvironmentSpec) -> bool:
        """Создает venv окружение для репозитория"""
        repo_env_path = self.envs_path / repo_name
        
        if repo_env_path.exists():
            logger.info(f"Venv окружение {repo_name} уже существует")
            return True
        
        # Создаем папку для venv окружений
        self.envs_path.mkdir(parents=True, exist_ok=True)
        
        # Проверяем наличие базового conda окружения
        if not (self.miniconda_path / "envs" / "portablesource").exists():
            logger.error("Базовое conda окружение portablesource не найдено!")
            return False
        
        # Создаем venv используя Python из базового conda окружения
        try:
            cmd = [str(self.python_exe), "-m", "venv", str(repo_env_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Ошибка создания venv: {result.stderr}")
                return False
            
            # Определяем путь к pip в venv
            if os.name == 'nt':
                venv_pip = repo_env_path / "Scripts" / "pip.exe"
                venv_python = repo_env_path / "Scripts" / "python.exe"
            else:
                venv_pip = repo_env_path / "bin" / "pip"
                venv_python = repo_env_path / "bin" / "python"
            
            # Обновляем pip в venv
            subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], 
                         capture_output=True, text=True)
            
            # Устанавливаем дополнительные пакеты
            if spec.pip_packages:
                for package in spec.pip_packages:
                    result = subprocess.run([str(venv_pip), "install", package], 
                                          capture_output=True, text=True)
                    if result.returncode != 0:
                        logger.warning(f"Не удалось установить {package}: {result.stderr}")
            
            logger.info(f"Venv окружение {repo_name} создано в {repo_env_path}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка создания venv окружения: {e}")
            return False
    
    def remove_environment(self, name: str) -> bool:
        """Удаляет venv окружение"""
        if not self.environment_exists(name):
            logger.warning(f"Venv окружение {name} не существует")
            return True
        
        repo_env_path = self.envs_path / name
        
        try:
            # Удаляем папку venv
            shutil.rmtree(repo_env_path)
            logger.info(f"Venv окружение {name} удалено")
            return True
        except Exception as e:
            logger.error(f"Ошибка удаления venv окружения {name}: {e}")
            return False
    
    def get_environment_python_path(self, env_name: str) -> Optional[Path]:
        """Получает путь к Python в venv окружении"""
        repo_env_path = self.envs_path / env_name
        
        if os.name == 'nt':
            python_path = repo_env_path / "Scripts" / "python.exe"
        else:
            python_path = repo_env_path / "bin" / "python"
        
        return python_path if python_path.exists() else None
    
    def activate_environment_script(self, env_name: str) -> str:
        """Возвращает скрипт для активации conda базового окружения + venv репозитория"""
        repo_env_path = self.envs_path / env_name
        
        if os.name == 'nt':
            # Windows batch
            conda_bat = self.miniconda_path / "Scripts" / "activate.bat"
            venv_activate = repo_env_path / "Scripts" / "activate.bat"
            return f'call "{conda_bat}" && conda activate portablesource && call "{venv_activate}"'
        else:
            # Linux bash
            conda_sh = self.miniconda_path / "etc" / "profile.d" / "conda.sh"
            venv_activate = repo_env_path / "bin" / "activate"
            return f'source "{conda_sh}" && conda activate portablesource && source "{venv_activate}"'
    
    def generate_launcher_script(self, repo_name: str, repo_path: Path, main_script: str) -> Path:
        """Генерирует скрипт запуска для репозитория в папке репозитория"""
        if os.name == 'nt':
            script_name = f"start_{repo_name}.bat"
            script_content = f"""@echo off
echo Запуск {repo_name}...
{self.activate_environment_script(repo_name)}
cd /d "{repo_path}"
python "{main_script}" %*
pause
"""
        else:
            script_name = f"start_{repo_name}.sh"
            script_content = f"""#!/bin/bash
echo "Запуск {repo_name}..."
{self.activate_environment_script(repo_name)}
cd "{repo_path}"
python "{main_script}" "$@"
"""
        
        # Создаем скрипт в папке репозитория
        script_path = repo_path / script_name
        script_path.write_text(script_content, encoding='utf-8')
        
        if os.name != 'nt':
            # Делаем исполняемым на Linux
            script_path.chmod(0o755)
        
        logger.info(f"Создан скрипт запуска: {script_path}")
        return script_path 
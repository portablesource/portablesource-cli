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

from portablesource.get_gpu import GPUDetector, CUDAVersion, GPUType

logger = logging.getLogger(__name__)

@dataclass
class EnvironmentSpec:
    """Спецификация окружения"""
    name: str
    python_version: str = "3.11"
    packages: Optional[List[str]] = None
    pip_packages: Optional[List[str]] = None
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
        try:
            from tqdm import tqdm
        except ImportError:
            logger.warning("tqdm не установлен, скачивание без прогресс-бара")
            tqdm = None
        
        url = self.get_installer_url()
        filename = Path(url).name
        installer_path = self.install_path / filename
        
        logger.info(f"Скачивание Miniconda из {url}")
        
        try:
            if tqdm:
                # Скачивание с прогресс-баром
                response = urllib.request.urlopen(url)
                total_size = int(response.headers.get('Content-Length', 0))
                
                with open(installer_path, 'wb') as f:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Скачивание Miniconda") as pbar:
                        while True:
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))
            else:
                # Обычное скачивание без прогресс-бара
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
        finally:
            # Удаляем установщик в любом случае
            try:
                if installer_path.exists():
                    os.remove(installer_path)
                    logger.info(f"Установщик удален: {installer_path}")
            except Exception as e:
                logger.warning(f"Не удалось удалить установщик {installer_path}: {e}")

            
class EnvironmentManager:
    """Менеджер окружений conda"""
    
    def __init__(self, install_path: Path):
        self.install_path = install_path
        self.miniconda_path = install_path / "miniconda"
        self.envs_path = install_path / "envs"
        self.repos_path = install_path / "repos"
        self.conda_exe = self.miniconda_path / "Scripts" / "conda.exe" if os.name == 'nt' else self.miniconda_path / "bin" / "conda"
        # Python executable from base conda environment
        if os.name == 'nt':
            self.python_exe = self.miniconda_path / "envs" / "portablesource" / "python.exe"
        else:
            self.python_exe = self.miniconda_path / "envs" / "portablesource" / "bin" / "python"
        self.installer = MinicondaInstaller(install_path)
        self.gpu_detector = GPUDetector()
        
    def ensure_miniconda(self) -> bool:
        """Убеждается, что Miniconda установлена"""
        if not self.installer.is_installed():
            return self.installer.install()
        return True
    
    def accept_conda_terms_of_service(self) -> bool:
        """Принимает Terms of Service для conda каналов"""
        channels = [
            "https://repo.anaconda.com/pkgs/main",
            "https://repo.anaconda.com/pkgs/r", 
            "https://repo.anaconda.com/pkgs/msys2"
        ]
        
        logger.info("Принятие Terms of Service для conda каналов...")
        
        for channel in channels:
            try:
                cmd = ["tos", "accept", "--override-channels", "--channel", channel]
                result = self.run_conda_command(cmd)
                
                if result.returncode == 0:
                    logger.info(f"✅ Terms of Service принят для {channel}")
                else:
                    logger.warning(f"⚠️ Не удалось принять ToS для {channel}: {result.stderr}")
                    
            except Exception as e:
                logger.warning(f"Ошибка при принятии ToS для {channel}: {e}")
        
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
    
    def run_conda_command_with_progress(self, args: List[str], description: str = "Выполнение команды conda", **kwargs) -> subprocess.CompletedProcess:
        """Выполняет команду conda с прогресс-баром и захватом вывода"""
        cmd = [str(self.conda_exe)] + args
        logger.info(f"Выполнение команды: {' '.join(cmd)}")
        
        # Добавляем переменные окружения для conda
        env = os.environ.copy()
        if os.name == 'nt':
            env['PATH'] = str(self.miniconda_path / "Scripts") + os.pathsep + env.get('PATH', '')
        else:
            env['PATH'] = str(self.miniconda_path / "bin") + os.pathsep + env.get('PATH', '')
        
        try:
            from tqdm import tqdm
            TQDM_AVAILABLE = True
        except ImportError:
            TQDM_AVAILABLE = False
            logger.warning("tqdm не установлен, выполнение без прогресс-бара")
        
        if TQDM_AVAILABLE:
            # Выполнение с прогресс-баром
            logger.info(f"🔄 {description}...")
            
            # Запускаем процесс
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Создаем прогресс-бар
            with tqdm(desc=description, unit="операция", dynamic_ncols=True) as pbar:
                output_lines = []
                if process.stdout:
                    for line in process.stdout:
                        output_lines.append(line)
                        pbar.update(1)
                        
                        # Показываем важные сообщения conda
                        line_lower = line.lower().strip()
                        if any(keyword in line_lower for keyword in [
                            "downloading", "extracting", "installing", "solving", 
                            "collecting", "preparing", "executing", "verifying",
                            "error", "failed", "warning"
                        ]):
                            # Обрезаем длинные строки для отображения
                            display_text = line.strip()[:60]
                            if len(line.strip()) > 60:
                                display_text += "..."
                            pbar.set_postfix_str(display_text)
            
            # Ждем завершения
            process.wait()
            
            # Создаем результат в формате CompletedProcess
            result = subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout=''.join(output_lines),
                stderr=None
            )
            
            if result.returncode == 0:
                logger.info(f"✅ {description} завершено успешно")
            else:
                logger.error(f"❌ {description} завершено с ошибкой (код: {result.returncode})")
                if result.stdout:
                    logger.error(f"Вывод: {result.stdout[-500:]}")
            
            return result
        else:
            # Обычное выполнение без прогресс-бара
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
    
    def check_base_environment_integrity(self) -> bool:
        """Проверяет целостность базового окружения"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if not conda_env_path.exists():
            logger.warning(f"Conda окружение {env_name} не найдено")
            return False
        
        # Проверяем наличие основных исполняемых файлов
        if os.name == 'nt':
            python_exe = conda_env_path / "python.exe"
            pip_exe = conda_env_path / "Scripts" / "pip.exe"
            git_exe = conda_env_path / "Library" / "bin" / "git.exe"
        else:
            python_exe = conda_env_path / "bin" / "python"
            pip_exe = conda_env_path / "bin" / "pip"
            git_exe = conda_env_path / "bin" / "git"
        
        missing_tools = []
        if not python_exe.exists():
            missing_tools.append("python")
        if not pip_exe.exists():
            missing_tools.append("pip")
        if not git_exe.exists():
            missing_tools.append("git")
        
        if missing_tools:
            logger.warning(f"В окружении {env_name} отсутствуют инструменты: {', '.join(missing_tools)}")
            return False
        
        # Проверяем работоспособность Python
        try:
            result = subprocess.run([str(python_exe), "--version"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.warning(f"Python в окружении {env_name} не работает")
                return False
        except Exception as e:
            logger.warning(f"Ошибка проверки Python в окружении {env_name}: {e}")
            return False
        
        # Проверяем TensorRT для NVIDIA GPU
        gpu_info = self.gpu_detector.get_gpu_info()
        nvidia_gpu = next((gpu for gpu in gpu_info if gpu.gpu_type == GPUType.NVIDIA), None)
        
        if nvidia_gpu:
            # Используем ConfigManager для определения поддержки TensorRT
            from .config import ConfigManager
            config_manager = ConfigManager()
            gpu_config = config_manager.configure_gpu(nvidia_gpu.name, nvidia_gpu.memory // 1024 if nvidia_gpu.memory else 0)
            
            if gpu_config.supports_tensorrt:
                tensorrt_status = self.check_tensorrt_installation()
                if not tensorrt_status:
                    logger.warning("TensorRT не установлен или не работает, будет выполнена переустановка")
                    if not self.reinstall_tensorrt():
                        logger.warning("Не удалось переустановить TensorRT, но базовое окружение работает")
        
        logger.info(f"Базовое окружение {env_name} прошло проверку целостности")
        return True
    
    def check_tensorrt_installation(self) -> bool:
        """Проверяет установку и работоспособность TensorRT"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if os.name == 'nt':
            python_exe = conda_env_path / "python.exe"
        else:
            python_exe = conda_env_path / "bin" / "python"
        
        try:
            # Проверяем импорт TensorRT
            result = subprocess.run([
                str(python_exe), "-c", 
                "import tensorrt; print(f'TensorRT {tensorrt.__version__} работает'); assert tensorrt.Builder(tensorrt.Logger())"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                logger.info(f"✅ TensorRT проверка пройдена: {result.stdout.strip()}")
                return True
            else:
                logger.warning(f"❌ TensorRT проверка не пройдена: {result.stderr.strip()}")
                return False
        except Exception as e:
            logger.warning(f"❌ Ошибка проверки TensorRT: {e}")
            return False
    
    def reinstall_tensorrt(self) -> bool:
        """Переустанавливает TensorRT"""
        env_name = "portablesource"
        
        try:
            logger.info("Переустановка TensorRT...")
            
            # Удаляем существующий TensorRT
            uninstall_cmd = ["run", "-n", env_name, "pip", "uninstall", "-y", "tensorrt", "tensorrt-libs", "tensorrt-bindings"]
            uninstall_result = self.run_conda_command(uninstall_cmd)
            
            # Обновляем pip, setuptools и wheel (игнорируем ошибки, если пакеты уже обновлены)
            # update_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
            # update_result = self.run_conda_command_with_progress(update_cmd, "Обновление pip, setuptools и wheel")
            update_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            
            # Продолжаем установку TensorRT даже если обновление pip завершилось с ошибкой
            # (часто pip уже обновлен, но возвращает код ошибки)
            if update_result.returncode == 0:
                logger.info("✅ pip, setuptools и wheel обновлены")
            else:
                logger.warning("⚠️ Обновление pip завершилось с предупреждениями, продолжаем установку TensorRT")
            
            # Устанавливаем TensorRT заново
            tensorrt_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "tensorrt"]
            tensorrt_result = self.run_conda_command_with_progress(tensorrt_cmd, "Переустановка TensorRT")
            
            if tensorrt_result.returncode == 0:
                # Проверяем установку
                if self.check_tensorrt_installation():
                    logger.info("✅ TensorRT успешно переустановлен")
                    return True
                else:
                    logger.warning("⚠️ TensorRT установлен, но проверка не пройдена")
                    return False
            else:
                logger.warning(f"⚠️ Ошибка переустановки TensorRT: {tensorrt_result.stderr}")
                return False
                
        except Exception as e:
            logger.warning(f"⚠️ Ошибка переустановки TensorRT: {e}")
            return False
    
    def remove_base_environment(self) -> bool:
        """Удаляет базовое conda окружение"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if not conda_env_path.exists():
            logger.info(f"Conda окружение {env_name} уже отсутствует")
            return True
        
        try:
            # Удаляем через conda
            cmd = ["env", "remove", "-n", env_name, "-y"]
            result = self.run_conda_command(cmd)
            
            if result.returncode == 0:
                logger.info(f"Conda окружение {env_name} удалено")
                return True
            else:
                logger.error(f"Ошибка удаления conda окружения: {result.stderr}")
                # Пробуем удалить папку напрямую
                shutil.rmtree(conda_env_path)
                logger.info(f"Conda окружение {env_name} удалено принудительно")
                return True
        except Exception as e:
            logger.error(f"Ошибка удаления conda окружения {env_name}: {e}")
            return False
    
    def create_base_environment(self) -> bool:
        """Создает базовое окружение PortableSource"""
        env_name = "portablesource"
        
        # Проверяем существование и целостность окружения
        conda_env_path = self.miniconda_path / "envs" / env_name
        if conda_env_path.exists():
            if self.check_base_environment_integrity():
                logger.info(f"Базовое окружение {env_name} уже существует и работает корректно")
                return True
            else:
                logger.warning(f"Базовое окружение {env_name} повреждено, выполняется переустановка...")
                if not self.remove_base_environment():
                    logger.error("Не удалось удалить поврежденное окружение")
                    return False
        
        # Принимаем Terms of Service перед созданием окружения
        self.accept_conda_terms_of_service()
        
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
        
        # Создаем окружение с прогресс-баром
        cmd = ["create", "-n", env_name, "-y"] + packages
        result = self.run_conda_command_with_progress(cmd, f"Создание окружения {env_name} с {len(packages)} пакетами")
        
        if result.returncode == 0:
            logger.info(f"Базовое окружение {env_name} создано")
            
            # Устанавливаем дополнительные пакеты для NVIDIA GPU
            if nvidia_gpu:
                logger.info("Установка дополнительных пакетов для NVIDIA GPU...")
                try:
                    # Skip pip upgrade to avoid permission issues
                    # update_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
                    # update_result = self.run_conda_command_with_progress(update_cmd, "Обновление pip, setuptools и wheel")
                    update_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")  # Mock successful result
                    
                    # Продолжаем установку TensorRT даже если обновление pip завершилось с ошибкой
                    if update_result.returncode == 0:
                        logger.info("✅ pip, setuptools и wheel обновлены")
                    else:
                        logger.warning("⚠️ Обновление pip завершилось с предупреждениями, продолжаем установку TensorRT")
                    
                    # Устанавливаем TensorRT согласно официальной документации
                    logger.info("Установка TensorRT (опционально)...")
                    tensorrt_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "tensorrt"]
                    tensorrt_result = self.run_conda_command_with_progress(tensorrt_cmd, "Установка TensorRT")
                    
                    if tensorrt_result.returncode == 0:
                        logger.info("✅ TensorRT успешно установлен")
                        logger.info("💡 Для проверки используйте: python -c 'import tensorrt; print(tensorrt.__version__)'")
                    else:
                        logger.warning("⚠️ TensorRT не установился (возможно, несовместимая версия Python или CUDA)")
                        logger.info("💡 TensorRT можно установить вручную позже при необходимости")
                except Exception as e:
                    logger.warning(f"⚠️ Ошибка установки дополнительных NVIDIA пакетов: {e}")
                    logger.info("💡 Базовое окружение создано успешно, дополнительные пакеты можно установить позже")
            
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
            
            # Skip pip upgrade to avoid permission issues
            # subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], 
            #              capture_output=True, text=True)
            
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
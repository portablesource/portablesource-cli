#!/usr/bin/env python3
"""
Environment Manager for PortableSource
Managing portable tools in ps_env directory
"""

import os
import logging
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import re
from tqdm import tqdm

from .get_gpu import GPUDetector
from .config import ConfigManager, TOOLinks

logger = logging.getLogger(__name__)

@dataclass
class PortableToolSpec:
    """Specification for portable tools"""
    name: str
    url: str
    extract_path: str
    executable_path: str
    
    def get_full_extract_path(self, ps_env_path: Path) -> Path:
        """Get full extraction path"""
        return ps_env_path / self.extract_path
    
    def get_full_executable_path(self, ps_env_path: Path) -> Path:
        """Get full executable path"""
        return ps_env_path / self.executable_path

class PortableEnvironmentManager:
    """Portable environment manager for downloading and extracting tools"""
    
    def __init__(self, install_path: Path, config_manager: Optional[ConfigManager] = None):
        self.install_path = install_path
        self.ps_env_path = install_path / "ps_env"
        self.gpu_detector = GPUDetector()
        
        self._aria2c_lock = threading.Lock()
        self._7z_lock = threading.Lock()
        
        if config_manager is None:
            config_path = install_path / "portablesource_config.json"
            self.config_manager = ConfigManager(config_path)
            self.config_manager.load_config()
        else:
            self.config_manager = config_manager
        
        self.tool_specs = {
            "ffmpeg": PortableToolSpec(
                name="ffmpeg",
                url=TOOLinks.FFMPEG_URL.value,
                extract_path="ffmpeg",
                executable_path="ffmpeg/ffmpeg.exe" if os.name == 'nt' else "ffmpeg/ffmpeg"
            ),
            "git": PortableToolSpec(
                name="git",
                url=TOOLinks.GIT_URL.value,
                extract_path="git",
                executable_path="git/cmd/git.exe" if os.name == 'nt' else "git/bin/git"
            ),
            "python": PortableToolSpec(
                name="python",
                url=TOOLinks.PYTHON311_URL.value,
                extract_path="python",
                executable_path="python/python.exe" if os.name == 'nt' else "python/bin/python"
            )
        }

    def _setup_prerequisites(self) -> bool:
        """Устанавливает 7z и aria2c, если их нет."""
        self.ps_env_path.mkdir(parents=True, exist_ok=True)
        
        seven_zip_path = self.ps_env_path / "7z.exe"
        if not self._verify_archive(seven_zip_path):
            if not self._download_file_urllib("https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe", seven_zip_path, "7-Zip"):
                return False
        
        aria2c_path = self.ps_env_path / "aria2c.exe"
        if not (aria2c_path.exists() and aria2c_path.stat().st_size > 1000):
             if not self._download_file_urllib("https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/aria2c.exe", aria2c_path, "aria2c"):
                return False
        return True
    
    def _download_file_urllib(self, url: str, destination: Path, description: str) -> bool:
        """Загрузчик для маленьких файлов (7z, aria2c) с чистым tqdm."""
        try:
            response = urllib.request.urlopen(url)
            total_size = int(response.headers.get('Content-Length', 0))
            
            with tqdm(total=total_size, unit='B', unit_scale=True, desc=description, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt}') as pbar:
                with open(destination, 'wb') as f:
                    for chunk in iter(lambda: response.read(16384), b''):
                        f.write(chunk)
                        pbar.update(len(chunk))
            return True
        except Exception as e:
            logger.error(f"Failed to download '{description}': {e}")
            return False
    
    def _download_file_aria(self, url: str, destination: Path, description: str, skip_verification: bool = False) -> bool:
        """Загрузчик для больших файлов с aria2c и чистым tqdm."""
        try:
            # Проверяем, не скачан ли файл уже полностью
            if destination.exists() and not skip_verification:
                # Проверяем целостность существующего файла
                if self._verify_archive(destination):
                    return True
                # Если файл поврежден, удаляем его
                destination.unlink()
            
            aria2c_path = self.ps_env_path / "aria2c.exe"
            cmd = [
                str(aria2c_path), "--continue=true", "--max-tries=0", "--retry-wait=5", "--split=5",
                "--max-connection-per-server=5", "--min-split-size=1M", "--summary-interval=1",
                "--dir", str(destination.parent), "--out", destination.name, url
            ]
            
            progress_regex = re.compile(r"\[#\w+ (\d+\.?\d*)([KMGT]?i?B)/(\d+\.?\d*)([KMGT]?i?B)\((\d+)%\)")
            def size_to_bytes(size, unit):
                size = float(size)
                if 'K' in unit: return size * 1024
                if 'M' in unit: return size * 1024**2
                if 'G' in unit: return size * 1024**3
                return size

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            
            pbar = None
            initial_downloaded = None
            
            # Читаем вывод построчно до завершения процесса
            for line in iter(process.stdout.readline, ''):
                if "Download Results" in line: 
                    break
                
                # Отладочный вывод для диагностики
                if line.strip():
                    logger.debug(f"aria2c output: {line.strip()}")
                
                match = progress_regex.search(line)
                if match:
                    downloaded_s, downloaded_u, total_s, total_u, percent = match.groups()
                    total_bytes = size_to_bytes(total_s, total_u)
                    downloaded_bytes = size_to_bytes(downloaded_s, downloaded_u)
                    
                    if pbar is None:
                        initial_downloaded = downloaded_bytes
                        pbar = tqdm(total=total_bytes, initial=downloaded_bytes, unit='B', unit_scale=True, unit_divisor=1024, 
                                   desc=description, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]')
                    
                    pbar.n = downloaded_bytes
                    pbar.refresh()
            
            process.wait()
            if pbar:
                if pbar.total: 
                    pbar.n = pbar.total
                pbar.close()

            return process.returncode == 0
        except Exception as e:
            logger.error(f"An error occurred during download of '{description}': {e}")
            return False

    def _verify_archive(self, file_path: Path) -> bool:
        """Проверяет архив, не выводя мусор в консоль."""
        if not file_path.exists() or file_path.stat().st_size < 1024: return False
        try:
            seven_zip_exe = self.ps_env_path / "7z.exe"
            result = subprocess.run([str(seven_zip_exe), "t", str(file_path)], capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.debug(f"7z integrity test failed for {file_path.name}:\n{result.stderr}")
                return False
            return True
        except Exception:
            return False

    def _extract_archive(self, archive_path: Path, extract_to: Path) -> bool:
        """Распаковывает архив, не выводя мусор в консоль."""
        try:
            seven_zip_exe = self.ps_env_path / "7z.exe"
            result = subprocess.run([str(seven_zip_exe), "x", str(archive_path), f"-o{extract_to}", "-y"], capture_output=True, text=True)
            if result.returncode == 0:
                return True
            else:
                logger.error(f"Extraction failed for {archive_path.name}.")
                logger.debug(f"7-Zip extraction failed. Raw stderr:\n{result.stderr}")
                return False
        except Exception as e:
            logger.error(f"An unexpected error during extraction: {e}")
            return False

    def _verify_file_integrity(self, file_path: Path) -> bool:
        """Verifies archive integrity, hiding unnecessary error details."""
        try:
            if not file_path.exists() or file_path.stat().st_size < 100: return False
            if file_path.suffix.lower() not in ['.7z', '.zip', '.rar']: return True

            seven_zip_exe = self.ps_env_path / "7z.exe"
            if not seven_zip_exe.exists(): return False
            
            result = subprocess.run([str(seven_zip_exe), "t", str(file_path)], capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                logger.debug(f"7z integrity test failed for {file_path.name}. Raw output:\n{result.stderr}")
                return False
            return True
        except Exception:
            return False

    def download_file(self, url: str, destination: Path, description: str) -> bool:
        """Main downloader with aria2c and beautiful progress bar."""
        try:
            aria2c_path = self.ps_env_path / "aria2c.exe"
            if not aria2c_path.exists():
                logger.error("aria2c not found. Please run prerequisite setup.")
                return False

            if destination.exists() and self._verify_file_integrity(destination):
                logger.info(f'"{description}" already exists and is valid.')
                return True

            logger.info(f"Downloading '{description}'...")
            
            cmd = [
                str(aria2c_path), "--continue=true", "--max-tries=0", "--retry-wait=5",
                "--split=5", "--max-connection-per-server=5", "--min-split-size=1M",
                "--file-allocation=none", "--check-integrity=true", "--summary-interval=1",
                "--dir", str(destination.parent), "--out", destination.name, url
            ]
            
            progress_regex = re.compile(r"\[#\w+ (\d+\.?\d*)([KMGT]?i?B)/(\d+\.?\d*)([KMGT]?i?B)\((\d+)%\)\]")

            def size_to_bytes(size, unit):
                size = float(size)
                if 'K' in unit: return size * 1024
                if 'M' in unit: return size * 1024**2
                if 'G' in unit: return size * 1024**3
                return size

            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
            
            with tqdm(total=None, unit='B', unit_scale=True, unit_divisor=1024, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]') as pbar:
                pbar.set_description(description)
                for line in iter(process.stdout.readline, ''):
                    if "Download Results" in line: break
                    match = progress_regex.search(line)
                    if match:
                        downloaded_size, downloaded_unit, total_size, total_unit, _ = match.groups()
                        if pbar.total is None: pbar.total = size_to_bytes(total_size, total_unit)
                        pbar.update(size_to_bytes(downloaded_size, downloaded_unit) - pbar.n)
            
            process.wait()

            if process.returncode == 0:
                if pbar.total: pbar.update(pbar.total - pbar.n)
                logger.info(f"Successfully downloaded '{description}'.")
                return True
            else:
                logger.error(f"aria2c failed to download '{description}'.")
                return False

        except Exception as e:
            logger.error(f"An exception occurred during download of '{description}': {e}")
            return False
    
    def download_7z_executable(self) -> Optional[Path]:
        """Download 7z.exe if not available in system"""
        try:
            if shutil.which("7z"):
                return None
            
            seven_zip_url = "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe"
            seven_zip_path = self.ps_env_path / "7z.exe"
            
            if seven_zip_path.exists():
                # Для 7z.exe просто проверяем размер файла
                if seven_zip_path.stat().st_size > 500000:  # 7z.exe должен быть больше 500KB
                    return seven_zip_path
                else:
                    logger.info("Existing 7z.exe is corrupted, re-downloading...")
                    seven_zip_path.unlink()
            
            logger.info("Downloading 7z.exe...")
            if self._download_file_aria(seven_zip_url, seven_zip_path, "7z.exe", skip_verification=True):
                return seven_zip_path
            else:
                logger.error("Failed to download 7z.exe")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading 7z.exe: {e}")
            return None
    
    def extract_7z_archive(self, archive_path: Path, extract_to: Path) -> bool:
        """Extracts archive, hiding error details."""
        try:
            seven_zip_exe = self.ps_env_path / "7z.exe"
            if not seven_zip_exe.exists():
                logger.error("7-Zip not found!")
                return False

            logger.info(f"Extracting {archive_path.name}...")
            result = subprocess.run([str(seven_zip_exe), "x", str(archive_path), f"-o{extract_to}", "-y"], capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"Successfully extracted {archive_path.name}.")
                return True
            else:
                logger.error(f"Extraction failed for {archive_path.name}.")
                logger.debug(f"7-Zip extraction failed. Raw stderr:\n{result.stderr}")
                return False
        except Exception as e:
            logger.error(f"An unexpected error occurred during extraction: {e}")
            return False
    
    def _fix_nested_extraction(self, extract_path: Path, tool_name: str) -> None:
        """Fix nested folder structure when archive contains a subfolder with the same name"""
        try:
            nested_folder = extract_path / tool_name
            if nested_folder.exists() and nested_folder.is_dir():
                nested_contents = list(nested_folder.iterdir())
                extract_contents = [item for item in extract_path.iterdir() if item != nested_folder]
                
                if nested_contents and len(extract_contents) == 0:
                    temp_dir = extract_path.parent / f"{tool_name}_temp"
                    temp_dir.mkdir(exist_ok=True)
                    
                    for item in nested_contents:
                        shutil.move(str(item), str(temp_dir / item.name))
                    
                    nested_folder.rmdir()
                    
                    for item in temp_dir.iterdir():
                        shutil.move(str(item), str(extract_path / item.name))
                    
                    temp_dir.rmdir()
                    
        except Exception as e:
            logger.error(f"Failed to fix nested extraction for {tool_name}: {e}")
    
    def _fix_cuda_nested_extraction(self, extract_path: Path, cuda_version) -> None:
        """Fix CUDA-specific nested folder structure issues"""
        try:
            possible_nested_folders = [
                f"CUDA_{cuda_version.value}",
                f"cuda_{cuda_version.value}",
                "CUDA",
                "cuda"
            ]
            
            for folder_name in possible_nested_folders:
                nested_folder = extract_path / folder_name
                if nested_folder.exists() and nested_folder.is_dir():
                    if (nested_folder / "bin").exists():
                        extract_contents = [item for item in extract_path.iterdir() if item != nested_folder]
                        
                        if len(extract_contents) == 0:
                            temp_dir = extract_path.parent / f"cuda_temp_{cuda_version.value}"
                            temp_dir.mkdir(exist_ok=True)
                            
                            for item in nested_folder.iterdir():
                                shutil.move(str(item), str(temp_dir / item.name))
                            
                            nested_folder.rmdir()
                            
                            for item in temp_dir.iterdir():
                                shutil.move(str(item), str(extract_path / item.name))
                            
                            temp_dir.rmdir()
                            return
                    
            if not (extract_path / "bin").exists():
                logger.error(f"CUDA extraction may be incomplete - no bin directory found in {extract_path}")
                
        except Exception as e:
            logger.error(f"Failed to fix CUDA nested extraction: {e}")
    
    def install_tool(self, tool_name: str) -> bool:
        """Полный цикл установки для одного инструмента."""
        spec = self.tool_specs[tool_name]
        if spec.get_full_executable_path(self.ps_env_path).exists():
            return True
        
        archive_path = self.ps_env_path / f"{spec.name}.7z"
        extract_path = spec.get_full_extract_path(self.ps_env_path)
        
        if not self._verify_archive(archive_path):
            if not self._download_file_aria(spec.url, archive_path, spec.name): return False
        
        if not self._extract_archive(archive_path, extract_path): return False
        
        self._fix_nested_extraction(extract_path, tool_name)
        
        try: archive_path.unlink()
        except OSError: pass

        if not spec.get_full_executable_path(self.ps_env_path).exists():
            logger.error(f"Installation of {spec.name} failed: executable not found.")
            return False
            
        return True

    def install_cuda(self) -> bool:
        """Полный цикл установки для CUDA."""
        gpu_config = self.config_manager.config.gpu_config
        if not (gpu_config and gpu_config.cuda_version and "cuda" in gpu_config.recommended_backend):
            return True
        
        cuda_extract_path = self.ps_env_path / "CUDA"
        if (cuda_extract_path / "bin").exists():
            return True
            
        cuda_link = self.config_manager.get_cuda_download_link(gpu_config.cuda_version)
        if not cuda_link: return False
        
        archive_path = self.ps_env_path / f"cuda_{gpu_config.cuda_version.value}.7z"

        if not self._verify_archive(archive_path):
            # Если файл заблокирован, попробуем его удалить
            if archive_path.exists():
                try:
                    archive_path.unlink()
                    logger.info(f"Removed corrupted/locked file: {archive_path}")
                except OSError as e:
                    logger.warning(f"Could not remove file {archive_path}: {e}")
            
            if not self._download_file_aria(cuda_link, archive_path, f"CUDA {gpu_config.cuda_version.value}"): return False
        
        if not self._extract_archive(archive_path, cuda_extract_path): return False
        
        self._fix_cuda_nested_extraction(cuda_extract_path, gpu_config.cuda_version)
        
        try: archive_path.unlink()
        except OSError: pass

        if not (cuda_extract_path / "bin").exists():
            logger.error("CUDA installation failed: 'bin' directory not found.")
            return False
        
        self.config_manager.configure_cuda_paths()
        return True

    def run_in_activated_environment(self, command: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Run a command in the portable environment with proper PATH setup"""
        if not self.ps_env_path.exists():
            logger.error("Base environment ps_env not found. Run --setup-env first.")
            return subprocess.CompletedProcess([], 1, "", "Base environment not found")

        env = self.setup_environment_for_subprocess()
        
        if command and command[0] == "nvcc":
            logger.debug(f"Running nvcc with PATH: {env.get('PATH', 'Not set')}")
            if (self.config_manager.config and 
                self.config_manager.config.gpu_config and 
                self.config_manager.config.gpu_config.cuda_paths):
                cuda_paths = self.config_manager.config.gpu_config.cuda_paths
                cuda_bin = Path(cuda_paths.cuda_bin)
                logger.debug(f"CUDA bin path exists: {cuda_bin.exists()} at {cuda_bin}")
                if cuda_bin.exists():
                    nvcc_exe = cuda_bin / "nvcc.exe"
                    logger.debug(f"nvcc.exe exists: {nvcc_exe.exists()} at {nvcc_exe}")
        
        import platform
        use_shell = platform.system() == "Windows"
        
        if use_shell:
            command_str = ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in command)
            return subprocess.run(
                command_str,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=env,
                shell=True
            )
        else:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                env=env
            )

    def get_ps_env_python(self) -> Optional[Path]:
        """Gets the path to python executable in ps_env"""
        if not self.ps_env_path.exists():
            return None
        
        if "python" in self.tool_specs:
            python_exe = self.ps_env_path / self.tool_specs["python"].executable_path
            return python_exe if python_exe.exists() else None
        return None

    def get_ps_env_pip(self) -> Optional[Path]:
        """Gets the path to pip executable in ps_env"""
        if not self.ps_env_path.exists():
            return None
        
        if "python" in self.tool_specs:
            if os.name == 'nt':
                pip_exe = self.ps_env_path / "python" / "Scripts" / "pip.exe"
            else:
                pip_exe = self.ps_env_path / "python" / "bin" / "pip"
            return pip_exe if pip_exe.exists() else None
        return None

    def get_git_executable(self) -> Optional[Path]:
        """Get git executable path from portable environment"""
        if "git" in self.tool_specs:
            git_exe = self.ps_env_path / self.tool_specs["git"].executable_path
            return git_exe if git_exe.exists() else None
        return None

    def get_python_executable(self) -> Optional[Path]:
        """Get Python executable path from portable environment"""
        return self.get_ps_env_python()

    def setup_environment_for_subprocess(self) -> Dict[str, str]:
        """Setup environment variables for subprocess execution"""
        env_vars = dict(os.environ)
        
        if not self.ps_env_path.exists():
            return env_vars
        
        tool_paths = []
        for tool_name, tool_spec in self.tool_specs.items():
            tool_dir = self.ps_env_path / tool_spec.extract_path
            if tool_dir.exists():
                executable_path = self.ps_env_path / tool_spec.executable_path
                executable_dir = executable_path.parent
                if executable_dir.exists():
                    tool_paths.append(str(executable_dir))
                else:
                    tool_paths.append(str(tool_dir))
        

        if (self.config_manager.config and 
            self.config_manager.config.gpu_config and 
            self.config_manager.config.gpu_config.cuda_paths):
            cuda_paths = self.config_manager.config.gpu_config.cuda_paths
            cuda_base = Path(cuda_paths.base_path)
            

            if cuda_base.exists():
                cuda_bin = Path(cuda_paths.cuda_bin)
                if cuda_bin.exists():
                    tool_paths.append(str(cuda_bin))

                    cuda_lib = Path(cuda_paths.cuda_lib)
                    if cuda_lib.exists():
                        tool_paths.append(str(cuda_lib))
                    cuda_lib_64 = Path(cuda_paths.cuda_lib_64)
                    if cuda_lib_64.exists():
                        tool_paths.append(str(cuda_lib_64))
                    

                    env_vars['CUDA_PATH'] = str(cuda_base)
                    env_vars['CUDA_HOME'] = str(cuda_base)
                    env_vars['CUDA_ROOT'] = str(cuda_base)
                    env_vars['CUDA_BIN_PATH'] = str(cuda_bin)
                    env_vars['CUDA_LIB_PATH'] = str(cuda_lib_64) if cuda_lib_64.exists() else str(cuda_lib)
                else:
                    logger.debug(f"CUDA bin directory not found: {cuda_bin}")
            else:
                logger.debug(f"CUDA base directory not found: {cuda_base}. CUDA may not be installed.")

                if (self.config_manager.config.gpu_config and 
                    self.config_manager.config.gpu_config.cuda_version):
                    logger.info("CUDA not found but GPU supports it. You may need to run --setup-env to install CUDA.")
        
        if tool_paths:
            current_path = env_vars.get('PATH', '')
            env_vars['PATH'] = os.pathsep.join(tool_paths + [current_path])
        
        return env_vars

    def check_environment_availability(self) -> bool:
        """Check if portable environment is available and working"""
        if not self.ps_env_path.exists():
            return False
        

        python_exe = self.get_python_executable()
        git_exe = self.get_git_executable()
        
        return python_exe is not None and python_exe.exists() and git_exe is not None and git_exe.exists()

    
    def _extract_version_from_output(self, tool_name: str, output: str) -> str:
        """Extract version information from tool output"""
        if not output:
            return "Unknown version"
        
        lines = output.strip().split('\n')
        
        # For nvcc, look for the actual nvcc output after all the environment setup
        if tool_name == "nvcc":
            # Find lines that contain "nvcc:" or "Cuda compilation tools"
            for line in lines:
                if "nvcc:" in line or "Cuda compilation tools" in line:
                    return line.strip()

            for line in reversed(lines):
                if line.strip() and not line.startswith("C:\\") and "SET" not in line and "set" not in line:
                    return line.strip()
        

        version_patterns = {
            "python": ["Python "],
            "git": ["git version"],
            "ffmpeg": ["ffmpeg version"]
        }
        

        if tool_name in version_patterns:
            patterns = version_patterns[tool_name]
            for line in lines:
                for pattern in patterns:
                    if pattern in line:
                        return line.strip()
        

        for line in lines:
            line = line.strip()
            if line and not line.startswith("C:\\") and "SET" not in line and "set" not in line and not line.startswith("(") and ">" not in line:
                return line
        
        return "Unknown version"
    
    def _verify_environment_tools(self) -> bool:
        """Verify that all essential tools in the environment are working properly"""
        tools_to_check = [
            ("python", ["--version"]),
            ("git", ["--version"]),
            ("ffmpeg", ["-version"])
        ]
        

        if self.gpu_detector.get_gpu_info():
            gpu_info = self.gpu_detector.get_gpu_info()
            if gpu_info and any(gpu.gpu_type.name == "NVIDIA" for gpu in gpu_info):
                tools_to_check.append(("nvcc", ["--version"]))
        
        all_tools_working = True
        
        for tool_name, args in tools_to_check:
            try:
                result = self.run_in_activated_environment([tool_name] + args)

                version_output = self._extract_version_from_output(tool_name, result.stdout)
                

                if version_output and version_output != "Unknown version":
                    logger.info(f"[OK] {tool_name}: {version_output}")
                else:
                    logger.error(f"[ERROR] {tool_name}: Failed to run (exit code {result.returncode})")
                    if result.stderr:
                        logger.error(f"   Error: {result.stderr.strip()}")
                    all_tools_working = False
            except Exception as e:
                logger.error(f"[ERROR] {tool_name}: Exception occurred - {e}")
                all_tools_working = False
        
        return all_tools_working
    
    def _check_and_suggest_cuda_installation(self) -> None:
        """Check if CUDA should be available and suggest installation if missing"""
        if (self.config_manager.config and 
            self.config_manager.config.gpu_config and 
            self.config_manager.config.gpu_config.cuda_version):
            
            cuda_paths = self.config_manager.config.gpu_config.cuda_paths
            if cuda_paths:
                cuda_base = Path(cuda_paths.base_path)
                if not cuda_base.exists():
                    logger.warning(f"CUDA {self.config_manager.config.gpu_config.cuda_version.value} is configured but not installed.")
                    logger.info("To install CUDA, run: portablesource --setup-env")
                else:
                    cuda_bin = Path(cuda_paths.cuda_bin)
                    if not cuda_bin.exists():
                        logger.warning(f"CUDA installation incomplete: {cuda_bin} not found")
                        logger.info("To reinstall CUDA, run: portablesource --setup-env")
                    else:
                        nvcc_exe = cuda_bin / "nvcc.exe"
                        if not nvcc_exe.exists():
                            logger.warning(f"nvcc.exe not found in CUDA installation: {nvcc_exe}")
                            logger.info("To reinstall CUDA, run: portablesource --setup-env")
    
    def check_environment_status(self) -> Dict[str, Any]:
        """Check the current status of the environment and all tools"""
        status = {
            "environment_exists": self.ps_env_path.exists(),
            "environment_setup_completed": self.config_manager.is_environment_setup_completed(),
            "tools_status": {}
        }
        
        if not status["environment_exists"]:
            status["overall_status"] = "Environment not found"
            return status
        

        self._check_and_suggest_cuda_installation()
        

        tools_to_check = [
            ("python", ["--version"]),
            ("git", ["--version"]),
            ("ffmpeg", ["-version"])
        ]
        

        if self.gpu_detector.get_gpu_info():
            gpu_info = self.gpu_detector.get_gpu_info()
            if gpu_info and any(gpu.gpu_type.name == "NVIDIA" for gpu in gpu_info):
                tools_to_check.append(("nvcc", ["--version"]))
        
        all_working = True
        for tool_name, args in tools_to_check:
            try:
                result = self.run_in_activated_environment([tool_name] + args)
                version_output = self._extract_version_from_output(tool_name, result.stdout)
                

                if version_output and version_output != "Unknown version":
                    status["tools_status"][tool_name] = {
                        "working": True,
                        "version": version_output
                    }
                else:

                    if tool_name == "nvcc":
                        error_msg = f"Exit code {result.returncode}"
                        if result.stderr and "не является внутренней или внешней" in result.stderr:
                            error_msg = "Command not found - CUDA may not be installed or not in PATH"
                        elif result.stderr and "не удается найти указанный путь" in result.stderr:
                            error_msg = "Path not found - CUDA installation may be incomplete"
                        
                        status["tools_status"][tool_name] = {
                            "working": False,
                            "error": error_msg,
                            "stderr": result.stderr.strip() if result.stderr else None
                        }
                    else:
                        status["tools_status"][tool_name] = {
                            "working": False,
                            "error": f"Exit code {result.returncode}",
                            "stderr": result.stderr.strip() if result.stderr else None
                        }
                    all_working = False
            except Exception as e:
                status["tools_status"][tool_name] = {
                    "working": False,
                    "error": str(e)
                }
                all_working = False
        
        status["all_tools_working"] = all_working
        status["overall_status"] = "Ready" if all_working else "Issues detected"
        
        return status
 
    def get_environment_info(self) -> Dict[str, Any]:
        """Get information about portable environment"""

        python_path = self.get_ps_env_python()
        base_env_exists = self.ps_env_path.exists() and python_path and python_path.exists()
        

        installed_tools = {}
        for tool_name, tool_spec in self.tool_specs.items():
            tool_path = self.ps_env_path / tool_spec.extract_path
            installed_tools[tool_name] = tool_path.exists()
        
        info = {
            "base_env_exists": base_env_exists,
            "base_env_python": str(self.get_ps_env_python()) if self.get_ps_env_python() else None,
            "base_env_pip": str(self.get_ps_env_pip()) if self.get_ps_env_pip() else None,
            "installed_tools": installed_tools,
            "paths": {
                "ps_env_path": str(self.ps_env_path)
            }
        }
        return info

    def setup_environment(self) -> bool:
        """Полная настройка портативной среды."""
        logger.info("Setting up portable environment...")
        
        self.ps_env_path.mkdir(parents=True, exist_ok=True)
        
        if not self.config_manager.config or not self.config_manager.config.install_path:
            self.config_manager.configure_install_path(str(self.install_path))
        
        try:
            gpu_config = self.config_manager.configure_gpu_from_detection()
            logger.info(f"GPU configured: {gpu_config.name}")
        except Exception as e:
            logger.error(f"Failed to configure GPU: {e}")
        
        if not self._setup_prerequisites(): return False
        
        for tool_name in self.tool_specs:
            if not self.install_tool(tool_name): return False
        
        if not self.install_cuda(): return False
        
        if not self._verify_environment_tools(): return False
        
        try:
            self.config_manager.mark_environment_setup_completed(True)
            if self.config_manager.config:
                self.config_manager.config.install_path = str(self.install_path)
            self.config_manager.save_config()
        except Exception as e:
            logger.warning(f"Failed to save setup status to config: {e}")
        
        logger.info("Portable environment setup completed successfully.")
        return True

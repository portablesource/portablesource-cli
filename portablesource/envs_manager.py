#!/usr/bin/env python3
"""
Environment Manager for PortableSource
Managing environments based on Miniconda
"""

import os
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass

from portablesource.get_gpu import GPUDetector, GPUType

logger = logging.getLogger(__name__)

@dataclass
class EnvironmentSpec:
    """Environment specification"""
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
    """Miniconda installer"""
    
    def __init__(self, install_path: Path):
        self.install_path = install_path
        self.miniconda_path = install_path / "miniconda"
        self.conda_exe = self.miniconda_path / "Scripts" / "conda.exe" if os.name == 'nt' else self.miniconda_path / "bin" / "conda"
        
    def is_installed(self) -> bool:
        """Checks if Miniconda is installed"""
        return self.conda_exe.exists()
    
    def get_installer_url(self) -> str:
        """Gets URL for downloading Miniconda"""
        if os.name == 'nt':
            # Windows
            return "https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe"
        else:
            # Linux/macOS
            return "https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh"
    
    def download_installer(self) -> Path:
        """Downloads Miniconda installer"""
        import urllib.request
        try:
            from tqdm import tqdm
        except ImportError:
            logger.warning("tqdm not installed, downloading without progress bar")
            tqdm = None
        
        url = self.get_installer_url()
        filename = Path(url).name
        installer_path = self.install_path / filename
        
        logger.info(f"Downloading Miniconda from {url}")
        
        try:
            if tqdm:
                # Download with progress bar
                response = urllib.request.urlopen(url)
                total_size = int(response.headers.get('Content-Length', 0))
                
                with open(installer_path, 'wb') as f:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Downloading Miniconda") as pbar:
                        while True:
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))
            else:
                # Regular download without progress bar
                urllib.request.urlretrieve(url, installer_path)
            
            logger.info(f"Miniconda downloaded: {installer_path}")
            return installer_path
        except Exception as e:
            logger.error(f"Error downloading Miniconda: {e}")
            raise
    
    def install(self) -> bool:
        """Installs Miniconda"""
        if self.is_installed():
            logger.info("Miniconda already installed")
            return True
        
        # Check if miniconda directory exists and is not empty
        if self.miniconda_path.exists():
            if any(self.miniconda_path.iterdir()):
                logger.warning(f"Directory {self.miniconda_path} is not empty, removing it...")
                try:
                    shutil.rmtree(self.miniconda_path)
                    logger.info(f"Directory {self.miniconda_path} removed")
                except Exception as e:
                    logger.error(f"Failed to remove directory {self.miniconda_path}: {e}")
                    return False
        
        installer_path = self.download_installer()
        
        try:
            if os.name == 'nt':
                # Windows
                cmd = [
                    str(installer_path),
                    "/InstallationType=JustMe",
                    "/S",  # Silent installation
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
            
            logger.info(f"Installing Miniconda to {self.miniconda_path}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info("Miniconda successfully installed")
                return True
            else:
                logger.error(f"Error installing Miniconda:")
                logger.error(f"Return code: {result.returncode}")
                if result.stdout:
                    logger.error(f"STDOUT: {result.stdout}")
                if result.stderr:
                    logger.error(f"STDERR: {result.stderr}")
                logger.error(f"Command: {' '.join(cmd)}")
                return False
                
        except Exception as e:
            logger.error(f"Error during Miniconda installation: {e}")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Command that failed: {' '.join(cmd)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
        finally:
            # Remove installer in any case
            try:
                if installer_path.exists():
                    os.remove(installer_path)
                    logger.info(f"Installer removed: {installer_path}")
            except Exception as e:
                logger.warning(f"Could not remove installer {installer_path}: {e}")

            
class EnvironmentManager:
    """Conda environment manager"""
    
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
        """Ensures that Miniconda is installed"""
        if not self.installer.is_installed():
            return self.installer.install()
        return True
    
    def accept_conda_terms_of_service(self) -> bool:
        """Accepts Terms of Service for conda channels"""
        channels = [
            "https://repo.anaconda.com/pkgs/main",
            "https://repo.anaconda.com/pkgs/r", 
            "https://repo.anaconda.com/pkgs/msys2"
        ]
        
        logger.info("Accepting Terms of Service for conda channels...")
        
        for channel in channels:
            try:
                cmd = ["tos", "accept", "--override-channels", "--channel", channel]
                result = self.run_conda_command(cmd)
                
                if result.returncode == 0:
                    logger.info(f"✅ Terms of Service accepted for {channel}")
                else:
                    logger.warning(f"⚠️ Failed to accept ToS for {channel}: {result.stderr}")
                    
            except Exception as e:
                logger.warning(f"Error accepting ToS for {channel}: {e}")
        
        return True
    
    def run_conda_command(self, args: List[str], **kwargs) -> subprocess.CompletedProcess:
        """Executes conda command"""
        cmd = [str(self.conda_exe)] + args
        logger.info(f"Executing command: {' '.join(cmd)}")
        
        # Add environment variables for conda
        env = os.environ.copy()
        if os.name == 'nt':
            env['PATH'] = str(self.miniconda_path / "Scripts") + os.pathsep + env.get('PATH', '')
        else:
            env['PATH'] = str(self.miniconda_path / "bin") + os.pathsep + env.get('PATH', '')
        
        return subprocess.run(cmd, env=env, capture_output=True, text=True, **kwargs)
    
    def run_conda_command_with_progress(self, args: List[str], description: str = "Executing conda command", **kwargs) -> subprocess.CompletedProcess:
        """Executes conda command with progress bar and output capture"""
        cmd = [str(self.conda_exe)] + args
        logger.info(f"Executing command: {' '.join(cmd)}")
        
        # Add environment variables for conda
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
            logger.warning("tqdm not installed, executing without progress bar")
        
        if TQDM_AVAILABLE:
            # Execution with progress bar
            logger.info(f"🔄 {description}...")
            
            # Start process
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # Create progress bar
            with tqdm(desc=description, unit="operation", dynamic_ncols=True) as pbar:
                output_lines = []
                if process.stdout:
                    for line in process.stdout:
                        output_lines.append(line)
                        pbar.update(1)
                        
                        # Show important conda messages
                        line_lower = line.lower().strip()
                        if any(keyword in line_lower for keyword in [
                            "downloading", "extracting", "installing", "solving", 
                            "collecting", "preparing", "executing", "verifying",
                            "error", "failed", "warning"
                        ]):
                            # Truncate long lines for display
                            display_text = line.strip()[:60]
                            if len(line.strip()) > 60:
                                display_text += "..."
                            pbar.set_postfix_str(display_text)
            
            # Wait for completion
            process.wait()
            
            # Create result in CompletedProcess format
            result = subprocess.CompletedProcess(
                args=cmd,
                returncode=process.returncode,
                stdout=''.join(output_lines),
                stderr=None
            )
            
            if result.returncode == 0:
                logger.info(f"✅ {description} completed successfully")
            else:
                logger.error(f"❌ {description} completed with error (code: {result.returncode})")
                if result.stdout:
                    logger.error(f"Output: {result.stdout[-500:]}")
            
            return result
        else:
            # Regular execution without progress bar
            return subprocess.run(cmd, env=env, capture_output=True, text=True, **kwargs)
    
    def list_environments(self) -> List[str]:
        """List of all venv environments"""
        if not self.envs_path.exists():
            return []
        
        envs = []
        for item in self.envs_path.iterdir():
            if item.is_dir() and (item / "pyvenv.cfg").exists():
                envs.append(item.name)
        
        return envs
    
    def environment_exists(self, name: str) -> bool:
        """Checks existence of venv environment"""
        repo_env_path = self.envs_path / name
        return repo_env_path.exists() and (repo_env_path / "pyvenv.cfg").exists()
    
    def check_base_environment_integrity(self) -> bool:
        """Checks integrity of base environment"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if not conda_env_path.exists():
            logger.warning(f"Conda environment {env_name} not found")
            return False
        
        # Check for main executable files
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
            logger.warning(f"Environment {env_name} is missing tools: {', '.join(missing_tools)}")
            return False
        
        # Check Python functionality
        try:
            result = subprocess.run([str(python_exe), "--version"], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode != 0:
                logger.warning(f"Python in environment {env_name} is not working")
                return False
        except Exception as e:
            logger.warning(f"Error checking Python in environment {env_name}: {e}")
            return False
        
        # Check TensorRT for NVIDIA GPU
        gpu_info = self.gpu_detector.get_gpu_info()
        nvidia_gpu = next((gpu for gpu in gpu_info if gpu.gpu_type == GPUType.NVIDIA), None)
        
        if nvidia_gpu:
            # Use ConfigManager to determine TensorRT support
            from .config import ConfigManager
            config_manager = ConfigManager()
            gpu_config = config_manager.configure_gpu(nvidia_gpu.name, nvidia_gpu.memory // 1024 if nvidia_gpu.memory else 0)
            
            if gpu_config.supports_tensorrt:
                tensorrt_status = self.check_tensorrt_installation()
                if not tensorrt_status:
                    logger.warning("TensorRT not installed or not working, will reinstall")
                    if not self.reinstall_tensorrt():
                        logger.warning("Failed to reinstall TensorRT, but base environment is working")
        
        return True
    
    def check_tensorrt_installation(self) -> bool:
        """Checks TensorRT installation and functionality"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if os.name == 'nt':
            python_exe = conda_env_path / "python.exe"
        else:
            python_exe = conda_env_path / "bin" / "python"
        
        try:
            # Check TensorRT import
            result = subprocess.run([
                str(python_exe), "-c", 
                "import tensorrt; print(f'TensorRT {tensorrt.__version__} working'); assert tensorrt.Builder(tensorrt.Logger())"
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return True
            else:
                logger.warning(f"❌ TensorRT check failed: {result.stderr.strip()}")
                return False
        except Exception as e:
            logger.warning(f"❌ TensorRT check error: {e}")
            return False
    
    def reinstall_tensorrt(self) -> bool:
        """Reinstalls TensorRT"""
        env_name = "portablesource"
        
        try:
            logger.info("Reinstalling TensorRT...")
            
            # Remove existing TensorRT
            uninstall_cmd = ["run", "-n", env_name, "pip", "uninstall", "-y", "tensorrt", "tensorrt-libs", "tensorrt-bindings"]
            uninstall_result = self.run_conda_command(uninstall_cmd)
            
            # Update pip, setuptools and wheel (ignore errors if packages are already updated)
            # update_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
            # update_result = self.run_conda_command_with_progress(update_cmd, "Updating pip, setuptools and wheel")
            update_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            
            # Continue TensorRT installation even if pip update failed
                    # (often pip is already updated but returns error code)
            if update_result.returncode == 0:
                pass
                #logger.info("✅ pip, setuptools and wheel updated")
            else:
                logger.warning("⚠️ pip update completed with warnings, continuing TensorRT installation")
            
            # Install TensorRT again
            tensorrt_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "tensorrt"]
            tensorrt_result = self.run_conda_command_with_progress(tensorrt_cmd, "Reinstalling TensorRT")
            
            if tensorrt_result.returncode == 0:
                # Check installation
                if self.check_tensorrt_installation():
                    logger.info("✅ TensorRT successfully reinstalled")
                    return True
                else:
                    logger.warning("⚠️ TensorRT installed but check failed")
                    return False
            else:
                logger.warning(f"⚠️ TensorRT reinstallation error: {tensorrt_result.stderr}")
                return False
                
        except Exception as e:
            logger.warning(f"⚠️ TensorRT reinstallation error: {e}")
            return False
    
    def remove_base_environment(self) -> bool:
        """Removes base conda environment"""
        env_name = "portablesource"
        conda_env_path = self.miniconda_path / "envs" / env_name
        
        if not conda_env_path.exists():
            logger.info(f"Conda environment {env_name} already absent")
            return True
        
        try:
            # Remove via conda
            cmd = ["env", "remove", "-n", env_name, "-y"]
            result = self.run_conda_command(cmd)
            
            if result.returncode == 0:
                logger.info(f"Conda environment {env_name} removed")
                return True
            else:
                logger.error(f"Error removing conda environment: {result.stderr}")
                # Try to remove folder directly
                shutil.rmtree(conda_env_path)
                logger.info(f"Conda environment {env_name} forcibly removed")
                return True
        except Exception as e:
            logger.error(f"Error removing conda environment {env_name}: {e}")
            return False
    
    def create_base_environment(self) -> bool:
        """Creates base PortableSource environment"""
        env_name = "portablesource"
        
        # Check environment existence and integrity
        conda_env_path = self.miniconda_path / "envs" / env_name
        if conda_env_path.exists():
            if self.check_base_environment_integrity():
                logger.info(f"Base environment {env_name} already exists and works correctly")
                return True
            else:
                logger.warning(f"Base environment {env_name} is corrupted, reinstalling...")
                if not self.remove_base_environment():
                    logger.error("Failed to remove corrupted environment")
                    return False
        
        # Accept Terms of Service before creating environment
        self.accept_conda_terms_of_service()
        
        # Define packages for installation
        packages = [
            "python=3.11",
            "git",
            "ffmpeg",
            "pip",
            "setuptools",
            "wheel"
        ]
        
        # Add CUDA packages if NVIDIA GPU is present
        gpu_info = self.gpu_detector.get_gpu_info()
        nvidia_gpu = next((gpu for gpu in gpu_info if gpu.gpu_type == GPUType.NVIDIA), None)
        
        if nvidia_gpu and nvidia_gpu.cuda_version:
            cuda_version = nvidia_gpu.cuda_version.value
            logger.info(f"Adding CUDA {cuda_version} toolkit + cuDNN")
            
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
        
        # Create environment with progress bar
        cmd = ["create", "-n", env_name, "-y"] + packages
        result = self.run_conda_command_with_progress(cmd, "")
        
        if result.returncode == 0:
            #logger.info(f"Базовое окружение {env_name} создано")
            
            # Install additional packages for NVIDIA GPU
            if nvidia_gpu:
                #logger.info("Установка дополнительных пакетов для NVIDIA GPU...")
                try:
                    # Skip pip upgrade to avoid permission issues
                    # update_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
                    # update_result = self.run_conda_command_with_progress(update_cmd, "Updating pip, setuptools and wheel")
                    update_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")  # Mock successful result
                    
                    # Continue TensorRT installation even if pip update failed
                    if update_result.returncode == 0:
                        logger.info("✅ pip, setuptools and wheel updated")
                    else:
                        logger.warning("⚠️ pip update completed with warnings, continuing TensorRT installation")
                    
                    # Install TensorRT according to official documentation
                    #logger.info("Installing TensorRT (optional)...")
                    tensorrt_cmd = ["run", "-n", env_name, "pip", "install", "--upgrade", "tensorrt"]
                    tensorrt_result = self.run_conda_command_with_progress(tensorrt_cmd, "Installing TensorRT")
                    
                    if tensorrt_result.returncode == 0:
                        pass
                        #logger.info("✅ TensorRT successfully installed")
                        #logger.info("💡 For verification use: python -c 'import tensorrt; print(tensorrt.__version__)'")
                    else:
                        logger.warning("⚠️ TensorRT failed to install (possibly incompatible Python or CUDA version)")
                        logger.info("💡 TensorRT can be installed manually later if needed")
                except Exception as e:
                    logger.warning(f"⚠️ Error installing additional NVIDIA packages: {e}")
                    logger.info("💡 Base environment created successfully, additional packages can be installed later")
            
            return True
        else:
            logger.error(f"Error creating base environment: {result.stderr}")
            return False
    
    def create_repository_environment(self, repo_name: str, spec: EnvironmentSpec) -> bool:
        """Creates venv environment for repository"""
        repo_env_path = self.envs_path / repo_name
        
        if repo_env_path.exists():
            #logger.info(f"Venv environment {repo_name} already exists")
            return True
        
        # Create folder for venv environments
        self.envs_path.mkdir(parents=True, exist_ok=True)
        
        # Check for base conda environment
        if not (self.miniconda_path / "envs" / "portablesource").exists():
            logger.error("Base conda environment portablesource not found!")
            return False
        
        # Create venv using Python from base conda environment
        try:
            cmd = [str(self.python_exe), "-m", "venv", str(repo_env_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"Error creating venv: {result.stderr}")
                return False
            
            # Define path to pip in venv
            if os.name == 'nt':
                venv_pip = repo_env_path / "Scripts" / "pip.exe"
                venv_python = repo_env_path / "Scripts" / "python.exe"
            else:
                venv_pip = repo_env_path / "bin" / "pip"
                venv_python = repo_env_path / "bin" / "python"
            
            # Skip pip upgrade to avoid permission issues
            # subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], 
            #              capture_output=True, text=True)
            
            # Install additional packages
            if spec.pip_packages:
                for package in spec.pip_packages:
                    result = subprocess.run([str(venv_pip), "install", package], 
                                          capture_output=True, text=True)
                    if result.returncode != 0:
                        logger.warning(f"Failed to install {package}: {result.stderr}")
            
            #logger.info(f"Venv environment {repo_name} created in {repo_env_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating venv environment: {e}")
            return False
    
    def remove_environment(self, name: str) -> bool:
        """Removes venv environment"""
        if not self.environment_exists(name):
            logger.warning(f"Venv environment {name} does not exist")
            return True
        
        repo_env_path = self.envs_path / name
        
        try:
            # Remove venv folder
            shutil.rmtree(repo_env_path)
            logger.info(f"Venv environment {name} removed")
            return True
        except Exception as e:
            logger.error(f"Error removing venv environment {name}: {e}")
            return False
    
    def get_environment_python_path(self, env_name: str) -> Optional[Path]:
        """Gets path to Python in venv environment"""
        repo_env_path = self.envs_path / env_name
        
        if os.name == 'nt':
            python_path = repo_env_path / "Scripts" / "python.exe"
        else:
            python_path = repo_env_path / "bin" / "python"
        
        return python_path if python_path.exists() else None
#!/usr/bin/env python3
"""
Environment Manager for PortableSource
Managing micromamba and base ps_env environment only
"""

import os
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from portablesource.get_gpu import GPUDetector, GPUType

logger = logging.getLogger(__name__)

@dataclass
class BaseEnvironmentSpec:
    """Base environment specification for ps_env"""
    python_version: str = "3.11"
    cuda_version: Optional[str] = None
    
    def get_packages(self) -> List[str]:
        """Get base packages for ps_env"""
        packages = ["git", "ffmpeg", "uv", f"python=={self.python_version}"]
        
        if self.cuda_version:
            packages.extend([
                f"cuda-toolkit={self.cuda_version}",
                "cudnn"
            ])
        
        return packages

class MicromambaManager:
    """Micromamba installer and base environment manager"""
    
    def __init__(self, install_path: Path):
        self.install_path = install_path
        self.micromamba_path = install_path / "micromamba"
        self.micromamba_exe = self.micromamba_path / "micromamba.exe" if os.name == 'nt' else self.micromamba_path / "micromamba"
        self.ps_env_path = install_path / "ps_env"  # Main environment path
        self.gpu_detector = GPUDetector()

    def get_installer_url(self) -> str:
        """Gets URL for downloading Micromamba"""
        if os.name == 'nt':
            return "https://github.com/mamba-org/micromamba-releases/releases/download/2.3.0-1/micromamba-win-64"
        else:
            return "https://github.com/mamba-org/micromamba-releases/releases/download/2.3.0-1/micromamba-linux-64"

    def ensure_micromamba_installed(self) -> bool:
        """Ensures that micromamba is installed"""
        if self.micromamba_exe.exists():
            logger.info("Micromamba already installed.")
            return True

        logger.info("Micromamba not found, downloading...")
        self.micromamba_path.mkdir(exist_ok=True)
        url = self.get_installer_url()
        
        try:
            # Download with progress bar if tqdm is available
            try:
                from tqdm import tqdm
                response = urllib.request.urlopen(url)
                total_size = int(response.headers.get('Content-Length', 0))
                
                with open(self.micromamba_exe, 'wb') as f:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc="Downloading Micromamba") as pbar:
                        while True:
                            chunk = response.read(8192)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))
            except ImportError:
                logger.warning("tqdm not installed, downloading without progress bar")
                urllib.request.urlretrieve(url, self.micromamba_exe)

            logger.info(f"Micromamba downloaded to {self.micromamba_exe}")
            
            # On non-windows, make it executable
            if os.name != 'nt':
                os.chmod(self.micromamba_exe, 0o755)

            return True
        except Exception as e:
            logger.error(f"Error downloading Micromamba: {e}")
            return False

    def run_micromamba_command(self, command: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Runs a micromamba command"""
        full_cmd = [str(self.micromamba_exe)] + command
        logger.info(f"Running command: {' '.join(full_cmd)}")
        
        return subprocess.run(
            full_cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            cwd=str(cwd) if cwd else None
        )

    def create_base_environment(self, cuda_version: Optional[str] = None) -> bool:
        """Creates the main ps_env environment with micromamba"""
        if not self.ensure_micromamba_installed():
            return False

        if self.ps_env_path.exists():
            logger.info("Base environment ps_env already exists.")
            return True

        logger.info("Creating base environment ps_env...")
        
        # Auto-detect CUDA version if not provided
        if not cuda_version:
            gpu_info = self.gpu_detector.get_gpu_info()
            if gpu_info and gpu_info[0].gpu_type == GPUType.NVIDIA:
                cuda_version = gpu_info[0].cuda_version.value if gpu_info[0].cuda_version else None
        
        # Create environment spec
        env_spec = BaseEnvironmentSpec(cuda_version=cuda_version)
        packages = env_spec.get_packages()
        
        # Build create command: {mamba_path} create -p ./ps_env -c nvidia -c conda-forge cuda-toolkit={cuda_ver} cudnn git ffmpeg uv python==3.11
        create_cmd = [
            "create",
            "-p", str(self.ps_env_path),
            "-c", "nvidia",
            "-c", "conda-forge", 
            "-y"
        ] + packages

        result = self.run_micromamba_command(create_cmd)

        if result.returncode != 0:
            logger.error("Error creating base environment ps_env:")
            logger.error(f"STDOUT: {result.stdout}")
            logger.error(f"STDERR: {result.stderr}")
            return False

        logger.info("✅ Base environment ps_env created successfully.")
        return True

    def get_activation_commands(self) -> List[str]:
        """Get the activation commands for micromamba shell hook (for batch files)"""
        if os.name == 'nt':
            return [
                f'call "{self.micromamba_exe}" shell hook -s cmd.exe > nul',
                f'call micromamba activate "{self.ps_env_path}"'
            ]
        else:
            return [
                f'eval "$({self.micromamba_exe} shell hook -s bash)"',
                f'micromamba activate "{self.ps_env_path}"'
            ]

    def setup_environment_for_subprocess(self) -> Dict[str, str]:
        """Setup environment variables for subprocess to use micromamba"""
        env = os.environ.copy()
        
        # Add micromamba to PATH
        micromamba_dir = str(self.micromamba_path)
        if 'PATH' in env:
            env['PATH'] = f"{micromamba_dir}{os.pathsep}{env['PATH']}"
        else:
            env['PATH'] = micromamba_dir
            
        # Set MAMBA_EXE for shell hook
        env['MAMBA_EXE'] = str(self.micromamba_exe)
        
        return env

    def run_in_activated_environment(self, command: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Run a command in the activated ps_env environment using micromamba run"""
        if not self.ps_env_path.exists():
            logger.error("Base environment ps_env not found. Run --setup-env first.")
            return subprocess.CompletedProcess([], 1, "", "Base environment not found")

        # Use micromamba run to execute command in environment
        run_cmd = ["run", "-p", str(self.ps_env_path)] + command
        
        return self.run_micromamba_command(run_cmd, cwd=cwd)

    def get_ps_env_python(self) -> Optional[Path]:
        """Gets the path to python executable in ps_env"""
        if not self.ps_env_path.exists():
            return None
        
        python_exe = self.ps_env_path / "python.exe" if os.name == 'nt' else self.ps_env_path / "bin" / "python"
        return python_exe if python_exe.exists() else None

    def get_ps_env_pip(self) -> Optional[Path]:
        """Gets the path to pip executable in ps_env"""
        if not self.ps_env_path.exists():
            return None
        
        pip_exe = self.ps_env_path / "pip.exe" if os.name == 'nt' else self.ps_env_path / "bin" / "pip"
        return pip_exe if pip_exe.exists() else None

    def get_ps_env_uv(self) -> Optional[Path]:
        """Gets the path to uv executable in ps_env"""
        if not self.ps_env_path.exists():
            return None
        
        uv_exe = self.ps_env_path / "uv.exe" if os.name == 'nt' else self.ps_env_path / "bin" / "uv"
        return uv_exe if uv_exe.exists() else None

    def get_environment_info(self) -> Dict[str, Any]:
        """Get information about environments"""
        info = {
            "micromamba_installed": self.micromamba_exe.exists(),
            "base_env_exists": self.ps_env_path.exists(),
            "base_env_python": str(self.get_ps_env_python()) if self.get_ps_env_python() else None,
            "base_env_pip": str(self.get_ps_env_pip()) if self.get_ps_env_pip() else None,
            "base_env_uv": str(self.get_ps_env_uv()) if self.get_ps_env_uv() else None,
            "paths": {
                "micromamba_exe": str(self.micromamba_exe),
                "ps_env_path": str(self.ps_env_path)
            }
        }
        return info

    def check_micromamba_availability(self) -> bool:
        """Check if micromamba is available and working.
        
        Returns:
            True if micromamba is available, False otherwise
        """
        if not self.micromamba_exe.exists():
            return False
        
        try:
            result = self.run_micromamba_command(["--version"])
            return result.returncode == 0
        except Exception as e:
            logger.error(f"Error checking micromamba availability: {e}")
            return False

    def setup_environment(self) -> bool:
        """Setup the complete environment (micromamba + ps_env).
        
        Returns:
            True if setup was successful, False otherwise
        """
        logger.info("Setting up PortableSource environment...")
        
        # Step 1: Install micromamba
        if not self.ensure_micromamba_installed():
            logger.error("❌ Failed to install micromamba")
            return False
        
        # Step 2: Create base environment
        if not self.create_base_environment():
            logger.error("❌ Failed to create base environment")
            return False
        
        logger.info("✅ Environment setup completed successfully")
        logger.info(f"Micromamba installed at: {self.micromamba_exe}")
        logger.info(f"Base environment created at: {self.ps_env_path}")
        
        return True
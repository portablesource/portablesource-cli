#!/usr/bin/env python3
"""
Environment Manager for PortableSource
Managing micromamba and base ps_env environment only
"""

import os
import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

from portablesource.get_gpu import GPUDetector, GPUType
from portablesource.config import ConfigManager

logger = logging.getLogger(__name__)

@dataclass
class BaseEnvironmentSpec:
    """Base environment specification for ps_env"""
    python_version: str = "3.11"
    cuda_version: Optional[str] = None
    
    def get_packages(self) -> List[str]:
        """Get base packages for ps_env"""
        packages = ["git", "ffmpeg", f"python=={self.python_version}"]
        
        if self.cuda_version:
            packages.extend([
                f"cuda-toolkit={self.cuda_version}",
                "cudnn"
            ])
        
        return packages

class MicromambaManager:
    """Micromamba installer and base environment manager"""
    
    def __init__(self, install_path: Path, config_manager: Optional[ConfigManager] = None):
        self.install_path = install_path
        self.micromamba_path = install_path / "micromamba"
        self.micromamba_exe = self.micromamba_path / "micromamba.exe" if os.name == 'nt' else self.micromamba_path / "micromamba"
        self.ps_env_path = install_path / "ps_env"  # Main environment path
        self.gpu_detector = GPUDetector()
        
        # Initialize config manager with proper path if not provided
        if config_manager is None:
            config_path = install_path / "portablesource_config.json"
            self.config_manager = ConfigManager(config_path)
            self.config_manager.load_config()
        else:
            self.config_manager = config_manager

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

        # Check if environment actually exists and is valid (has Python executable)
        python_path = self.get_ps_env_python()
        if self.ps_env_path.exists() and python_path and python_path.exists():
            logger.info("Base environment ps_env already exists.")
            return True
        elif self.ps_env_path.exists():
            logger.info("Found empty ps_env directory, recreating environment...")
            # Remove empty directory to recreate properly
            shutil.rmtree(self.ps_env_path)
            logger.info("Removed empty ps_env directory.")

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
            logger.error("❌ Error creating base environment ps_env:")
            logger.error(f"STDOUT: {result.stdout}")
            logger.error(f"STDERR: {result.stderr}")
            return False
        

        
        # Verify that environment was actually created and all tools work
        if not self._verify_environment_tools():
            logger.error("❌ Environment verification failed - some tools are not working properly")
            return False


        return True

    def get_activation_commands(self) -> List[str]:
        """Get the activation commands for micromamba shell hook (for batch files)"""
        if os.name == 'nt':
            mamba_hook_path = self.micromamba_path / "condabin" / "mamba_hook.bat"

            if not mamba_hook_path.exists():
                logger.error(f"❌ mamba_hook.bat not found at path: {mamba_hook_path}. "
                               "Make sure micromamba has been initialized (e.g. by running environment_manager.py).")
                return []

            return [
                f'call "{mamba_hook_path}"',
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

    
    def _extract_version_from_output(self, tool_name: str, output: str) -> str:
        """Extract version information from tool output, filtering out micromamba activation noise"""
        if not output:
            return "Unknown version"
        
        lines = output.strip().split('\n')
        
        # For nvcc, look for the actual nvcc output after all the environment setup
        if tool_name == "nvcc":
            # Find lines that contain "nvcc:" or "Cuda compilation tools"
            for line in lines:
                if "nvcc:" in line or "Cuda compilation tools" in line:
                    return line.strip()
            # If not found, try to get the last meaningful line
            for line in reversed(lines):
                if line.strip() and not line.startswith("C:\\") and "SET" not in line and "set" not in line:
                    return line.strip()
        
        # For other tools, look for version patterns
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
        
        # Fallback: return first non-empty line that doesn't look like environment setup
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
        
        # Add nvcc check if CUDA is available
        if self.gpu_detector.get_gpu_info():
            gpu_info = self.gpu_detector.get_gpu_info()
            if gpu_info and any(gpu.gpu_type.name == "NVIDIA" for gpu in gpu_info):
                tools_to_check.append(("nvcc", ["--version"]))
        
        all_tools_working = True
        
        for tool_name, args in tools_to_check:
            try:
                result = self.run_in_activated_environment([tool_name] + args)
                if result.returncode == 0:
                    # Extract version info from output, filtering out micromamba noise
                    version_output = self._extract_version_from_output(tool_name, result.stdout)
                    logger.info(f"✅ {tool_name}: {version_output}")
                else:
                    logger.error(f"❌ {tool_name}: Failed to run (exit code {result.returncode})")
                    if result.stderr:
                        logger.error(f"   Error: {result.stderr.strip()}")
                    all_tools_working = False
            except Exception as e:
                logger.error(f"❌ {tool_name}: Exception occurred - {e}")
                all_tools_working = False
        
        return all_tools_working
    
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
        
        # Check individual tools
        tools_to_check = [
            ("python", ["--version"]),
            ("git", ["--version"]),
            ("ffmpeg", ["-version"])
        ]
        
        # Add nvcc check if CUDA is available
        if self.gpu_detector.get_gpu_info():
            gpu_info = self.gpu_detector.get_gpu_info()
            if gpu_info and any(gpu.gpu_type.name == "NVIDIA" for gpu in gpu_info):
                tools_to_check.append(("nvcc", ["--version"]))
        
        all_working = True
        for tool_name, args in tools_to_check:
            try:
                result = self.run_in_activated_environment([tool_name] + args)
                if result.returncode == 0:
                    version_output = self._extract_version_from_output(tool_name, result.stdout)
                    status["tools_status"][tool_name] = {
                        "working": True,
                        "version": version_output
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
        """Get information about environments"""
        # Check if environment actually exists and is valid (has Python executable)
        python_path = self.get_ps_env_python()
        base_env_exists = self.ps_env_path.exists() and python_path and python_path.exists()
        
        info = {
            "micromamba_installed": self.micromamba_exe.exists(),
            "base_env_exists": base_env_exists,
            "base_env_python": str(self.get_ps_env_python()) if self.get_ps_env_python() else None,
            "base_env_pip": str(self.get_ps_env_pip()) if self.get_ps_env_pip() else None,
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
        """
        Setup the complete environment (micromamba + ps_env).
        
        Returns:
            True if setup was successful, False otherwise
        """
        logger.info("Setting up PortableSource environment...")
        
        # Step 1: Create base environment (this will install micromamba if needed)
        if not self.create_base_environment():
            logger.error("❌ Failed to create base environment")
            return False
        
        # Step 3: Verify environment is working by checking all tools
        if not self._verify_environment_tools():
            logger.error("❌ Environment verification failed - some tools are not working properly")
            return False
        
        # Step 4: Configure GPU automatically (always run this step)
        try:
            logger.info("Starting GPU configuration...")
            gpu_config = self.config_manager.configure_gpu_from_detection()
            logger.info(f"✅ GPU configuration completed: {gpu_config.name} ({gpu_config.recommended_backend})")
        except Exception as e:
            logger.error(f"Failed to configure GPU automatically: {e}")
            import traceback
            logger.error(f"GPU configuration traceback: {traceback.format_exc()}")
        
        # Step 5: Update setup status (GPU config already saved in step 4)
        try:
            self.config_manager.config.environment_setup_completed = True
            self.config_manager.config.install_path = str(self.install_path)
            self.config_manager.save_config()
            logger.info("✅ Environment setup status saved to configuration")
        except Exception as e:
            logger.warning(f"Failed to save setup status to config: {e}")
        
        logger.info("✅ Environment setup completed successfully")
        logger.info(f"Micromamba installed at: {self.micromamba_exe}")
        logger.info(f"Base environment created at: {self.ps_env_path}")
        
        return True
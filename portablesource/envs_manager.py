#!/usr/bin/env python3
"""
Environment Manager for PortableSource
Managing portable tools in ps_env directory
"""

import os
import logging
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        self.ps_env_path = install_path / "ps_env"  # Main environment path
        self.gpu_detector = GPUDetector()
        
        # Initialize config manager with proper path if not provided
        if config_manager is None:
            config_path = install_path / "portablesource_config.json"
            self.config_manager = ConfigManager(config_path)
            self.config_manager.load_config()
        else:
            self.config_manager = config_manager
        
        # Define portable tools specifications
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

    def download_file(self, url: str, destination: Path, description: str = "Downloading") -> bool:
        """Download a file with progress bar"""
        try:
            # Download with progress bar if tqdm is available
            try:
                from tqdm import tqdm
                response = urllib.request.urlopen(url)
                total_size = int(response.headers.get('Content-Length', 0))
                desc = f"Downloading {description}"
                with open(destination, 'wb') as f:
                    with tqdm(total=total_size, unit='B', unit_scale=True, desc=desc) as pbar:
                        while True:
                            chunk = response.read(16384)
                            if not chunk:
                                break
                            f.write(chunk)
                            pbar.update(len(chunk))
            except ImportError:
                logger.warning("tqdm not installed, downloading without progress bar")
                urllib.request.urlretrieve(url, destination)

            logger.info(f"Downloaded {description} to {destination}")
            return True

        except Exception as e:
            logger.error(f"Error downloading {description}: {e}")
            return False
    
    def download_7z_executable(self) -> Optional[Path]:
        """Download 7z.exe if not available in system"""
        try:
            # Check if 7z is already available in system
            if shutil.which("7z"):
                return None  # System 7z is available
            
            # Download 7z.exe to ps_env directory
            seven_zip_url = "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe"
            seven_zip_path = self.ps_env_path / "7z.exe"
            
            if seven_zip_path.exists():
                return seven_zip_path  # Already downloaded
            
            logger.info("Downloading 7z.exe...")
            if self.download_file(seven_zip_url, seven_zip_path, "7z.exe"):
                return seven_zip_path
            else:
                logger.error("Failed to download 7z.exe")
                return None
                
        except Exception as e:
            logger.error(f"Error downloading 7z.exe: {e}")
            return None
    
    def extract_7z_archive(self, archive_path: Path, extract_to: Path) -> bool:
        """Extract 7z archive using external 7zip"""
        try:
            # First try to use system 7zip or download 7z.exe
            seven_zip_exe = None
            
            if shutil.which("7z"):
                seven_zip_exe = "7z"
            else:
                # Try to download 7z.exe
                downloaded_7z = self.download_7z_executable()
                if downloaded_7z:
                    seven_zip_exe = str(downloaded_7z)
            
            if seven_zip_exe:
                result = subprocess.run(
                    [seven_zip_exe, "x", str(archive_path), f"-o{extract_to}", "-y"],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    logger.info(f"Extracted {archive_path} to {extract_to} using 7zip")
                    return True
                else:
                    logger.error(f"7zip extraction failed: {result.stderr}")
                    return False
            else:
                logger.error("7zip executable not found and could not be downloaded")
                return False
                
        except Exception as e:
            logger.error(f"Error extracting {archive_path}: {e}")
            return False
    
    def _fix_nested_extraction(self, extract_path: Path, tool_name: str) -> None:
        """Fix nested folder structure when archive contains a subfolder with the same name"""
        try:
            # Check if there's a nested folder with the same name as the tool
            nested_folder = extract_path / tool_name
            if nested_folder.exists() and nested_folder.is_dir():
                # Check if the nested folder contains the actual content
                nested_contents = list(nested_folder.iterdir())
                extract_contents = [item for item in extract_path.iterdir() if item != nested_folder]
                
                # If the nested folder has content and the extract path has only the nested folder
                if nested_contents and len(extract_contents) == 0:
                    logger.info(f"Fixing nested folder structure for {tool_name}")
                    
                    # Create a temporary directory to move contents
                    temp_dir = extract_path.parent / f"{tool_name}_temp"
                    temp_dir.mkdir(exist_ok=True)
                    
                    # Move all contents from nested folder to temp directory
                    for item in nested_contents:
                        shutil.move(str(item), str(temp_dir / item.name))
                    
                    # Remove the now-empty nested folder
                    nested_folder.rmdir()
                    
                    # Move contents from temp directory to extract path
                    for item in temp_dir.iterdir():
                        shutil.move(str(item), str(extract_path / item.name))
                    
                    # Remove temp directory
                    temp_dir.rmdir()
                    
                    logger.info(f"Fixed nested folder structure for {tool_name}")
                    
        except Exception as e:
            logger.warning(f"Failed to fix nested extraction for {tool_name}: {e}")
    
    def _fix_cuda_nested_extraction(self, extract_path: Path, cuda_version) -> None:
        """Fix CUDA-specific nested folder structure issues"""
        try:
            # Common CUDA nested folder patterns to check
            possible_nested_folders = [
                f"CUDA_{cuda_version.value}",  # e.g., CUDA_124
                f"cuda_{cuda_version.value}",  # e.g., cuda_124
                "CUDA",  # Generic CUDA folder
                "cuda"   # Generic cuda folder
            ]
            
            for folder_name in possible_nested_folders:
                nested_folder = extract_path / folder_name
                if nested_folder.exists() and nested_folder.is_dir():
                    # Check if this nested folder contains bin directory (CUDA content)
                    if (nested_folder / "bin").exists():
                        logger.info(f"Found CUDA content in nested folder: {folder_name}")
                        
                        # Check if extract_path is empty except for this nested folder
                        extract_contents = [item for item in extract_path.iterdir() if item != nested_folder]
                        
                        if len(extract_contents) == 0:
                            logger.info(f"Moving CUDA content from {folder_name} to parent directory")
                            
                            # Create a temporary directory to move contents
                            temp_dir = extract_path.parent / f"cuda_temp_{cuda_version.value}"
                            temp_dir.mkdir(exist_ok=True)
                            
                            # Move all contents from nested folder to temp directory
                            for item in nested_folder.iterdir():
                                shutil.move(str(item), str(temp_dir / item.name))
                            
                            # Remove the now-empty nested folder
                            nested_folder.rmdir()
                            
                            # Move contents from temp directory to extract path
                            for item in temp_dir.iterdir():
                                shutil.move(str(item), str(extract_path / item.name))
                            
                            # Remove temp directory
                            temp_dir.rmdir()
                            
                            logger.info(f"[OK] Fixed CUDA nested folder structure for {folder_name}")
                            return  # Exit after fixing the first match
                        else:
                            logger.info(f"CUDA folder {folder_name} found but extract path has other contents, skipping")
                    
            # If no nested folders found, check if CUDA is properly extracted
            if not (extract_path / "bin").exists():
                logger.warning(f"CUDA extraction may be incomplete - no bin directory found in {extract_path}")
                
        except Exception as e:
            logger.warning(f"Failed to fix CUDA nested extraction: {e}")
    
    def install_tool(self, tool_name: str) -> bool:
        """Install a specific tool"""
        if tool_name not in self.tool_specs:
            logger.error(f"Unknown tool: {tool_name}")
            return False
        
        tool_spec = self.tool_specs[tool_name]
        extract_path = tool_spec.get_full_extract_path(self.ps_env_path)
        executable_path = tool_spec.get_full_executable_path(self.ps_env_path)
        
        # Check if tool is already installed
        if executable_path.exists():
            logger.info(f"{tool_name} already installed at {executable_path}")
            return True
        
        # Create ps_env directory if it doesn't exist
        self.ps_env_path.mkdir(parents=True, exist_ok=True)
        
        # Download archive
        archive_name = f"{tool_name}.7z"
        archive_path = self.ps_env_path / archive_name
        
        logger.info(f"Downloading {tool_name}...")
        if not self.download_file(tool_spec.url, archive_path, f"{tool_name}"):
            return False
        
        # Extract archive
        logger.info(f"Extracting {tool_name}...")
        if not self.extract_7z_archive(archive_path, extract_path):
            return False
        
        # Handle nested folder structure (when archive contains a subfolder with the same name)
        self._fix_nested_extraction(extract_path, tool_name)
        
        # Clean up archive
        try:
            archive_path.unlink()
            logger.info(f"Cleaned up {archive_name}")
        except Exception as e:
            logger.warning(f"Failed to clean up {archive_name}: {e}")
        
        # Verify installation
        if executable_path.exists():
            logger.info(f"[OK] {tool_name} installed successfully at {executable_path}")
            return True
        else:
            logger.error(f"[ERROR] {tool_name} installation failed - executable not found at {executable_path}")
            return False

    def install_cuda(self) -> bool:
        """Install CUDA based on GPU configuration"""
        if (not self.config_manager.config or 
            not self.config_manager.config.gpu_config or 
            not self.config_manager.config.gpu_config.cuda_version):
            logger.info("No CUDA version configured, skipping CUDA installation")
            return True
        
        cuda_version = self.config_manager.config.gpu_config.cuda_version
        cuda_download_link = self.config_manager.get_cuda_download_link(cuda_version)
        
        if not cuda_download_link:
            logger.error(f"No download link available for CUDA version {cuda_version.value}")
            return False
        
        cuda_extract_path = self.ps_env_path / "CUDA"
        cuda_archive_name = f"cuda_{cuda_version.value}.7z"
        cuda_archive_path = self.ps_env_path
        
        # Check if CUDA is already installed
        if cuda_extract_path.exists() and (cuda_extract_path / "bin").exists():
            logger.info(f"CUDA {cuda_version.value} already installed at {cuda_extract_path}")
            return True
        
        # Create ps_env directory if it doesn't exist
        self.ps_env_path.mkdir(parents=True, exist_ok=True)
        
        # Download CUDA archive
        logger.info(f"Downloading CUDA {cuda_version.value}...")
        if not self.download_file(cuda_download_link, cuda_archive_path, f"CUDA {cuda_version.value}"):
            return False
        
        # Extract CUDA archive
        logger.info(f"Extracting CUDA {cuda_version.value}...")
        if not self.extract_7z_archive(cuda_archive_path, cuda_extract_path):
            return False
        
        # Handle CUDA-specific nested folder structure
        self._fix_cuda_nested_extraction(cuda_extract_path, cuda_version)
        
        # Clean up archive
        try:
            cuda_archive_path.unlink()
            logger.info(f"Cleaned up {cuda_archive_name}")
        except Exception as e:
            logger.warning(f"Failed to clean up {cuda_archive_name}: {e}")
        
        # Verify CUDA installation
        if cuda_extract_path.exists() and (cuda_extract_path / "bin").exists():
            logger.info(f"[OK] CUDA {cuda_version.value} installed successfully at {cuda_extract_path}")
            # Update CUDA paths in config
            self.config_manager.configure_cuda_paths()
            return True
        else:
            logger.error(f"[ERROR] CUDA {cuda_version.value} installation failed")
            return False
    
    def _download_cuda_only(self, cuda_version) -> Optional[Path]:
        """Download CUDA archive only (for parallel processing)"""
        try:
            cuda_download_link = self.config_manager.get_cuda_download_link(cuda_version)
            if not cuda_download_link:
                logger.error(f"No download link available for CUDA {cuda_version.value}")
                return None
            
            cuda_archive_name = f"cuda_{cuda_version.value.replace('.', '_')}.7z"
            cuda_archive_path = self.ps_env_path / cuda_archive_name
            
            # Check if already downloaded
            if cuda_archive_path.exists():
                logger.info(f"CUDA {cuda_version.value} archive already exists")
                return cuda_archive_path
            
            # Download CUDA archive
            logger.info(f"Downloading CUDA {cuda_version.value}...")
            if self.download_file(cuda_download_link, cuda_archive_path, f"CUDA {cuda_version.value}"):
                return cuda_archive_path
            else:
                return None
        except Exception as e:
            logger.error(f"Failed to download CUDA: {e}")
            return None
    
    def _extract_cuda(self, cuda_archive_path: Path, cuda_version) -> bool:
        """Extract CUDA archive (for parallel processing)"""
        try:
            # CUDA should be extracted to ps_env/CUDA directory
            cuda_extract_path = self.ps_env_path / "CUDA"
            
            # Check if already extracted
            if cuda_extract_path.exists() and (cuda_extract_path / "bin").exists():
                logger.info(f"CUDA {cuda_version.value} already extracted")
                # Update CUDA paths in config
                self.config_manager.configure_cuda_paths()
                return True
            
            # Check if old versioned folder exists and rename it
            old_cuda_path = self.ps_env_path / f"cuda_{cuda_version.value.replace('.', '_')}"
            if old_cuda_path.exists() and (old_cuda_path / "bin").exists():
                logger.info(f"Renaming existing CUDA folder from {old_cuda_path} to {cuda_extract_path}")
                try:
                    if cuda_extract_path.exists():
                        import shutil
                        shutil.rmtree(cuda_extract_path)
                    old_cuda_path.rename(cuda_extract_path)
                    logger.info(f"[OK] CUDA {cuda_version.value} folder renamed successfully")
                    # Update CUDA paths in config
                    self.config_manager.configure_cuda_paths()
                    return True
                except Exception as e:
                    logger.error(f"Failed to rename CUDA folder: {e}")
                    return False
            
            # Ensure 7z.exe is available (should already be downloaded by this point)
            import shutil
            if not shutil.which("7z"):
                seven_zip_path = self.ps_env_path / "7z.exe"
                if not seven_zip_path.exists():
                    logger.error("7z.exe not found for CUDA extraction")
                    return False
            
            # Extract CUDA archive directly to CUDA folder
            if not self.extract_7z_archive(cuda_archive_path, cuda_extract_path):
                return False
            
            # Handle CUDA-specific nested folder structure
            self._fix_cuda_nested_extraction(cuda_extract_path, cuda_version)
            
            # Clean up archive
            try:
                cuda_archive_path.unlink()
                logger.info(f"Cleaned up CUDA archive")
            except Exception as e:
                logger.warning(f"Failed to clean up CUDA archive: {e}")
            
            # Verify CUDA installation
            if cuda_extract_path.exists() and (cuda_extract_path / "bin").exists():
                logger.info(f"[OK] CUDA {cuda_version.value} extracted successfully")
                # Update CUDA paths in config
                self.config_manager.configure_cuda_paths()
                return True
            else:
                logger.error(f"[ERROR] CUDA {cuda_version.value} extraction failed")
                return False
        except Exception as e:
            logger.error(f"Failed to extract CUDA: {e}")
            return False

    def _install_tools_parallel(self, tools_to_install: List[str]) -> bool:
        """Install multiple tools in parallel with optimized download and extraction"""
        logger.info(f"Installing tools in parallel: {', '.join(tools_to_install)}")
        
        # Pre-download 7z.exe once to avoid conflicts during parallel extraction
        if not shutil.which("7z"):
            seven_zip_path = self.download_7z_executable()
            if not seven_zip_path:
                logger.error("Failed to download 7z.exe for extraction")
                return False
        
        # Phase 1: Download all archives in parallel
        download_tasks = []
        archive_paths = {}
        
        with ThreadPoolExecutor(max_workers=len(tools_to_install)) as executor:
            for tool_name in tools_to_install:
                if tool_name not in self.tool_specs:
                    logger.error(f"Unknown tool: {tool_name}")
                    return False
                
                tool_spec = self.tool_specs[tool_name]
                executable_path = tool_spec.get_full_executable_path(self.ps_env_path)
                
                # Check if tool is already installed
                if executable_path.exists():
                    logger.info(f"{tool_name} already installed at {executable_path}")
                    continue
                
                # Prepare download
                archive_name = f"{tool_name}.7z"
                archive_path = self.ps_env_path / archive_name
                archive_paths[tool_name] = archive_path
                
                # Submit download task
                future = executor.submit(self._download_tool, tool_name, tool_spec.url, archive_path)
                download_tasks.append((tool_name, future))
            
            # Wait for all downloads to complete
            download_results = {}
            for tool_name, future in download_tasks:
                try:
                    download_results[tool_name] = future.result()
                    if download_results[tool_name]:
                        logger.info(f"[OK] {tool_name} download completed")
                    else:
                        logger.error(f"[ERROR] {tool_name} download failed")
                        return False
                except Exception as e:
                    logger.error(f"[ERROR] {tool_name} download failed with exception: {e}")
                    return False
        
        # Phase 2: Extract archives sequentially to avoid file conflicts
        for tool_name, archive_path in archive_paths.items():
            if tool_name in download_results and download_results[tool_name]:
                tool_spec = self.tool_specs[tool_name]
                extract_path = tool_spec.get_full_extract_path(self.ps_env_path)
                
                success = self._extract_and_verify_tool(tool_name, archive_path, extract_path)
                if success:
                    logger.info(f"[OK] {tool_name} extraction and verification completed")
                else:
                    logger.error(f"[ERROR] {tool_name} extraction or verification failed")
                    return False
        
        logger.info("[OK] All tools installed successfully")
        return True
    
    def _download_tool(self, tool_name: str, url: str, archive_path: Path) -> bool:
        """Download a single tool (thread-safe)"""
        logger.info(f"Downloading {tool_name}...")
        return self.download_file(url, archive_path, f"{tool_name}")
    
    def _extract_and_verify_tool(self, tool_name: str, archive_path: Path, extract_path: Path) -> bool:
        """Extract and verify a single tool (thread-safe)"""
        logger.info(f"Extracting {tool_name}...")
        
        # Extract archive
        if not self.extract_7z_archive(archive_path, extract_path):
            return False
        
        # Handle nested folder structure
        self._fix_nested_extraction(extract_path, tool_name)
        
        # Clean up archive
        try:
            archive_path.unlink()
            logger.info(f"Cleaned up {tool_name}.7z")
        except Exception as e:
            logger.warning(f"Failed to clean up {tool_name}.7z: {e}")
        
        # Verify installation
        tool_spec = self.tool_specs[tool_name]
        executable_path = tool_spec.get_full_executable_path(self.ps_env_path)
        
        if executable_path.exists():
            return True
        else:
            logger.error(f"[ERROR] {tool_name} installation failed - executable not found at {executable_path}")
            return False

    def setup_portable_environment(self) -> bool:
        """Setup the portable environment by downloading and extracting all tools"""
        logger.info("Setting up portable environment...")
        
        # Create ps_env directory
        self.ps_env_path.mkdir(parents=True, exist_ok=True)
        
        # Ensure install path is set in config
        if not self.config_manager.config or not self.config_manager.config.install_path:
            self.config_manager.configure_install_path(str(self.install_path))
        
        # Step 1: Configure GPU first to determine if we need CUDA
        try:
            logger.info("Configuring GPU...")
            gpu_config = self.config_manager.configure_gpu_from_detection()
            logger.info(f"GPU configured: {gpu_config.name} ({gpu_config.recommended_backend})")
        except Exception as e:
            logger.error(f"Failed to configure GPU: {e}")
            gpu_config = None
        
        # Step 2: Download CUDA first if we have NVIDIA GPU
        cuda_download_future = None
        cuda_executor = None
        if gpu_config:
            logger.info(f"GPU config: backend={gpu_config.recommended_backend}, cuda_version={gpu_config.cuda_version}")
            
            if (gpu_config.recommended_backend and "cuda" in gpu_config.recommended_backend and 
                gpu_config.cuda_version):
                
                # Check if CUDA is already installed
                cuda_extract_path = self.ps_env_path / "CUDA"
                if cuda_extract_path.exists() and (cuda_extract_path / "bin").exists():
                    logger.info(f"CUDA {gpu_config.cuda_version.value} already installed at {cuda_extract_path}")
                    # Configure CUDA paths in config
                    self.config_manager.configure_cuda_paths()
                else:
                    logger.info(f"Starting CUDA {gpu_config.cuda_version.value} download...")
                    cuda_executor = ThreadPoolExecutor(max_workers=1)
                    cuda_download_future = cuda_executor.submit(self._download_cuda_only, gpu_config.cuda_version)
            else:
                logger.info("CUDA not needed for this GPU configuration")
        else:
            logger.info("No GPU configuration available, skipping CUDA")
        
        # Step 3: Install basic tools in parallel while CUDA downloads
        tools_to_install = ["ffmpeg", "git", "python"]
        
        if not self._install_tools_parallel(tools_to_install):
            logger.error("[ERROR] Failed to install basic tools")
            return False
        
        # Step 4: Wait for CUDA download and extract it
        if cuda_download_future and gpu_config and gpu_config.cuda_version:
            try:
                logger.info("Waiting for CUDA download to complete...")
                cuda_archive_path = cuda_download_future.result()
                if cuda_archive_path:
                    logger.info(f"Extracting CUDA {gpu_config.cuda_version.value}...")
                    if not self._extract_cuda(cuda_archive_path, gpu_config.cuda_version):
                        logger.error("[ERROR] Failed to extract CUDA")
                        return False
                    logger.info(f"[OK] CUDA {gpu_config.cuda_version.value} installed successfully")
                else:
                    logger.error("[ERROR] CUDA download failed")
                    return False
            except Exception as e:
                logger.error(f"[ERROR] CUDA installation failed: {e}")
                return False
            finally:
                if cuda_executor:
                    cuda_executor.shutdown(wait=True)
        elif gpu_config and gpu_config.recommended_backend and "cuda" in gpu_config.recommended_backend:
            logger.warning("CUDA was expected but download future is None")
        
        logger.info("[OK] Portable environment setup completed successfully")
        return True



    def run_in_activated_environment(self, command: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Run a command in the portable environment with proper PATH setup"""
        if not self.ps_env_path.exists():
            logger.error("Base environment ps_env not found. Run --setup-env first.")
            return subprocess.CompletedProcess([], 1, "", "Base environment not found")

        # Use the centralized environment setup function
        env = self.setup_environment_for_subprocess()
        
        # Debug: Log PATH for nvcc command
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
        
        # On Windows, use shell=True for better executable resolution
        import platform
        use_shell = platform.system() == "Windows"
        
        if use_shell:
            # Convert command list to string for shell execution
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
        # Start with a copy of the current environment
        # Use dict() to ensure we get all environment variables
        env_vars = dict(os.environ)
        
        if not self.ps_env_path.exists():
            return env_vars
        
        # Add tool paths to PATH
        tool_paths = []
        for tool_name, tool_spec in self.tool_specs.items():
            tool_dir = self.ps_env_path / tool_spec.extract_path
            if tool_dir.exists():
                # Add the directory containing the executable to PATH
                executable_path = self.ps_env_path / tool_spec.executable_path
                executable_dir = executable_path.parent
                if executable_dir.exists():
                    tool_paths.append(str(executable_dir))
                else:
                    # Fallback to extract directory
                    tool_paths.append(str(tool_dir))
        
        # Add CUDA paths if available
        if (self.config_manager.config and 
            self.config_manager.config.gpu_config and 
            self.config_manager.config.gpu_config.cuda_paths):
            cuda_paths = self.config_manager.config.gpu_config.cuda_paths
            cuda_base = Path(cuda_paths.base_path)
            
            # Check if CUDA is actually installed
            if cuda_base.exists():
                cuda_bin = Path(cuda_paths.cuda_bin)
                if cuda_bin.exists():
                    tool_paths.append(str(cuda_bin))
                    # Also add CUDA lib paths for runtime libraries
                    cuda_lib = Path(cuda_paths.cuda_lib)
                    if cuda_lib.exists():
                        tool_paths.append(str(cuda_lib))
                    cuda_lib_64 = Path(cuda_paths.cuda_lib_64)
                    if cuda_lib_64.exists():
                        tool_paths.append(str(cuda_lib_64))
                    
                    # Set CUDA environment variables
                    env_vars['CUDA_PATH'] = str(cuda_base)
                    env_vars['CUDA_HOME'] = str(cuda_base)
                    env_vars['CUDA_ROOT'] = str(cuda_base)
                    env_vars['CUDA_BIN_PATH'] = str(cuda_bin)
                    env_vars['CUDA_LIB_PATH'] = str(cuda_lib_64) if cuda_lib_64.exists() else str(cuda_lib)
                else:
                    logger.debug(f"CUDA bin directory not found: {cuda_bin}")
            else:
                logger.debug(f"CUDA base directory not found: {cuda_base}. CUDA may not be installed.")
                # Try to trigger CUDA installation if GPU supports it
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
        
        # Check if essential tools are available
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
                # Extract version info from output
                version_output = self._extract_version_from_output(tool_name, result.stdout)
                
                # Consider tool working if we got meaningful version output, regardless of exit code
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
        
        # Check if CUDA should be available but isn't installed
        self._check_and_suggest_cuda_installation()
        
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
                version_output = self._extract_version_from_output(tool_name, result.stdout)
                
                # Consider tool working if we got meaningful version output, regardless of exit code
                if version_output and version_output != "Unknown version":
                    status["tools_status"][tool_name] = {
                        "working": True,
                        "version": version_output
                    }
                else:
                    # Special handling for nvcc errors
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
        # Check if environment actually exists and is valid (has Python executable)
        python_path = self.get_ps_env_python()
        base_env_exists = self.ps_env_path.exists() and python_path and python_path.exists()
        
        # Check installed tools
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
        """
        Setup the complete portable environment.
        
        Returns:
            True if setup was successful, False otherwise
        """
        logger.info("Setting up PortableSource environment...")
        
        # Step 1: Setup portable environment (download and extract tools)
        if not self.setup_portable_environment():
            logger.error("[ERROR] Failed to setup portable environment")
            return False
        
        # Step 2: Verify that all tools work
        try:
            if not self._verify_environment_tools():
                logger.error("[ERROR] Environment verification failed - some tools are not working properly")
                return False
        except Exception as e:
            logger.warning(f"Environment verification failed: {e}")
        
        # Step 3: Update setup status
        try:
            self.config_manager.mark_environment_setup_completed(True)
            if self.config_manager.config:
                self.config_manager.config.install_path = str(self.install_path)
            self.config_manager.save_config()
            logger.info("[OK] Environment setup status saved to configuration")
        except Exception as e:
            logger.warning(f"Failed to save setup status to config: {e}")
        
        logger.info("[OK] Environment setup completed successfully")
        logger.info(f"Portable environment created at: {self.ps_env_path}")
        
        return True

#!/usr/bin/env python3
"""
Universal Repository Installer for PortableSource

This module provides intelligent installation of any repository with automatic
dependency analysis and GPU-specific package handling.
"""

import os
import sys
import re
import subprocess
import logging
import shutil
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Union
from urllib.parse import urlparse
from dataclasses import dataclass, field
from enum import Enum

from tqdm import tqdm

from portablesource.config import ConfigManager, SERVER_DOMAIN


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ServerAPIClient:
    """Client for PortableSource server API"""

    def __init__(self, server_url: str = f"http://{SERVER_DOMAIN}"):
        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.timeout = 10
    
    def get_repository_info(self, name: str) -> Optional[Dict]:
        """Get repository information from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.debug(f"Repository '{name}' not found in server database")
                return None
            else:
                logger.warning(f"Server returned status {response.status_code} for repository '{name}'")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not connect to server: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting repository info from server: {e}")
            return None
    
    def search_repositories(self, query: str) -> List[Dict]:
        """Search repositories in server database"""
        try:
            url = f"{self.server_url}/api/search"
            response = self.session.get(url, params={'q': query}, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                return data.get('repositories', [])
            else:
                logger.warning(f"Server search returned status {response.status_code}")
                return []
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not connect to server for search: {e}")
            return []
        except Exception as e:
            logger.error(f"Error searching repositories: {e}")
            return []
    
    def get_repository_dependencies(self, name: str) -> Optional[Dict]:
        """Get repository dependencies from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}/dependencies"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.debug(f"No dependencies found for repository '{name}'")
                return None
            else:
                logger.warning(f"Server returned status {response.status_code} for dependencies of '{name}'")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not connect to server for dependencies: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting dependencies from server: {e}")
            return None
    
    def get_installation_plan(self, name: str) -> Optional[Dict]:
        """Get installation plan from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}/install-plan"
            logger.info(f"🌐 Requesting installation plan from: {url}")
            response = self.session.get(url, timeout=self.timeout)
            
            logger.info(f"📡 Server response status: {response.status_code}")
            
            if response.status_code == 200:
                plan = response.json()
                logger.info(f"✅ Successfully received installation plan for '{name}'")
                return plan
            elif response.status_code == 404:
                logger.warning(f"❌ No installation plan found for repository '{name}' (404)")
                return None
            else:
                logger.warning(f"❌ Server returned status {response.status_code} for installation plan of '{name}'")
                if hasattr(response, 'text'):
                    logger.warning(f"Response content: {response.text[:200]}")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Could not connect to server for installation plan: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Error getting installation plan from server: {e}")
            return None
    
    def is_server_available(self) -> bool:
        """Check if server is available"""
        try:
            url = f"{self.server_url}/api/repositories"
            response = self.session.get(url, timeout=self.timeout)
            return response.status_code == 200
        except Exception:
            return False


class PackageType(Enum):
    """Types of special packages that need custom handling"""
    TORCH = "torch"
    ONNXRUNTIME = "onnxruntime"
    TENSORFLOW = "tensorflow"
    REGULAR = "regular"


@dataclass
class PackageInfo:
    """Information about a package"""
    name: str
    version: Optional[str] = None
    extras: Optional[List[str]] = None
    package_type: PackageType = PackageType.REGULAR
    original_line: str = ""
    
    def __str__(self):
        result = self.name
        if self.extras:
            result += f"[{','.join(self.extras)}]"
        if self.version:
            result += f"=={self.version}"
        return result


@dataclass
class InstallationPlan:
    """Plan for installing packages"""
    torch_packages: List[PackageInfo] = field(default_factory=list)
    onnx_packages: List[PackageInfo] = field(default_factory=list)
    tensorflow_packages: List[PackageInfo] = field(default_factory=list)
    regular_packages: List[PackageInfo] = field(default_factory=list)
    torch_index_url: Optional[str] = None
    onnx_package_name: Optional[str] = None


class RequirementsAnalyzer:
    """Analyzes requirements.txt files and categorizes packages"""
    
    def __init__(self):
        self.torch_packages = {"torch", "torchvision", "torchaudio", "torchtext", "torchdata"}
        self.onnx_packages = {"onnxruntime", "onnxruntime-gpu", "onnxruntime-directml", "onnxruntime-openvino"}
        self.tensorflow_packages = {"tensorflow", "tensorflow-gpu", "tf-nightly", "tf-nightly-gpu"}
    
    def parse_requirement_line(self, line: str) -> Optional[PackageInfo]:
        """
        Parse a single requirement line
        
        Args:
            line: Requirement line from requirements.txt
            
        Returns:
            PackageInfo object or None if invalid
        """
        # Remove comments and whitespace
        line = line.split('#')[0].strip()
        if not line or line.startswith('-'):
            return None
        
        # Handle different requirement formats
        # Examples: torch==1.12.0, torch>=1.11.0, torch[cuda], torch==1.12.0+cu117
        
        # Extract package name and extras
        match = re.match(r'^([a-zA-Z0-9_-]+)(?:\[([^\]]+)\])?(.*)$', line)
        if not match:
            return None
        
        package_name = match.group(1).lower()
        extras = match.group(2).split(',') if match.group(2) else None
        version_part = match.group(3)
        
        # Extract version
        version = None
        if version_part:
            version_match = re.search(r'[=<>!]+([^\s,;]+)', version_part)
            if version_match:
                version = version_match.group(1)
        
        # Determine package type
        package_type = PackageType.REGULAR
        if package_name in self.torch_packages:
            package_type = PackageType.TORCH
        elif package_name in self.onnx_packages:
            package_type = PackageType.ONNXRUNTIME
        elif package_name in self.tensorflow_packages:
            package_type = PackageType.TENSORFLOW
        
        return PackageInfo(
            name=package_name,
            version=version,
            extras=extras,
            package_type=package_type,
            original_line=line
        )
    
    def analyze_requirements(self, requirements_path: Path) -> List[PackageInfo]:
        """
        Analyze requirements.txt file
        
        Args:
            requirements_path: Path to requirements.txt
            
        Returns:
            List of PackageInfo objects
        """
        packages = []
        
        try:
            with open(requirements_path, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        package_info = self.parse_requirement_line(line)
                        if package_info:
                            packages.append(package_info)
                    except Exception as e:
                        logger.warning(f"Error parsing line {line_num} in {requirements_path}: {e}")
                        continue
        
        except Exception as e:
            logger.error(f"Error reading requirements file {requirements_path}: {e}")
            return []
        
        logger.info(f"Analyzed {len(packages)} packages from {requirements_path}")
        return packages
    
    def create_installation_plan(self, packages: List[PackageInfo], gpu_config) -> InstallationPlan:
        """
        Create installation plan based on GPU configuration
        
        Args:
            packages: List of parsed packages
            gpu_config: GPU configuration
            
        Returns:
            InstallationPlan object
        """
        from portablesource.get_gpu import GPUDetector
        
        plan = InstallationPlan()
        
        # Get GPU information
        gpu_detector = GPUDetector()
        gpu_info_list = gpu_detector.get_gpu_info()
        primary_gpu_type = gpu_detector.get_primary_gpu_type()
        
        # Categorize packages
        for package in packages:
            if package.package_type == PackageType.TORCH:
                plan.torch_packages.append(package)
            elif package.package_type == PackageType.ONNXRUNTIME:
                plan.onnx_packages.append(package)
            elif package.package_type == PackageType.TENSORFLOW:
                plan.tensorflow_packages.append(package)
            else:
                plan.regular_packages.append(package)
        
        # Determine PyTorch index URL
        if plan.torch_packages:
            plan.torch_index_url = self._get_torch_index_url(gpu_info_list, primary_gpu_type)
        
        # Auto-detect and set the correct ONNX Runtime package name
        if plan.onnx_packages:
            plan.onnx_package_name = self._get_onnx_package_name(primary_gpu_type)
        
        return plan
    
    def _get_torch_index_url(self, gpu_info_list, primary_gpu_type) -> str:
        """Get PyTorch index URL based on GPU information"""
        from portablesource.get_gpu import GPUType, CUDAVersion
        
        if not primary_gpu_type or primary_gpu_type != GPUType.NVIDIA:
            return "https://download.pytorch.org/whl/cpu"
        
        # Find NVIDIA GPU and get CUDA version
        cuda_version = None
        for gpu_info in gpu_info_list:
            if gpu_info.gpu_type == GPUType.NVIDIA and gpu_info.cuda_version:
                cuda_version = gpu_info.cuda_version
                break
        
        # Determine CUDA version for PyTorch
        if cuda_version == CUDAVersion.CUDA_128:
            return "https://download.pytorch.org/whl/cu128"
        elif cuda_version == CUDAVersion.CUDA_124:
            return "https://download.pytorch.org/whl/cu124"
        elif cuda_version == CUDAVersion.CUDA_118:
            return "https://download.pytorch.org/whl/cu118"
        else:
            return "https://download.pytorch.org/whl/cpu"  # Fallback to CPU
    
    def _get_onnx_package_name(self, gpu_type) -> str:
        """Get ONNX Runtime package name based on GPU type"""
        from portablesource.get_gpu import GPUType
        import os

        if not gpu_type or gpu_type == GPUType.UNKNOWN:
            return "onnxruntime"

        if gpu_type == GPUType.NVIDIA:
            return "onnxruntime-gpu"
        elif gpu_type in [GPUType.AMD, GPUType.INTEL] and os.name == 'nt':
            return "onnxruntime-directml"
        else:
            # For AMD/Intel on other OS or other cases, default to standard onnxruntime
            # Specific logic for ROCm on Linux can be handled during installation command construction
            return "onnxruntime"
    
    def _get_onnx_package_for_provider(self, provider: str) -> tuple[str, list[str], dict[str, str]]:
        """
        Get ONNX Runtime package name, installation flags and environment variables for specific provider
        
        Args:
            provider: Execution provider ('tensorrt', 'cuda', 'directml', 'cpu', or '')
            
        Returns:
            Tuple of (package_name, install_flags, environment_vars)
        """
        if provider == 'tensorrt':
            # TensorRT requires specific version and proper environment setup
            return (
                "onnxruntime-gpu", 
                [],
                {
                    "ORT_CUDA_UNAVAILABLE": "0",
                    "ORT_TENSORRT_UNAVAILABLE": "0"
                }
            )
        elif provider == 'cuda':
            return (
                "onnxruntime-gpu", 
                [],
                {"ORT_CUDA_UNAVAILABLE": "0"}
            )
        elif provider == 'directml':
            return (
                "onnxruntime-directml", 
                [],
                {"ORT_DIRECTML_UNAVAILABLE": "0"}
            )
        elif provider == 'cpu':
            return (
                "onnxruntime", 
                [],
                {}
            )
        else:
            # Auto-detect based on system
            from portablesource.get_gpu import GPUDetector
            gpu_detector = GPUDetector()
            gpu_info_list = gpu_detector.get_gpu_info()
            primary_gpu_type = gpu_detector.get_primary_gpu_type()
            package_name = self._get_onnx_package_name(primary_gpu_type)
            env_vars = {}
            
            if package_name == "onnxruntime-gpu":
                env_vars["ORT_CUDA_UNAVAILABLE"] = "0"
            elif package_name == "onnxruntime-directml":
                env_vars["ORT_DIRECTML_UNAVAILABLE"] = "0"
                
            return package_name, [], env_vars


class MainFileFinder:
    """Finds main executable files in repositories using server API and fallbacks"""
    
    def __init__(self, server_client: ServerAPIClient):
        self.server_client = server_client
        self.common_main_files = [
            "run.py",
            "app.py", 
            "webui.py",
            "main.py",
            "start.py",
            "launch.py",
            "gui.py",
            "interface.py",
            "server.py"
        ]
    
    def find_main_file(self, repo_name: str, repo_path: Path, repo_url: str) -> Optional[str]:
        """
        Find main file using multiple strategies:
        1. Server API lookup
        2. Common file pattern fallbacks
        3. Return None if not found (user needs to specify manually)
        """
        
        # Strategy 1: Try server API first
        logger.info(f"Checking server database for repository: {repo_name}")
        server_info = self.server_client.get_repository_info(repo_name)
        
        if server_info:
            main_file = server_info.get('main_file')
            if main_file and self._validate_main_file(repo_path, main_file):
                logger.info(f"Found main file from server: {main_file}")
                return main_file
            else:
                logger.warning(f"Server returned main file '{main_file}' but it doesn't exist in repository")
        
        # Strategy 2: Try URL-based lookup (extract repo name from URL)
        if not server_info:
            url_repo_name = self._extract_repo_name_from_url(repo_url)
            if url_repo_name != repo_name:
                logger.info(f"Trying URL-based lookup: {url_repo_name}")
                server_info = self.server_client.get_repository_info(url_repo_name)
                if server_info:
                    main_file = server_info.get('main_file')
                    if main_file and self._validate_main_file(repo_path, main_file):
                        logger.info(f"Found main file from URL-based lookup: {main_file}")
                        return main_file
        
        # Strategy 3: Search server database for similar repositories
        logger.info(f"Searching server database for similar repositories...")
        search_results = self.server_client.search_repositories(repo_name)
        for result in search_results:
            main_file = result.get('main_file')
            if main_file and self._validate_main_file(repo_path, main_file):
                logger.info(f"Found main file from similar repository: {main_file}")
                return main_file
        
        # Strategy 4: Common file fallbacks
        logger.info("Trying common main file patterns...")
        for main_file in self.common_main_files:
            if self._validate_main_file(repo_path, main_file):
                logger.info(f"Found main file using fallback: {main_file}")
                return main_file
        
        # Strategy 5: Look for Python files in root directory
        logger.info("Searching for Python files in root directory...")
        python_files = list(repo_path.glob("*.py"))
        
        # Filter out common non-main files
        excluded_patterns = ['test_', 'setup.py', 'config.py', '__', 'install']
        main_candidates = []
        
        for py_file in python_files:
            filename = py_file.name.lower()
            if not any(pattern in filename for pattern in excluded_patterns):
                main_candidates.append(py_file.name)
        
        if len(main_candidates) == 1:
            logger.info(f"Found single Python file candidate: {main_candidates[0]}")
            return main_candidates[0]
        elif len(main_candidates) > 1:
            # Try to find the most likely main file
            for candidate in main_candidates:
                if any(pattern in candidate.lower() for pattern in ['main', 'run', 'start', 'app']):
                    logger.info(f"Found likely main file: {candidate}")
                    return candidate
        
        # All strategies failed
        logger.warning(f"Could not determine main file for repository: {repo_name}")
        return None
    
    def _validate_main_file(self, repo_path: Path, main_file: str) -> bool:
        """Check if main file exists in repository"""
        return (repo_path / main_file).exists()
    
    def _extract_repo_name_from_url(self, repo_url: str) -> str:
        """Extract repository name from URL"""
        try:
            parsed = urlparse(repo_url)
            path = parsed.path.strip('/')
            if path.endswith('.git'):
                path = path[:-4]
            return path.split('/')[-1].lower()
        except Exception:
            return ""


class RepositoryInstaller:
    """Universal repository installer with intelligent dependency handling"""
    
    def __init__(self, config_manager: Optional[ConfigManager] = None, server_url: str = f"http://{SERVER_DOMAIN}"):
        self.config_manager = config_manager or ConfigManager()
        self.analyzer = RequirementsAnalyzer()
        
        # Initialize server client and main file finder
        self.server_client = ServerAPIClient(server_url)
        self.main_file_finder = MainFileFinder(self.server_client)
        

        if self.server_client.is_server_available():
            logger.info("✅ Connected to PortableSource server")
        else:
            logger.warning("⚠️  PortableSource server not available - using fallback methods only")
        
        # Fallback repositories (will be used if server is not available)
        self.fallback_repositories = {
            "facefusion": {
                "url": "https://github.com/facefusion/facefusion",
                "branch": "master",
                "main_file": "run.py",
                "program_args": "run",
                "special_setup": self._setup_facefusion
            },
            "comfyui": {
                "url": "https://github.com/comfyanonymous/ComfyUI",
                "main_file": "main.py",
                "special_setup": None
            },
            "stable-diffusion-webui-forge": {
                "url": "https://github.com/lllyasviel/stable-diffusion-webui-forge",
                "main_file": "webui.py",
                "special_setup": None
            },
            "liveportrait": {
                "url": "https://github.com/KwaiVGI/LivePortrait",
                "main_file": "app.py",
                "special_setup": None
            },
            "deep-live-cam": {
                "url": "https://github.com/hacksider/Deep-Live-Cam",
                "main_file": "run.py",
                "special_setup": None
            }
        }
    
    def install_repository(self, repo_url_or_name: str, install_path: Optional[Union[str, Path]] = None) -> bool:
        """
        Install repository with intelligent dependency handling
        
        Args:
            repo_url_or_name: Repository URL or known name
            install_path: Installation path (optional)
            
        Returns:
            True if installation successful
        """
        try:
            # Determine repository info
            repo_info = self._get_repository_info(repo_url_or_name)
            if not repo_info:
                logger.error(f"Could not determine repository info for: {repo_url_or_name}")
                return False
            
            # Set up installation paths  
            if not install_path:
                logger.error("install_path is required in the new architecture")
                return False
            
            if isinstance(install_path, str):
                install_path = Path(install_path)
            elif not isinstance(install_path, Path):
                logger.error("install_path must be a string or Path object")
                return False
            
            # Используем новую структуру: install_path является корнем, repos - подпапка
            repo_name = self._extract_repo_name(repo_info["url"])
            repo_path = install_path / repo_name
            
            # Clone or update repository
            if not self._clone_or_update_repository(repo_info, repo_path):
                return False
            
            # Analyze and install dependencies (using base Python)
            if not self._install_dependencies(repo_path):
                return False
            
            # Run special setup if needed
            if repo_info.get("special_setup"):
                repo_info["special_setup"](repo_path)
            
            # Generate startup script
            self._generate_startup_script(repo_path, repo_info)
            
            # Send download statistics to server
            self._send_download_stats(repo_name)

            logger.info(f"Successfully installed repository: {repo_name}")
            return True
            
        except Exception as e:
            logger.error(f"Error installing repository {repo_url_or_name}: {e}")
            return False
    
    def _get_repository_info(self, repo_url_or_name: str) -> Optional[Dict]:
        """Get repository information from server API or fallback methods"""
        
        # Determine if input is a URL or repository name
        if repo_url_or_name.startswith(("http://", "https://", "git@")):
            # It's a URL
            repo_url = repo_url_or_name
            repo_name = self._extract_repo_name(repo_url)
        elif "/" in repo_url_or_name and not repo_url_or_name.startswith("http"):
            # It's a GitHub user/repo format
            repo_url = f"https://github.com/{repo_url_or_name}"
            repo_name = repo_url_or_name.split('/')[-1].lower()
        else:
            # It's a repository name
            repo_name = repo_url_or_name.lower()
            repo_url = None
        
        # Try server API first
        server_info = self.server_client.get_repository_info(repo_name)
        if server_info:
            return {
                "url": server_info.get("url", repo_url),
                "main_file": server_info.get("main_file", "main.py"),
                "program_args": server_info.get("program_args", ""),
                "special_setup": self._get_special_setup(repo_name)
            }
        
        # Try fallback repositories
        if repo_name in self.fallback_repositories:
            return self.fallback_repositories[repo_name]
        
        # If we have a URL but no server info, create basic info
        if repo_url:
            return {
                "url": repo_url,
                "main_file": None,  # Will be determined later
                "special_setup": self._get_special_setup(repo_name)
            }
        
        return None
    
    def _get_special_setup(self, repo_name: str):
        """Get special setup function for known repositories"""
        special_setups = {
            "facefusion": self._setup_facefusion,
            # Add more special setups as needed
        }
        return special_setups.get(repo_name.lower())
    
    def _extract_repo_name(self, repo_url: str) -> str:
        """Extract repository name from URL"""
        parsed = urlparse(repo_url)
        path = parsed.path.strip('/')
        if path.endswith('.git'):
            path = path[:-4]
        return path.split('/')[-1]
    
    def _clone_or_update_repository(self, repo_info: Dict, repo_path: Path) -> bool:
        """Clone or update repository with automatic error fixing"""
        try:
            git_exe = self._get_git_executable()
            
            if repo_path.exists():
                # Update existing repository
                logger.info(f"Updating repository at {repo_path}")
                os.chdir(repo_path)
                
                # Check if it's a git repository
                if (repo_path / ".git").exists():
                    # Try to update with automatic error fixing
                    if not self._update_repository_with_fixes(git_exe, repo_path):
                        return False
                else:
                    logger.warning(f"Directory exists but is not a git repository: {repo_path}")
                    return False
            else:
                # Clone new repository
                logger.info(f"Cloning repository to {repo_path}")
                os.chdir(repo_path.parent)
                
                cmd = [git_exe, "clone", repo_info["url"]]
                if repo_info.get("branch"):
                    cmd.extend(["-b", repo_info["branch"]])
                cmd.append(repo_path.name)
                
                self._run_git_with_progress(cmd, f"Cloning {repo_info['url']}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error cloning/updating repository: {e}")
            return False
    
    def _update_repository_with_fixes(self, git_exe: str, repo_path: Path) -> bool:
        """Update repository with automatic error fixing"""
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                self._run_git_with_progress([git_exe, "pull"], f"Updating repository at {repo_path}")
                return True
                
            except subprocess.CalledProcessError as e:
                error_output = str(e.output) if hasattr(e, 'output') else str(e)
                logger.warning(f"Git pull failed (attempt {attempt + 1}/{max_attempts}): {error_output}")
                
                # Try to fix common git issues
                if attempt < max_attempts - 1:  # Don't try fixes on last attempt
                    if self._fix_git_issues(git_exe, repo_path, error_output):
                        logger.info("Applied git fix, retrying...")
                        continue
                
                if attempt == max_attempts - 1:
                    logger.error(f"❌ Failed to update repository after {max_attempts} attempts")
                    return False
        
        return False
    
    def _fix_git_issues(self, git_exe: str, repo_path: Path, error_output: str) -> bool:
         """Try to fix common git issues automatically"""
         try:
             # Fix 1: Diverged branches - reset to remote
             if "diverged" in error_output.lower() or "non-fast-forward" in error_output.lower():
                 logger.info("🔧 Fixing diverged branches by resetting to remote...")
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 2: Uncommitted changes - stash them
             if "uncommitted changes" in error_output.lower() or "would be overwritten" in error_output.lower():
                 logger.info("🔧 Stashing uncommitted changes...")
                 subprocess.run([git_exe, "stash"], check=True, capture_output=True)
                 return True
             
             # Fix 3: Merge conflicts - abort and reset
             if "merge conflict" in error_output.lower() or "conflict" in error_output.lower():
                 logger.info("🔧 Resolving merge conflicts by resetting...")
                 subprocess.run([git_exe, "merge", "--abort"], capture_output=True)  # Don't check=True as it might fail
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 4: Detached HEAD - checkout main/master
             if "detached head" in error_output.lower():
                 logger.info("🔧 Fixing detached HEAD by checking out main branch...")
                 try:
                     subprocess.run([git_exe, "checkout", "main"], check=True, capture_output=True)
                 except subprocess.CalledProcessError:
                     subprocess.run([git_exe, "checkout", "master"], check=True, capture_output=True)
                 return True
             
             # Fix 5: Corrupted index - reset index
             if "index" in error_output.lower() and "corrupt" in error_output.lower():
                 logger.info("🔧 Fixing corrupted index...")
                 subprocess.run([git_exe, "reset", "--mixed"], check=True, capture_output=True)
                 return True
             
             # Fix 6: Remote tracking branch issues
             if "no tracking information" in error_output.lower():
                 logger.info("🔧 Setting up remote tracking branch...")
                 subprocess.run([git_exe, "branch", "--set-upstream-to=origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 7: Exit status 128 - generic git error, try comprehensive fix
             if "128" in error_output or "fatal:" in error_output.lower():
                 logger.info("🔧 Fixing git error 128 with comprehensive reset...")
                 # First try to fetch and reset
                 try:
                     subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                     subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                     return True
                 except subprocess.CalledProcessError:
                     # If main doesn't exist, try master
                     try:
                         subprocess.run([git_exe, "reset", "--hard", "origin/master"], check=True, capture_output=True)
                         return True
                     except subprocess.CalledProcessError:
                         # Last resort: clean and reset
                         subprocess.run([git_exe, "clean", "-fd"], capture_output=True)
                         subprocess.run([git_exe, "reset", "--hard", "HEAD"], capture_output=True)
                         return True
             
             # Fix 8: Permission denied or file lock issues
             if "permission denied" in error_output.lower() or "unable to create" in error_output.lower():
                 logger.info("🔧 Fixing permission/lock issues...")
                 import time
                 time.sleep(2)  # Wait a bit for locks to release
                 subprocess.run([git_exe, "gc", "--prune=now"], capture_output=True)  # Clean up
                 return True
             
             # Fix 9: Network/remote issues - retry with different approach
             if "network" in error_output.lower() or "remote" in error_output.lower() or "connection" in error_output.lower():
                 logger.info("🔧 Fixing network issues by refreshing remote...")
                 subprocess.run([git_exe, "remote", "set-url", "origin", subprocess.run([git_exe, "remote", "get-url", "origin"], capture_output=True, text=True).stdout.strip()], capture_output=True)
                 return True
                 
         except subprocess.CalledProcessError as fix_error:
             logger.warning(f"Fix attempt failed: {fix_error}")
             return False
         except Exception as e:
             logger.warning(f"Error during git fix: {e}")
             return False
         
         return False
    
    def _get_git_executable(self) -> str:
        """Get git executable path from conda environment"""
        if self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            conda_env_path = install_path / "miniconda" / "envs" / "portablesource"
            git_path = conda_env_path / "Scripts" / "git.exe"
            if git_path.exists():
                return str(git_path)
        
        # Fallback to system git
        return "git"
    

    
    def _get_python_executable(self) -> str:
        """Get Python executable path from conda environment"""
        if self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            conda_env_path = install_path / "miniconda" / "envs" / "portablesource"
            python_path = conda_env_path / "python.exe"
            if python_path.exists():
                return str(python_path)
        
        # Fallback to system python
        return "python"
    
    def _get_pip_executable(self, repo_name: str) -> str:
        """Get pip executable path from repository's venv"""
        if self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            venv_path = install_path / "envs" / repo_name
            pip_path = venv_path / "Scripts" / "pip.exe"
            if pip_path.exists():
                return str(pip_path)
        
        # Fallback to system pip
        return "pip"
    
    def _get_uv_executable(self, repo_name: str) -> List[str]:
        """Get uv executable command from repository's venv"""
        if self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            venv_path = install_path / "envs" / repo_name
            python_path = venv_path / "Scripts" / "python.exe"
            if python_path.exists():
                return [str(python_path), "-m", "uv"]
        
        # Fallback to system python with uv
        return ["python", "-m", "uv"]
    
    def _install_uv_in_venv(self, repo_name: str) -> bool:
        """Install uv in the venv environment"""
        try:
            # First check if uv is already available
            uv_cmd = self._get_uv_executable(repo_name)
            try:
                result = subprocess.run(uv_cmd + ["--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"UV already available in venv for {repo_name}: {result.stdout.strip()}")
                    return True
            except Exception:
                pass  # UV not available, continue with installation
            
            pip_exe = self._get_pip_executable(repo_name)
            logger.info(f"Installing uv in venv for {repo_name}...")
            self._run_pip_with_progress([pip_exe, "install", "uv"], "Installing uv")
            
            # Verify installation
            try:
                result = subprocess.run(uv_cmd + ["--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    logger.info(f"UV successfully installed: {result.stdout.strip()}")
                    return True
                else:
                    logger.error(f"UV installation verification failed: {result.stderr}")
                    return False
            except Exception as e:
                logger.error(f"UV installation verification failed: {e}")
                return False
                
        except Exception as e:
            logger.error(f"Error installing uv: {e}")
            return False
    
    def _get_installation_plan_from_server(self, repo_name: str) -> Optional[Dict]:
        """Get installation plan from server for the repository"""
        try:
            logger.info(f"🔍 Checking server for installation plan for {repo_name}")
            
            if not self.server_client.is_server_available():
                logger.warning(f"❌ Server not available for {repo_name}")
                return None
            
            logger.info(f"🌐 Server is available, requesting installation plan for {repo_name}")
            plan = self.server_client.get_installation_plan(repo_name)
            if plan:
                logger.info(f"✅ Retrieved installation plan from server for {repo_name}")
                logger.info(f"📋 Plan contains {len(plan.get('installation_order', []))} installation steps")
                return plan
            else:
                logger.warning(f"❌ No installation plan available on server for {repo_name}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Failed to get installation plan from server for {repo_name}: {e}")
            return None
    
    def _install_dependencies(self, repo_path: Path) -> bool:
        """Install dependencies in venv with new architecture - try server first, then local requirements"""
        try:
            repo_name = repo_path.name.lower()
            logger.info(f"📦 Installing dependencies for {repo_name}")
            
            # Create venv environment for the repository
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            # Try to get installation plan from server first
            server_plan = self._get_installation_plan_from_server(repo_name)
            if server_plan:
                logger.info(f"🌐 Using server installation plan for {repo_name}")
                if self._execute_server_installation_plan(server_plan, repo_path, repo_name):
                    logger.info(f"✅ Successfully installed dependencies from server for {repo_name}")
                    return True
                else:
                    logger.warning(f"⚠️ Server installation failed for {repo_name}, falling back to local requirements")
            else:
                logger.info(f"📄 No server installation plan available for {repo_name}, using local requirements")
            
            # Fallback to local requirements.txt
            requirements_files = [
                repo_path / "requirements.txt",
                repo_path / "requirements" / "requirements.txt",
                repo_path / "install" / "requirements.txt"
            ]
            
            requirements_path = None
            for req_file in requirements_files:
                if req_file.exists():
                    requirements_path = req_file
                    break
            
            if not requirements_path:
                logger.warning(f"No requirements.txt found in {repo_path}")
                return True  # Not an error, some repos don't have requirements
            
            # Install packages in venv from local requirements
            return self._install_packages_in_venv(repo_name, requirements_path)
            
        except Exception as e:
            logger.error(f"Error installing dependencies: {e}")
            return False
    
    def _create_venv_environment(self, repo_name: str) -> bool:
        """Create venv environment for repository"""
        try:
            if not self.config_manager.config.install_path:
                logger.error("Install path not configured")
                return False
            
            install_path = Path(self.config_manager.config.install_path)
            envs_path = install_path / "envs"
            venv_path = envs_path / repo_name
            
            # Create envs directory if it doesn't exist
            envs_path.mkdir(parents=True, exist_ok=True)
            
            # Remove existing venv if exists
            if venv_path.exists():
                logger.info(f"Removing existing venv: {venv_path}")
                import shutil
                shutil.rmtree(venv_path)
            
            # Create new venv using conda python
            python_exe = self._get_python_executable()
            
            logger.info(f"Creating venv environment: {venv_path}")
            result = subprocess.run([
                python_exe, "-m", "venv", str(venv_path)
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"✅ Created venv environment: {venv_path}")
                return True
            else:
                logger.error(f"Failed to create venv: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating venv environment: {e}")
            return False
    
    def _install_packages_in_venv(self, repo_name: str, requirements_path: Path) -> bool:
        """Install packages in venv environment using uv for regular packages and pip for torch"""
        try:
            # Install uv in venv first
            if not self._install_uv_in_venv(repo_name):
                logger.warning("Failed to install uv, falling back to pip for all packages")
                return self._install_packages_with_pip_only(repo_name, requirements_path)
            
            # Analyze requirements to separate torch and regular packages
            packages = self.analyzer.analyze_requirements(requirements_path)
            plan = self.analyzer.create_installation_plan(packages, None)
            
            pip_exe = self._get_pip_executable(repo_name)
            uv_cmd = self._get_uv_executable(repo_name)
            
            # Install torch packages with pip (they need special index URLs)
            if plan.torch_packages:
                logger.info("Installing PyTorch packages with pip...")
                torch_cmd = [pip_exe, "install"]
                
                for package in plan.torch_packages:
                    torch_cmd.append(str(package))
                
                if plan.torch_index_url:
                    torch_cmd.extend(["--index-url", plan.torch_index_url])
                
                self._run_pip_with_progress(torch_cmd, "Installing PyTorch packages")
            
            # Install ONNX packages with pip
            if plan.onnx_packages:
                logger.info("Installing ONNX packages with pip...")
                onnx_package_name = plan.onnx_package_name or "onnxruntime"
                
                # Find the onnxruntime package to get version if specified
                onnxruntime_package = next((p for p in plan.onnx_packages if p.name == 'onnxruntime'), None)
                if onnxruntime_package and onnxruntime_package.version:
                    package_str = f"{onnx_package_name}=={onnxruntime_package.version}"
                else:
                    package_str = onnx_package_name

                self._run_pip_with_progress([pip_exe, "install", package_str], f"Installing ONNX package: {package_str}")
            
            # Install TensorFlow packages with pip
            if plan.tensorflow_packages:
                logger.info("Installing TensorFlow packages with pip...")
                for package in plan.tensorflow_packages:
                    self._run_pip_with_progress([pip_exe, "install", str(package)], f"Installing TensorFlow package: {package}")
            
            # Handle Triton package separately
            triton_packages = [p for p in plan.regular_packages if 'triton' in p.name]
            regular_packages_no_triton = [p for p in plan.regular_packages if 'triton' not in p.name]

            if triton_packages:
                logger.info("Handling Triton package...")
                for package in triton_packages:
                    self._handle_triton_package(package, pip_exe)

            # Install regular packages with uv
            if regular_packages_no_triton:
                logger.info("Installing regular packages with uv...")
                
                # Create temporary requirements file for regular packages
                temp_requirements = requirements_path.parent / "requirements_regular_temp.txt"
                with open(temp_requirements, 'w', encoding='utf-8') as f:
                    for package in plan.regular_packages:
                        f.write(package.original_line + '\n')
                
                try:
                    # Use uv pip install for regular packages
                    uv_install_cmd = uv_cmd + ["pip", "install", "-r", str(temp_requirements)]
                    self._run_uv_with_progress(uv_install_cmd, "Installing regular packages with uv")
                finally:
                    # Clean up temporary file
                    try:
                        temp_requirements.unlink()
                    except Exception:
                        pass
            
            logger.info(f"✅ Successfully installed packages for {repo_name}")
            return True
                
        except Exception as e:
            logger.error(f"Error installing packages: {e}")
            return False
    
    def _install_packages_with_pip_only(self, repo_name: str, requirements_path: Path) -> bool:
        """Fallback method to install all packages with pip only"""
        try:
            pip_exe = self._get_pip_executable(repo_name)
            
            logger.info(f"Installing packages from {requirements_path} with pip")
            self._run_pip_with_progress([
                pip_exe, "install", "-r", str(requirements_path)
            ], f"Installing packages for {repo_name}")
            
            logger.info(f"✅ Successfully installed packages for {repo_name}")
            return True
                
        except Exception as e:
            logger.error(f"Error installing packages with pip: {e}")
            return False

    def _handle_triton_package(self, package: PackageInfo, pip_exe: str):
        """Handle Triton package installation based on OS."""
        if sys.platform == "win32":
            logger.info("Windows detected, installing triton-windows without version spec.")
            # Install triton-windows without version
            self._run_pip_with_progress([pip_exe, "install", "triton-windows"], "Installing triton-windows")
        else:
            logger.info("Skipping Triton installation on non-Windows OS.")
    
    def _execute_server_installation_plan(self, server_plan: Dict, repo_path: Path, repo_name: str) -> bool:
        """Execute installation plan from server"""
        try:
            pip_exe = self._get_pip_executable(repo_name)
            
            # Skip pip upgrade to avoid permission issues
            logger.info("⏭️  Skipping pip upgrade to avoid permission issues")
            
            # Execute installation steps in order
            for step in server_plan.get('installation_order', []):
                step_type = step.get('type', '')
                packages = step.get('packages', [])
                install_flags = step.get('install_flags', [])
                
                if not packages:
                    logger.info(f"⏭️  Skipping step {step['step']}: {step_type} (no packages)")
                    continue
                
                logger.info(f"🔧 Step {step['step']}: {step.get('description', step_type)}")
                
                # Determine which tool to use based on step type
                # First try uv, then fallback to pip for regular packages
                if step_type in ['regular', 'onnxruntime', 'tensorflow']:
                    # Always try to install uv for these packages (don't cache the result)
                    uv_available = self._install_uv_in_venv(repo_name)
                    logger.info(f"UV availability check for {step_type}: {uv_available}")
                    
                    if uv_available:
                        uv_cmd = self._get_uv_executable(repo_name)
                        install_cmd = uv_cmd + ["pip", "install"]
                        use_uv = True
                        use_uv_first = True
                        logger.info(f"Using UV for {step_type} packages")
                    else:
                        logger.warning(f"UV not available, using pip for {step_type} packages")
                        install_cmd = [pip_exe, "install"]
                        use_uv = False
                        use_uv_first = False
                else:
                    # Use pip for torch packages (may need specific index URLs)
                    install_cmd = [pip_exe, "install"]
                    use_uv = False
                    use_uv_first = False
                    logger.info(f"Using pip for {step_type} packages (torch packages need specific index URLs)")
                
                # Add packages with special handling for ONNX Runtime providers
                onnx_provider = None
                for package in packages:
                    if isinstance(package, dict):
                        pkg_name = package.get('package_name', '')
                        pkg_version = package.get('version', '')
                        index_url = package.get('index_url', '')
                        gpu_support = package.get('gpu_support', '')
                        
                        if pkg_name:
                            # Special handling for ONNX Runtime with specific providers
                            if step_type == 'onnxruntime':
                                onnx_package_name = server_plan.get('onnx_package_name') or 'onnxruntime'
                                pkg_name = onnx_package_name
                            
                            if pkg_version:
                                if pkg_version.startswith('>=') or pkg_version.startswith('=='):
                                    pkg_str = f"{pkg_name}{pkg_version}"
                                else:
                                    pkg_str = f"{pkg_name}=={pkg_version}"
                            else:
                                pkg_str = pkg_name
                            
                            install_cmd.append(pkg_str)
                            
                            # Add index URL if specified for this package
                            if index_url and '--index-url' not in install_cmd:
                                install_cmd.extend(['--index-url', index_url])

                if server_plan.get('torch_index_url') and '--index-url' not in install_cmd:
                    install_cmd.extend(['--index-url', server_plan['torch_index_url']])
                
                # Add install flags
                if install_flags:
                    install_cmd.extend(install_flags)
                
                # Execute command with progress and fallback logic
                step_description = f"Installing {step.get('description', step_type)}"
                
                if step_type in ['regular', 'onnxruntime', 'tensorflow'] and use_uv_first:
                    # Try uv first, then fallback to pip if it fails
                    try:
                        logger.info(f"📦 Installing with uv: {' '.join(install_cmd[3:])}")
                        self._run_uv_with_progress(install_cmd, step_description)
                    except subprocess.CalledProcessError as e:
                        logger.warning(f"⚠️ UV installation failed, trying pip fallback: {e}")
                        # Try pip fallback
                        pip_install_cmd = [pip_exe, "install"] + install_cmd[3:]  # Copy packages and flags
                        logger.info(f"📦 Installing with pip fallback: {' '.join(pip_install_cmd[2:])}")
                        self._run_pip_with_progress(pip_install_cmd, f"{step_description} (pip fallback)")
                elif use_uv:
                    logger.info(f"📦 Installing with uv: {' '.join(install_cmd[3:])}")
                    self._run_uv_with_progress(install_cmd, step_description)
                else:
                    logger.info(f"📦 Installing with pip: {' '.join(install_cmd[2:])}")
                    self._run_pip_with_progress(install_cmd, step_description)
            
            logger.info("✅ All server dependencies installed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Package installation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error executing server installation plan: {e}")
            return False
    
    def _execute_installation_plan(self, plan: InstallationPlan, original_requirements: Path, repo_name: str) -> bool:
        """Execute the installation plan using base Python"""
        try:
            pip_exe = self._get_pip_executable(repo_name)
            
            # Skip pip upgrade to avoid permission issues
            logger.info("⏭️  Skipping pip upgrade to avoid permission issues")
            
            # Install PyTorch packages with specific index
            if plan.torch_packages:
                logger.info("Installing PyTorch packages...")
                torch_cmd = [pip_exe, "install"]
                
                for package in plan.torch_packages:
                    torch_cmd.append(str(package))
                
                if plan.torch_index_url:
                    torch_cmd.extend(["--index-url", plan.torch_index_url])
                
                self._run_pip_with_progress(torch_cmd, "Installing PyTorch packages")
            
            # Install ONNX Runtime packages with GPU auto-detection for fallback
            if plan.onnx_packages:
                logger.info("Installing ONNX Runtime packages...")
                from portablesource.get_gpu import GPUDetector, GPUType
                gpu_detector = GPUDetector()
                primary_gpu_type = gpu_detector.get_primary_gpu_type()
                
                for package in plan.onnx_packages:
                    # Auto-detect GPU version for onnxruntime when falling back to local requirements
                    package_str = str(package)
                    if package.name == "onnxruntime" and primary_gpu_type == GPUType.NVIDIA:
                        # Replace onnxruntime with onnxruntime-gpu for NVIDIA GPUs
                        if package.version:
                            package_str = f"onnxruntime-gpu=={package.version}"
                        else:
                            package_str = "onnxruntime-gpu"
                        logger.info(f"🔄 Auto-detected NVIDIA GPU, using {package_str} instead of {package}")
                    elif package.name == "onnxruntime" and primary_gpu_type == GPUType.AMD and os.name == "nt":
                        # AMD GPU on Windows - use DirectML
                        if package.version:
                            package_str = f"onnxruntime-directml=={package.version}"
                        else:
                            package_str = "onnxruntime-directml"
                        logger.info(f"🔄 Auto-detected AMD GPU on Windows, using {package_str} instead of {package}")
                    elif package.name == "onnxruntime" and primary_gpu_type == GPUType.AMD and os.name == "posix":
                        # AMD GPU on Linux - use ROCm (if available)
                        if package.version:
                            package_str = f"onnxruntime-rocm=={package.version}"
                        else:
                            package_str = "onnxruntime-rocm"
                        logger.info(f"🔄 Auto-detected AMD GPU on Linux, using {package_str} instead of {package}")
                    elif package.name == "onnxruntime" and primary_gpu_type == GPUType.INTEL:
                        # Intel GPU - use DirectML
                        if package.version:
                            package_str = f"onnxruntime-directml=={package.version}"
                        else:
                            package_str = "onnxruntime-directml"
                        logger.info(f"🔄 Auto-detected Intel GPU, using {package_str} instead of {package}")

                    logger.info(f"Installing ONNX package: {package_str}")
                    self._run_pip_with_progress([pip_exe, "install", package_str], f"Installing ONNX package: {package_str}")
            
            # Install TensorFlow packages (if any)
            if plan.tensorflow_packages:
                logger.info("Installing TensorFlow packages...")
                for package in plan.tensorflow_packages:
                    self._run_pip_with_progress([pip_exe, "install", str(package)], f"Installing TensorFlow package: {package}")
            
            # Install uv in venv for regular packages
            if plan.regular_packages:
                if not self._install_uv_in_venv(repo_name):
                    logger.warning("Failed to install uv, using pip for regular packages")
                    # Fallback to pip for regular packages
                    modified_requirements = original_requirements.parent / "requirements_modified.txt"
                    with open(modified_requirements, 'w', encoding='utf-8') as f:
                        for package in plan.regular_packages:
                            f.write(package.original_line + '\n')
                    
                    if modified_requirements.stat().st_size > 0:
                        logger.info("Installing regular packages with pip...")
                        self._run_pip_with_progress([pip_exe, "install", "-r", str(modified_requirements)], "Installing regular packages")
                    
                    try:
                        modified_requirements.unlink()
                    except Exception:
                        pass
                else:
                    # Use uv for regular packages
                    uv_cmd = self._get_uv_executable(repo_name)
                    modified_requirements = original_requirements.parent / "requirements_modified.txt"
                    with open(modified_requirements, 'w', encoding='utf-8') as f:
                        for package in plan.regular_packages:
                            f.write(package.original_line + '\n')
                    
                    if modified_requirements.stat().st_size > 0:
                        logger.info("Installing regular packages with uv...")
                        uv_install_cmd = uv_cmd + ["pip", "install", "-r", str(modified_requirements)]
                        self._run_uv_with_progress(uv_install_cmd, "Installing regular packages with uv")
                    
                    try:
                        modified_requirements.unlink()
                    except Exception:
                        pass
            
            logger.info("All dependencies installed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Package installation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error executing installation plan: {e}")
            return False
    
    def _setup_facefusion(self, repo_path: Path):
        """Special setup for FaceFusion"""
        # FaceFusion-specific setup can be added here
        logger.info("Applying FaceFusion-specific setup...")
        
        # Create models directory
        models_dir = repo_path / "models"
        models_dir.mkdir(exist_ok=True)
        
        # Any other FaceFusion-specific setup
        pass
    
    def _generate_startup_script(self, repo_path: Path, repo_info: Dict):
        """Generate startup script with dual activation (conda + venv)"""
        try:
            repo_name = repo_path.name.lower()
            
            # Determine main file using our intelligent finder
            main_file = repo_info.get("main_file")
            if not main_file:
                main_file = self.main_file_finder.find_main_file(repo_name, repo_path, repo_info["url"])
            
            if not main_file:
                logger.error("❌ Could not determine main file for repository!")
                logger.error("📝 Please manually specify the main file to run:")
                logger.error(f"   Available Python files in {repo_path}:")
                for py_file in repo_path.glob("*.py"):
                    logger.error(f"   - {py_file.name}")
                return False
            
            # Create startup script with dual activation
            bat_file = repo_path / f"start_{repo_name}.bat"
            
            if not self.config_manager.config.install_path:
                logger.error("Install path not configured")
                return False
            
            install_path = Path(self.config_manager.config.install_path)
            conda_activate = install_path / "miniconda" / "Scripts" / "activate.bat"
            venv_activate = install_path / "envs" / repo_name / "Scripts" / "activate.bat"
            
            # Get program args from repo info
            program_args = repo_info.get('program_args', '')
            
            # Debug output for program args
            logger.info(f"🔧 Program args for {repo_name}: '{program_args}'")
            if program_args:
                logger.info(f"✅ Program args will be applied: {program_args}")
            else:
                logger.info(f"ℹ️ No program args specified for {repo_name}")
            
            # Generate batch file content
            bat_content = f"""@echo off
echo Launch {repo_name}...
cd /d "{repo_path}"
call "{conda_activate}"
call conda activate portablesource
call "{venv_activate}"
cls
python {main_file} {program_args}
pause
"""
            
            # Write batch file
            with open(bat_file, 'w', encoding='utf-8') as f:
                f.write(bat_content)
            
            logger.info(f"✅ Startup script generated: {bat_file}")
            logger.info(f"🚀 Main file: {main_file}")
            return True
                 
        except Exception as e:
            logger.error(f"Error generating startup script: {e}")
            return False

    def _send_download_stats(self, repo_name: str):
        """Send download statistics to server"""
        try:
            if not self.server_client.is_server_available():
                return  # Server not available, skip stats
            
            import requests
            
            # Send download record to server
            response = requests.post(
                f"{self.server_client.server_url}/api/repository/{repo_name}/download",
                json={'success': True},
                timeout=5
            )
            
            if response.status_code == 200:
                logger.info(f"📊 Download statistics sent for {repo_name}")
            else:
                logger.debug(f"Failed to send download statistics: {response.status_code}")
                
        except Exception as e:
            logger.debug(f"Error sending download statistics: {e}")
            # Don't fail installation if stats can't be sent
    
    def _run_pip_with_progress(self, pip_cmd: List[str], description: str):
        """Run pip command with progress bar if tqdm is available"""
        TQDM_AVAILABLE = True
        try:
            if TQDM_AVAILABLE:
                # Run with progress bar
                logger.info(f"🔄 {description}...")
                
                # Start the process
                process = subprocess.Popen(
                    pip_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Create progress bar
                with tqdm(desc=description, unit="line", dynamic_ncols=True) as pbar:
                    output_lines = []
                    if process.stdout:
                        for line in process.stdout:
                            output_lines.append(line)
                            pbar.update(1)
                            
                            # Show important messages
                            if "Installing" in line or "Downloading" in line or "ERROR" in line:
                                pbar.set_postfix_str(line.strip()[:50])
                
                # Wait for completion
                process.wait()
                
                if process.returncode != 0:
                    error_output = ''.join(output_lines)
                    raise subprocess.CalledProcessError(process.returncode, pip_cmd, error_output)
                    
                logger.info(f"✅ {description} completed")
            else:
                # Fallback to regular subprocess without progress
                logger.info(f"🔄 {description}...")
                subprocess.run(pip_cmd, check=True, capture_output=True, text=True)
                logger.info(f"✅ {description} completed")
                
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ {description} failed: {e}")
            raise
        except Exception as e:
             logger.error(f"❌ Error during {description}: {e}")
             raise
    
    def _run_uv_with_progress(self, uv_cmd: List[str], description: str):
        """Run uv command with progress bar if tqdm is available"""
        TQDM_AVAILABLE = True
        try:
            if TQDM_AVAILABLE:
                # Run with progress bar
                logger.info(f"🔄 {description}...")
                
                # Start the process
                process = subprocess.Popen(
                    uv_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Create progress bar
                with tqdm(desc=description, unit="line", dynamic_ncols=True) as pbar:
                    output_lines = []
                    if process.stdout:
                        for line in process.stdout:
                            output_lines.append(line)
                            pbar.update(1)
                            
                            # Show important messages
                            if "Installing" in line or "Downloading" in line or "ERROR" in line or "Resolved" in line:
                                pbar.set_postfix_str(line.strip()[:50])
                
                # Wait for completion
                process.wait()
                
                if process.returncode != 0:
                    error_output = ''.join(output_lines)
                    raise subprocess.CalledProcessError(process.returncode, uv_cmd, error_output)
                    
                logger.info(f"✅ {description} completed")
            else:
                # Fallback to regular subprocess without progress
                logger.info(f"🔄 {description}...")
                subprocess.run(uv_cmd, check=True, capture_output=True, text=True)
                logger.info(f"✅ {description} completed")
                
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ {description} failed: {e}")
            raise
        except Exception as e:
             logger.error(f"❌ Error during {description}: {e}")
             raise
     
    def _run_git_with_progress(self, git_cmd: List[str], description: str):
         """Run git command with progress bar if tqdm is available"""
         TQDM_AVAILABLE = True
         try:
             if TQDM_AVAILABLE:
                 # Run with progress bar
                 logger.info(f"🔄 {description}...")
                 
                 # Start the process
                 process = subprocess.Popen(
                     git_cmd,
                     stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT,
                     text=True,
                     bufsize=1,
                     universal_newlines=True
                 )
                 
                 # Create progress bar
                 with tqdm(desc=description, unit="line", dynamic_ncols=True) as pbar:
                     output_lines = []
                     if process.stdout:
                         for line in process.stdout:
                             output_lines.append(line)
                             pbar.update(1)
                             
                             # Show important git messages
                             if any(keyword in line.lower() for keyword in ["cloning", "receiving", "resolving", "updating", "error"]):
                                 pbar.set_postfix_str(line.strip()[:50])
                 
                 # Wait for completion
                 process.wait()
                 
                 if process.returncode != 0:
                     error_output = ''.join(output_lines)
                     # Create CalledProcessError with output for better error handling
                     error = subprocess.CalledProcessError(process.returncode, git_cmd, error_output)
                     error.output = error_output  # Ensure output is available
                     raise error
                     
                 logger.info(f"✅ {description} completed")
             else:
                 # Fallback to regular subprocess without progress
                 logger.info(f"🔄 {description}...")
                 result = subprocess.run(git_cmd, check=True, capture_output=True, text=True)
                 logger.info(f"✅ {description} completed")
                 
         except subprocess.CalledProcessError as e:
             logger.error(f"❌ {description} failed: {e}")
             raise
         except Exception as e:
             logger.error(f"❌ Error during {description}: {e}")
             raise


# Main execution for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test the installer
    installer = RepositoryInstaller()
    
    # Test with FaceFusion
    print("Testing repository installer with FaceFusion...")
    success = installer.install_repository("facefusion")
    
    if success:
        print("✅ Installation successful!")
    else:
        print("❌ Installation failed!")
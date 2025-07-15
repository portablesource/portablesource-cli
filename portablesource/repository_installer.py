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
from typing import Dict, List, Optional, Tuple, Set
from urllib.parse import urlparse
from dataclasses import dataclass
from enum import Enum

from .config import ConfigManager, GPUGeneration, CUDAVersion
from .get_gpu import GPUDetector, GPUType


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ServerAPIClient:
    """Client for PortableSource server API"""
    
    def __init__(self, server_url: str = "http://localhost:5000"):
        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.session.timeout = 10
    
    def get_repository_info(self, name: str) -> Optional[Dict]:
        """Get repository information from server"""
        try:
            url = f"{self.server_url}/api/repository/{name.lower()}"
            response = self.session.get(url)
            
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
            response = self.session.get(url, params={'q': query})
            
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
            url = f"{self.server_url}/api/repository/{name.lower()}/dependencies"
            response = self.session.get(url)
            
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
            url = f"{self.server_url}/api/repository/{name.lower()}/dependencies/install_plan"
            response = self.session.get(url)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.debug(f"No installation plan found for repository '{name}'")
                return None
            else:
                logger.warning(f"Server returned status {response.status_code} for installation plan of '{name}'")
                return None
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Could not connect to server for installation plan: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting installation plan from server: {e}")
            return None
    
    def is_server_available(self) -> bool:
        """Check if server is available"""
        try:
            url = f"{self.server_url}/api/repositories"
            response = self.session.get(url)
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
    torch_packages: List[PackageInfo]
    onnx_packages: List[PackageInfo]
    tensorflow_packages: List[PackageInfo]
    regular_packages: List[PackageInfo]
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
        plan = InstallationPlan(
            torch_packages=[],
            onnx_packages=[],
            tensorflow_packages=[],
            regular_packages=[]
        )
        
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
            plan.torch_index_url = self._get_torch_index_url(gpu_config)
        
        # Determine ONNX Runtime package
        if plan.onnx_packages:
            plan.onnx_package_name = self._get_onnx_package_name(gpu_config)
        
        return plan
    
    def _get_torch_index_url(self, gpu_config) -> str:
        """Get PyTorch index URL based on GPU configuration"""
        if not gpu_config or gpu_config.recommended_backend != "cuda":
            return "https://download.pytorch.org/whl/cpu"
        
        # Determine CUDA version for PyTorch
        if gpu_config.cuda_version == CUDAVersion.CUDA_128:
            return "https://download.pytorch.org/whl/cu128"
        elif gpu_config.cuda_version == CUDAVersion.CUDA_124:
            return "https://download.pytorch.org/whl/cu124"
        elif gpu_config.cuda_version == CUDAVersion.CUDA_118:
            return "https://download.pytorch.org/whl/cu118"
        else:
            return "https://download.pytorch.org/whl/cu118"  # Fallback
    
    def _get_onnx_package_name(self, gpu_config) -> str:
        """Get ONNX Runtime package name based on GPU configuration"""
        if not gpu_config:
            return "onnxruntime"
        
        if gpu_config.recommended_backend == "cuda":
            return "onnxruntime-gpu"
        elif gpu_config.recommended_backend == "directml":
            return "onnxruntime-directml"
        elif gpu_config.recommended_backend == "openvino":
            # TODO: Add OpenVINO support for Linux
            return "onnxruntime-openvino"
        else:
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
                ["--pre", "--extra-index-url", "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/"],
                {
                    "ORT_CUDA_UNAVAILABLE": "0",
                    "ORT_TENSORRT_UNAVAILABLE": "0",
                    "TENSORRT_ROOT": "${PROGRAMFILES}\\NVIDIA Corporation\\NVIDIA TensorRT",
                    "CUDNN_PATH": "${PROGRAMFILES}\\NVIDIA\\CUDNN"
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
            gpu_detector = GPUDetector()
            gpu_config = gpu_detector.detect_gpu()
            package_name = self._get_onnx_package_name(gpu_config)
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
    
    def __init__(self, config_manager: Optional[ConfigManager] = None, server_url: str = "http://localhost:5000"):
        self.config_manager = config_manager or ConfigManager()
        self.analyzer = RequirementsAnalyzer()
        
        # Initialize server client and main file finder
        self.server_client = ServerAPIClient(server_url)
        self.main_file_finder = MainFileFinder(self.server_client)
        
        # Check server availability
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
    
    def install_repository(self, repo_url_or_name: str, install_path: Optional[str] = None) -> bool:
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
            
            install_path = Path(install_path)
            
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
        """Clone or update repository"""
        try:
            git_exe = self._get_git_executable()
            
            if repo_path.exists():
                # Update existing repository
                logger.info(f"Updating repository at {repo_path}")
                os.chdir(repo_path)
                
                # Check if it's a git repository
                if (repo_path / ".git").exists():
                    subprocess.run([git_exe, "pull"], check=True, 
                                 capture_output=True, text=True)
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
                
                subprocess.run(cmd, check=True, capture_output=True, text=True)
            
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error cloning/updating repository: {e}")
            return False
    
    def _get_git_executable(self) -> str:
        """Get git executable path from conda environment"""
        if self.config_manager.config.system_paths:
            install_path = Path(self.config_manager.config.system_paths.install_path)
            conda_env_path = install_path / "miniconda" / "envs" / "portablesource"
            git_path = conda_env_path / "Scripts" / "git.exe"
            if git_path.exists():
                return str(git_path)
        
        # Fallback to system git
        return "git"
    

    
    def _get_python_executable(self) -> str:
        """Get Python executable path from conda environment"""
        if self.config_manager.config.system_paths:
            install_path = Path(self.config_manager.config.system_paths.install_path)
            conda_env_path = install_path / "miniconda" / "envs" / "portablesource"
            python_path = conda_env_path / "python.exe"
            if python_path.exists():
                return str(python_path)
        
        # Fallback to system python
        return "python"
    
    def _get_pip_executable(self, repo_name: str) -> str:
        """Get pip executable path from repository's venv"""
        if self.config_manager.config.system_paths:
            install_path = Path(self.config_manager.config.system_paths.install_path)
            venv_path = install_path / "envs" / repo_name
            pip_path = venv_path / "Scripts" / "pip.exe"
            if pip_path.exists():
                return str(pip_path)
        
        # Fallback to system pip
        return "pip"
    
    def _install_dependencies(self, repo_path: Path) -> bool:
        """Install dependencies in venv with new architecture"""
        try:
            repo_name = repo_path.name.lower()
            logger.info(f"📦 Installing dependencies for {repo_name}")
            
            # Create venv environment for the repository
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            # Find requirements file
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
            
            # Install packages in venv
            return self._install_packages_in_venv(repo_name, requirements_path)
            
        except Exception as e:
            logger.error(f"Error installing dependencies: {e}")
            return False
    
    def _create_venv_environment(self, repo_name: str) -> bool:
        """Create venv environment for repository"""
        try:
            if not self.config_manager.config.system_paths:
                logger.error("System paths not configured")
                return False
            
            install_path = Path(self.config_manager.config.system_paths.install_path)
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
        """Install packages in venv environment"""
        try:
            pip_exe = self._get_pip_executable(repo_name)
            
            logger.info(f"Installing packages from {requirements_path}")
            result = subprocess.run([
                pip_exe, "install", "-r", str(requirements_path)
            ], capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"✅ Successfully installed packages for {repo_name}")
                return True
            else:
                logger.error(f"Failed to install packages: {result.stderr}")
                return False
                
        except Exception as e:
            logger.error(f"Error installing packages: {e}")
            return False
    
    def _execute_server_installation_plan(self, server_plan: Dict, repo_path: Path) -> bool:
        """Execute installation plan from server"""
        try:
            pip_exe = self._get_pip_executable()
            
            # Upgrade pip first
            subprocess.run([pip_exe, "install", "--upgrade", "pip"], 
                         check=True, capture_output=True, text=True)
            
            # Execute installation steps in order
            for step in server_plan.get('installation_order', []):
                step_type = step.get('type', '')
                packages = step.get('packages', [])
                install_flags = step.get('install_flags', [])
                
                if not packages:
                    logger.info(f"⏭️  Skipping step {step['step']}: {step_type} (no packages)")
                    continue
                
                logger.info(f"🔧 Step {step['step']}: {step.get('description', step_type)}")
                
                # Build pip command
                pip_cmd = [pip_exe, "install"]
                
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
                            if step_type == 'onnxruntime' and gpu_support:
                                onnx_provider = gpu_support
                                onnx_pkg, onnx_flags, onnx_env = self._get_onnx_package_for_provider(gpu_support)
                                
                                # Set environment variables for TensorRT
                                if onnx_env:
                                    import os
                                    for env_var, env_val in onnx_env.items():
                                        # Expand environment variables in paths
                                        expanded_val = os.path.expandvars(env_val)
                                        os.environ[env_var] = expanded_val
                                        logger.info(f"🔧 Set environment variable: {env_var}={expanded_val}")
                                
                                # Use the provider-specific package name
                                pkg_name = onnx_pkg
                                
                                # Add provider-specific flags
                                if onnx_flags:
                                    pip_cmd.extend(onnx_flags)
                            
                            if pkg_version:
                                if pkg_version.startswith('>=') or pkg_version.startswith('=='):
                                    pkg_str = f"{pkg_name}{pkg_version}"
                                else:
                                    pkg_str = f"{pkg_name}=={pkg_version}"
                            else:
                                pkg_str = pkg_name
                            
                            pip_cmd.append(pkg_str)
                            
                            # Add index URL if specified for this package
                            if index_url and '--index-url' not in pip_cmd:
                                pip_cmd.extend(['--index-url', index_url])
                    else:
                        pip_cmd.append(str(package))
                
                # Add install flags
                if install_flags:
                    pip_cmd.extend(install_flags)
                
                # Execute pip command
                logger.info(f"📦 Installing: {' '.join(pip_cmd[2:])}")
                subprocess.run(pip_cmd, check=True, capture_output=True, text=True)
            
            logger.info("✅ All server dependencies installed successfully")
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"❌ Package installation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Error executing server installation plan: {e}")
            return False
    
    def _execute_installation_plan(self, plan: InstallationPlan, original_requirements: Path) -> bool:
        """Execute the installation plan using base Python"""
        try:
            pip_exe = self._get_pip_executable()
            
            # Upgrade pip first
            subprocess.run([pip_exe, "install", "--upgrade", "pip"], 
                         check=True, capture_output=True, text=True)
            
            # Install PyTorch packages with specific index
            if plan.torch_packages:
                logger.info("Installing PyTorch packages...")
                torch_cmd = [pip_exe, "install"]
                
                for package in plan.torch_packages:
                    torch_cmd.append(str(package))
                
                if plan.torch_index_url:
                    torch_cmd.extend(["--index-url", plan.torch_index_url])
                
                subprocess.run(torch_cmd, check=True, capture_output=True, text=True)
            
            # Install ONNX Runtime with correct variant
            if plan.onnx_packages and plan.onnx_package_name:
                logger.info(f"Installing ONNX Runtime: {plan.onnx_package_name}")
                subprocess.run([pip_exe, "install", plan.onnx_package_name], 
                             check=True, capture_output=True, text=True)
            
            # Install TensorFlow packages (if any)
            if plan.tensorflow_packages:
                logger.info("Installing TensorFlow packages...")
                for package in plan.tensorflow_packages:
                    subprocess.run([pip_exe, "install", str(package)], 
                                 check=True, capture_output=True, text=True)
            
            # Create modified requirements.txt without special packages
            if plan.regular_packages:
                modified_requirements = original_requirements.parent / "requirements_modified.txt"
                with open(modified_requirements, 'w', encoding='utf-8') as f:
                    for package in plan.regular_packages:
                        f.write(package.original_line + '\n')
                
                # Install regular packages
                if modified_requirements.stat().st_size > 0:
                    logger.info("Installing regular packages...")
                    subprocess.run([pip_exe, "install", "-r", str(modified_requirements)], 
                                 check=True, capture_output=True, text=True)
                
                # Clean up temporary file
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
            
            if not self.config_manager.config.system_paths:
                logger.error("System paths not configured")
                return False
            
            install_path = Path(self.config_manager.config.system_paths.install_path)
            conda_activate = install_path / "miniconda" / "Scripts" / "activate.bat"
            venv_activate = install_path / "envs" / repo_name / "Scripts" / "activate.bat"
            
            # Generate batch file content
            bat_content = f"""@echo off
echo 🚀 Starting {repo_name}...
echo.

REM Activate conda environment (tools)
call "{conda_activate}" && conda activate portablesource

REM Activate venv environment (packages)
call "{venv_activate}"

REM Change to repository directory
cd /d "{repo_path}"

REM Run the main file
python {main_file}

REM Keep window open on error
if %errorlevel% neq 0 (
    echo.
    echo ❌ Error occurred. Press any key to exit...
    pause >nul
)
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
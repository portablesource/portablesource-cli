#!/usr/bin/env python3
"""
Universal Repository Installer for PortableSource

This module provides intelligent installation of any repository with automatic
dependency analysis and GPU-specific package handling.
"""

import os
import re
import subprocess
import logging
import requests
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse
from dataclasses import dataclass, field
from enum import Enum

from tqdm import tqdm

from .config import ConfigManager, SERVER_DOMAIN
from .envs_manager import PortableEnvironmentManager

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ServerAPIClient:
    """Client for PortableSource server API"""

    def __init__(self, server_url: str = f"https://{SERVER_DOMAIN}"):
        self.server_url = server_url.rstrip('/')
        self.session = requests.Session()
        self.timeout = 10
    
    def get_repository_info(self, name: str) -> Optional[Dict]:
        """Get repository information from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                if self._validate_repository_info(data):
                    return data
                else:
                    logger.error(f"Invalid repository info data received for '{name}'")
                    return None
            elif response.status_code == 404:
                return None  # Not found is expected, don't log
            else:
                logger.warning(f"Server returned status {response.status_code} for repository '{name}'")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Server timeout while getting repository info for '{name}'")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"Could not connect to server for repository info '{name}'")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error getting repository info for '{name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting repository info for '{name}': {e}")
            return None
    
    def search_repositories(self, query: str) -> List[Dict]:
        """Search repositories in server database"""
        try:
            url = f"{self.server_url}/api/search"
            response = self.session.get(url, params={'q': query}, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                repositories = data.get('repositories', [])
                if isinstance(repositories, list):
                    return repositories
                else:
                    logger.error(f"Invalid search results format for query '{query}'")
                    return []
            else:
                logger.warning(f"Server search returned status {response.status_code} for query '{query}'")
                return []
                
        except requests.exceptions.Timeout:
            logger.warning(f"Server timeout while searching for '{query}'")
            return []
        except requests.exceptions.ConnectionError:
            logger.warning(f"Could not connect to server for search '{query}'")
            return []
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error searching for '{query}': {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error searching for '{query}': {e}")
            return []
    
    def get_repository_dependencies(self, name: str) -> Optional[Dict]:
        """Get repository dependencies from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}/dependencies"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                data = response.json()
                if self._validate_dependencies_data(data):
                    return data
                else:
                    logger.error(f"Invalid dependencies data received for '{name}'")
                    return None
            elif response.status_code == 404:
                return None  # Not found is expected, don't log
            else:
                logger.warning(f"Server returned status {response.status_code} for dependencies of '{name}'")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Server timeout while getting dependencies for '{name}'")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"Could not connect to server for dependencies '{name}'")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error getting dependencies for '{name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting dependencies for '{name}': {e}")
            return None
    
    def get_installation_plan(self, name: str) -> Optional[Dict]:
        """Get installation plan from server"""
        try:
            url = f"{self.server_url}/api/repositories/{name.lower()}/install-plan"
            response = self.session.get(url, timeout=self.timeout)
            
            if response.status_code == 200:
                plan_data = response.json()
                if plan_data.get('success') and 'installation_plan' in plan_data:
                    # Return the installation_plan part directly
                    return plan_data['installation_plan']
                else:
                    logger.error(f"Invalid installation plan data received for '{name}'")
                    return None
            elif response.status_code == 404:
                return None  # Not found is expected, don't log
            else:
                logger.warning(f"Server returned status {response.status_code} for installation plan of '{name}'")
                return None
                
        except requests.exceptions.Timeout:
            logger.warning(f"Server timeout while getting installation plan for '{name}'")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning(f"Could not connect to server for installation plan '{name}'")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error getting installation plan for '{name}': {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting installation plan for '{name}': {e}")
            return None
    
    def is_server_available(self) -> bool:
        """Check if server is available"""
        try:
            url = f"{self.server_url}/api/repositories"
            response = self.session.get(url, timeout=self.timeout)
            return response.status_code == 200
        except Exception:
            return False
    
    def _validate_repository_info(self, data: Dict) -> bool:
        """
        Validate repository information data from server
        
        Args:
            data: Repository info data from server
            
        Returns:
            True if data is valid
        """
        if not data or not isinstance(data, dict):
            return False
        
        # Check for new server response format with success and repository fields
        if 'success' in data and 'repository' in data:
            if not data.get('success', False):
                return False
            
            repository = data.get('repository')
            if not repository or not isinstance(repository, dict):
                return False
            
            # Check for repositoryUrl field in the new format
            if 'repositoryUrl' not in repository:
                return False
            
            # Validate URL format if present
            url = repository.get('repositoryUrl')
            if url and not isinstance(url, str):
                return False
            
            # Validate optional fields if present
            optional_fields = ['filePath', 'programArgs', 'description']
            for field in optional_fields:
                if field in repository and not isinstance(repository[field], str):
                    return False
            
            return True
        
        # Fallback to old format validation for backward compatibility
        # Check for basic structure - url is the minimum required field
        if 'url' not in data:
            return False
        
        # Validate URL format if present
        url = data.get('url')
        if url and not isinstance(url, str):
            return False
        
        # Validate optional fields if present
        optional_fields = ['main_file', 'program_args', 'description']
        for field in optional_fields:
            if field in data and not isinstance(data[field], str):
                return False
        
        return True
    
    def _validate_installation_plan(self, plan: Dict) -> bool:
        """
        Validate installation plan data from server
        
        Args:
            plan: Installation plan from server (already extracted installation_plan part)
            
        Returns:
            True if plan is valid
        """
        if not plan or not isinstance(plan, dict):
            return False
        
        # Check for steps field
        if 'steps' not in plan:
            return False
        
        steps = plan['steps']
        if not isinstance(steps, list):
            return False
        
        # Validate each step
        for step in steps:
            if not isinstance(step, dict):
                return False
            if 'type' not in step or 'packages' not in step:
                return False
            if not isinstance(step['packages'], list):
                return False
            
            # Validate step type
            valid_types = ['torch', 'regular', 'onnxruntime', 'insightface', 'triton']
            if step['type'] not in valid_types:
                return False
            
            # Validate packages structure
            for package in step['packages']:
                if not isinstance(package, str):
                    return False
        
        return True
    
    def _validate_dependencies_data(self, data: Dict) -> bool:
        """
        Validate dependencies data from server
        
        Args:
            data: Dependencies data from server
            
        Returns:
            True if data is valid
        """
        if not data or not isinstance(data, dict):
            return False
        
        # Dependencies data should have at least one of these fields
        expected_fields = ['requirements', 'packages', 'dependencies']
        if not any(field in data for field in expected_fields):
            return False
        
        # Validate structure of present fields
        for field in expected_fields:
            if field in data:
                field_data = data[field]
                if not isinstance(field_data, (list, dict, str)):
                    return False
        
        return True


class PackageType(Enum):
    """Types of special packages that need custom handling"""
    TORCH = "torch"
    ONNXRUNTIME = "onnxruntime"
    INSIGHTFACE = "insightface"
    TRITON = "triton"
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
    insightface_packages: List[PackageInfo] = field(default_factory=list)
    triton_packages: List[PackageInfo] = field(default_factory=list)
    regular_packages: List[PackageInfo] = field(default_factory=list)
    torch_index_url: Optional[str] = None
    onnx_package_name: Optional[str] = None


class RequirementsAnalyzer:
    """Analyzes requirements.txt files and categorizes packages"""
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.torch_packages = {"torch", "torchvision", "torchaudio", "torchtext", "torchdata"}
        self.onnx_packages = {"onnxruntime", "onnxruntime-gpu", "onnxruntime-directml", "onnxruntime-openvino"}
        self.insightface_packages = {"insightface"}
        self.triton_packages = {"triton"}
    
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
        
        # Ignore lines with --index-url
        if '--index-url' in line:
            return None
            
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
        elif package_name in self.insightface_packages:
            package_type = PackageType.INSIGHTFACE
        elif package_name in self.triton_packages:
            package_type = PackageType.TRITON
        
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
        plan = InstallationPlan()
        
        # Get GPU information from config
        gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
        
        # Categorize packages
        for package in packages:
            if package.package_type == PackageType.TORCH:
                plan.torch_packages.append(package)
            elif package.package_type == PackageType.ONNXRUNTIME:
                plan.onnx_packages.append(package)
            elif package.package_type == PackageType.INSIGHTFACE:
                plan.insightface_packages.append(package)
            elif package.package_type == PackageType.TRITON:
                plan.triton_packages.append(package)
            else:
                plan.regular_packages.append(package)
        
        # Determine PyTorch index URL
        if plan.torch_packages:
            plan.torch_index_url = self._get_torch_index_url_from_config(gpu_config_obj)
        
        # Auto-detect and set the correct ONNX Runtime package name
        if plan.onnx_packages:
            plan.onnx_package_name = self._get_onnx_package_name_from_config(gpu_config_obj)
        
        return plan
    
    def _get_torch_index_url_from_config(self, gpu_config) -> str:
        """Get PyTorch index URL based on GPU configuration"""
        if not gpu_config or not gpu_config.name or not gpu_config.name.upper().startswith('NVIDIA'):
            return "https://download.pytorch.org/whl/cpu"
        
        # Get CUDA version from config
        cuda_version = gpu_config.cuda_version if gpu_config else None
        
        # Determine CUDA version for PyTorch
        if cuda_version:
            # Handle both string and object formats
            if hasattr(cuda_version, 'value'):
                cuda_version_str = cuda_version.value
            else:
                cuda_version_str = str(cuda_version)
            
            # Map CUDA versions to PyTorch index URLs
            if cuda_version_str in ["12.8", "128"]:
                return "https://download.pytorch.org/whl/cu128"
            elif cuda_version_str in ["12.4", "124"]:
                return "https://download.pytorch.org/whl/cu124"
            elif cuda_version_str in ["11.8", "118"]:
                return "https://download.pytorch.org/whl/cu118"
        
        return "https://download.pytorch.org/whl/cpu"  # Fallback to CPU
    
    def _get_onnx_package_name_from_config(self, gpu_config) -> str:
        """Get ONNX Runtime package name based on GPU configuration"""
        import os

        if not gpu_config or not gpu_config.name:
            return "onnxruntime"

        gpu_name_upper = gpu_config.name.upper() if gpu_config and gpu_config.name else ""
        if gpu_name_upper.startswith('NVIDIA'):
            return "onnxruntime-gpu"
        elif (gpu_name_upper.startswith('AMD') or gpu_name_upper.startswith('INTEL')) and os.name == 'nt':
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
            # Auto-detect based on system config
            gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
            package_name = self._get_onnx_package_name_from_config(gpu_config_obj)
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
        server_info = self.server_client.get_repository_info(repo_name)
        
        if server_info:
            # Handle new server response format
            if 'success' in server_info and 'repository' in server_info:
                repository = server_info['repository']
                main_file = repository.get('filePath')
            else:
                # Handle old server response format for backward compatibility
                main_file = server_info.get('main_file')
            
            if main_file and self._validate_main_file(repo_path, main_file):
                return main_file
            else:
                logger.warning(f"Server returned main file '{main_file}' but it doesn't exist in repository")
        
        # Strategy 2: Try URL-based lookup (extract repo name from URL)
        if not server_info:
            url_repo_name = self._extract_repo_name_from_url(repo_url)
            if url_repo_name != repo_name:
                server_info = self.server_client.get_repository_info(url_repo_name)
                if server_info:
                    # Handle new server response format
                    if 'success' in server_info and 'repository' in server_info:
                        repository = server_info['repository']
                        main_file = repository.get('filePath')
                    else:
                        # Handle old server response format for backward compatibility
                        main_file = server_info.get('main_file')
                    
                    if main_file and self._validate_main_file(repo_path, main_file):
                        return main_file
        
        # Strategy 3: Search server database for similar repositories
        search_results = self.server_client.search_repositories(repo_name)
        for result in search_results:
            main_file = result.get('main_file')
            if main_file and self._validate_main_file(repo_path, main_file):
                return main_file
        
        # Strategy 4: Common file fallbacks
        for main_file in self.common_main_files:
            if self._validate_main_file(repo_path, main_file):
                return main_file
        
        # Strategy 5: Look for Python files in root directory
        python_files = list(repo_path.glob("*.py"))
        
        # Filter out common non-main files
        excluded_patterns = ['test_', 'setup.py', 'config.py', '__', 'install']
        main_candidates = []
        
        for py_file in python_files:
            filename = py_file.name.lower()
            if not any(pattern in filename for pattern in excluded_patterns):
                main_candidates.append(py_file.name)
        
        if len(main_candidates) == 1:
            return main_candidates[0]
        elif len(main_candidates) > 1:
            # Try to find the most likely main file
            for candidate in main_candidates:
                if any(pattern in candidate.lower() for pattern in ['main', 'run', 'start', 'app']):
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
    
    def __init__(self, install_path: Optional[Union[str, Path]] = None, config_manager: Optional[ConfigManager] = None, server_url: str = f"https://{SERVER_DOMAIN}"):
        # Set base_path from install_path parameter
        if install_path:
            if isinstance(install_path, str):
                self.base_path = Path(install_path)
            else:
                self.base_path = install_path
        else:
            self.base_path = Path.cwd()  # Default to current directory
        
        # Type annotation for the attribute
        self.base_path: Path
            
        # Initialize config manager with proper path if not provided
        if config_manager is None:
            config_path = self.base_path / "portablesource_config.json"
            self.config_manager = ConfigManager(config_path)
            self.config_manager.load_config()
        else:
            self.config_manager = config_manager
        self.analyzer = RequirementsAnalyzer(config_manager=self.config_manager)
        
        # Initialize environment manager
        self.environment_manager = PortableEnvironmentManager(self.base_path, self.config_manager)
        
        # Initialize server client and main file finder
        self.server_client = ServerAPIClient(server_url)
        self.main_file_finder = MainFileFinder(self.server_client)
        

        if not self.server_client.is_server_available():
            logger.warning("PortableSource server not available - using fallback methods only")
        
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
            # Set up installation paths  
            if not install_path:
                logger.error("install_path is required in the new architecture")
                return False
            
            if isinstance(install_path, str):
                install_path = Path(install_path)
            elif not isinstance(install_path, Path):
                logger.error("install_path must be a string or Path object")
                return False
            
            # Set current repository name for unified methods
            is_url = self._is_repository_url(repo_url_or_name)
            if is_url:
                self._current_repo_name = self._extract_repo_name(repo_url_or_name)
            else:
                self._current_repo_name = repo_url_or_name.lower()
            
            # Determine input type and route to appropriate handler
            if is_url:
                # Handle URL installation: extract name -> search server plan -> install dependencies or clone
                return self._handle_url_installation(repo_url_or_name, install_path)
            else:
                # Handle name installation: use standard logic
                return self._handle_name_installation(repo_url_or_name, install_path)
            
        except Exception as e:
            logger.error(f"Error installing repository {repo_url_or_name}: {e}")
            return False
    
    def _handle_url_installation(self, repo_url: str, install_path: Path) -> bool:
        """
        Handle installation from repository URL with automatic fallback to local installation
        
        Args:
            repo_url: Repository URL
            install_path: Installation path
            
        Returns:
            True if installation successful
        """
        try:
            # Extract repository name from URL
            repo_name = self._extract_repo_name(repo_url)
            
            # Try to get installation plan from server
            if repo_name is not None:
                server_plan = self.server_client.get_installation_plan(repo_name)
            
            if server_plan and repo_name is not None:
                # Install only dependencies from server plan without cloning
                success = self._install_from_server_plan_only(server_plan, repo_name, install_path)
                if success:
                    return True
                else:
                    # Server plan failed, fallback to local installation
                    logger.warning(f"Server plan installation failed for {repo_name}, falling back to local installation")
                    return self._install_with_cloning(repo_url, install_path)
            else:
                # No server plan found, fallback to local installation
                return self._install_with_cloning(repo_url, install_path)
                
        except Exception as e:
            logger.error(f"Error handling URL installation for {repo_url}: {e}")
            return False
    
    def _install_from_server_plan_only(self, server_plan: Dict, repo_name: str, install_path: Path) -> bool:
        """
        Install only dependencies from server plan without cloning repository
        
        Args:
            server_plan: Installation plan from server
            repo_name: Repository name
            install_path: Installation path
            
        Returns:
            True if installation successful
        """
        try:
            # Validate server plan data
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan data for {repo_name}")
                return False
            
            # Create venv environment for the repository
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            # Execute server installation plan without local repository
            return self._execute_server_installation_plan(server_plan, None, repo_name)
            
        except Exception as e:
            logger.error(f"Error installing from server plan for {repo_name}: {e}")
            return False
    
    def _handle_name_installation(self, repo_name: str, install_path: Path) -> bool:
        """
        Handle installation by repository name using standard logic
        
        Args:
            repo_name: Repository name
            install_path: Installation path
            
        Returns:
            True if installation successful
        """
        try:
            # Use standard logic - check server first, then fallback repositories
            repo_info = self._get_repository_info(repo_name)
            
            if not repo_info:
                logger.error(f"Repository '{repo_name}' not found")
                return False
            
            repo_path = install_path / repo_name
            
            # Clone or update repository
            if not self._clone_or_update_repository(repo_info, repo_path):
                return False
            
            # Analyze and install dependencies
            if not self._install_dependencies(repo_path):
                return False
            
            # Run special setup if needed
            if repo_info.get("special_setup"):
                repo_info["special_setup"](repo_path)
            
            # Generate startup script
            self._generate_startup_script(repo_path, repo_info)
            
            # Send download statistics to server
            self._send_download_stats(repo_name)

            return True
            
        except Exception as e:
            logger.error(f"Error handling name installation for {repo_name}: {e}")
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
        if repo_name is not None:
            server_info = self.server_client.get_repository_info(repo_name)
        if server_info and repo_name is not None:
            # Handle new server response format
            if 'success' in server_info and 'repository' in server_info:
                repository = server_info['repository']
                return {
                    "url": repository.get("repositoryUrl", repo_url).strip() if repository.get("repositoryUrl") else repo_url,
                    "main_file": repository.get("filePath", "main.py"),
                    "program_args": repository.get("programArgs", ""),
                    "special_setup": self._get_special_setup(repo_name)
                }
            else:
                # Handle old server response format for backward compatibility
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
        if repo_url and repo_name is not None:
            return {
                "url": repo_url,
                "main_file": None,  # Will be determined later
                "special_setup": self._get_special_setup(repo_name)
            }
        
        return None
    
    def _is_repository_url(self, input_str: str) -> bool:
        """Determine if input is a repository URL"""
        return input_str.startswith(("http://", "https://", "git@"))
    
    def _extract_repo_name(self, repo_url: str) -> str:
        """Extract repository name from URL"""
        try:
            parsed = urlparse(repo_url)
            path = parsed.path.strip('/')
            if path.endswith('.git'):
                path = path[:-4]
            return path.split('/')[-1].lower()
        except Exception:
            return ""
    

    

    
    def _install_with_cloning(self, repo_url: str, install_path: Path) -> bool:
        """
        Install repository by cloning and using local requirements.txt
        
        Args:
            repo_url: Repository URL
            install_path: Installation path
            
        Returns:
            True if installation successful
        """
        try:
            # Create basic repo info for cloning
            repo_name = self._extract_repo_name(repo_url)
            if repo_name is not None:
                repo_info = {
                    "url": repo_url,
                    "main_file": None,  # Will be determined later
                    "special_setup": self._get_special_setup(repo_name)
                }
            
                repo_path = install_path / repo_name
            
            # Clone or update repository
            if not self._clone_or_update_repository(repo_info, repo_path):
                return False
            
            # Analyze and install dependencies
            if not self._install_dependencies(repo_path):
                return False
            
            # Run special setup if needed
            if repo_info.get("special_setup"):
                repo_info["special_setup"](repo_path)
            
            # Generate startup script
            self._generate_startup_script(repo_path, repo_info)
            
            # Send download statistics to server
            if repo_name is not None:
                self._send_download_stats(repo_name)

            return True
            
        except Exception as e:
            logger.error(f"Error installing with cloning for {repo_url}: {e}")
            return False
    
    def _validate_server_plan(self, plan: Dict) -> bool:
        """
        Validate server installation plan data
        
        Args:
            plan: Installation plan from server (already extracted installation_plan part)
            
        Returns:
            True if plan is valid
        """
        if not plan or not isinstance(plan, dict):
            return False
        
        # Check for steps field
        if 'steps' not in plan:
            return False
        
        steps = plan['steps']
        if not isinstance(steps, list):
            return False
        
        # Validate each step
        for step in steps:
            if not isinstance(step, dict):
                return False
            if 'type' not in step or 'packages' not in step:
                return False
            if not isinstance(step['packages'], list):
                return False
        
        return True
    
    def _validate_repository_info(self, data: Dict) -> bool:
        """
        Validate repository information data from server
        
        Args:
            data: Repository info data from server
            
        Returns:
            True if data is valid
        """
        if not data or not isinstance(data, dict):
            return False
        
        # Check for basic structure - url is the minimum required field
        if 'url' not in data:
            return False
        
        # Validate URL format if present
        url = data.get('url')
        if url and not isinstance(url, str):
            return False
        
        # Validate optional fields if present
        optional_fields = ['main_file', 'program_args', 'description']
        for field in optional_fields:
            if field in data and not isinstance(data[field], str):
                return False
        
        return True
    

    
    def _validate_dependencies_data(self, data: Dict) -> bool:
        """
        Validate dependencies data from server
        
        Args:
            data: Dependencies data from server
            
        Returns:
            True if data is valid
        """
        if not data or not isinstance(data, dict):
            return False
        
        # Dependencies data should have at least one of these fields
        expected_fields = ['requirements', 'packages', 'dependencies']
        if not any(field in data for field in expected_fields):
            return False
        
        # Validate structure of present fields
        for field in expected_fields:
            if field in data:
                field_data = data[field]
                if not isinstance(field_data, (list, dict, str)):
                    return False
        
        return True
    
    def _install_from_server_plan_only_enhanced(self, server_plan: Dict, repo_name: str, install_path: Path) -> bool:
        """
        Install only dependencies from server plan without cloning repository (enhanced version)
        
        Args:
            server_plan: Installation plan from server
            repo_name: Repository name
            install_path: Installation path
            
        Returns:
            True if installation successful
        """
        try:
            # Validate server plan data
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan data for {repo_name}")
                return False
            
            # Create venv environment for the repository
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            # Execute server installation plan without local repository
            return self._execute_server_installation_plan(server_plan, None, repo_name)
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during server plan installation for {repo_name}: {e}")
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Package installation failed for {repo_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Error installing from server plan for {repo_name}: {e}")
            return False

    def _get_special_setup(self, repo_name: str):
        """Get special setup function for repository"""
        special_setups = {
            "facefusion": self._setup_facefusion
        }
        return special_setups.get(repo_name.lower(), None)
    

    
    def _clone_or_update_repository(self, repo_info: Dict, repo_path: Path) -> bool:
        """Clone or update repository with automatic error fixing"""
        try:
            git_exe = self._get_git_executable()
            
            if repo_path.exists():
                # Update existing repository
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
                        continue
                
                if attempt == max_attempts - 1:
                    logger.error(f"Failed to update repository after {max_attempts} attempts")
                    return False
        
        return False
    
    def _fix_git_issues(self, git_exe: str, repo_path: Path, error_output: str) -> bool:
         """Try to fix common git issues automatically"""
         try:
             # Fix 1: Diverged branches - reset to remote
             if "diverged" in error_output.lower() or "non-fast-forward" in error_output.lower():
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 2: Uncommitted changes - stash them
             if "uncommitted changes" in error_output.lower() or "would be overwritten" in error_output.lower():
                 subprocess.run([git_exe, "stash"], check=True, capture_output=True)
                 return True
             
             # Fix 3: Merge conflicts - abort and reset
             if "merge conflict" in error_output.lower() or "conflict" in error_output.lower():
                 subprocess.run([git_exe, "merge", "--abort"], capture_output=True)  # Don't check=True as it might fail
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 4: Detached HEAD - checkout main/master
             if "detached head" in error_output.lower():
                 try:
                     subprocess.run([git_exe, "checkout", "main"], check=True, capture_output=True)
                 except subprocess.CalledProcessError:
                     subprocess.run([git_exe, "checkout", "master"], check=True, capture_output=True)
                 return True
             
             # Fix 5: Corrupted index - reset index
             if "index" in error_output.lower() and "corrupt" in error_output.lower():
                 subprocess.run([git_exe, "reset", "--mixed"], check=True, capture_output=True)
                 return True
             
             # Fix 6: Remote tracking branch issues
             if "no tracking information" in error_output.lower():
                 subprocess.run([git_exe, "branch", "--set-upstream-to=origin/main"], check=True, capture_output=True)
                 return True
             
             # Fix 7: Exit status 128 - generic git error, try comprehensive fix
             if "128" in error_output or "fatal:" in error_output.lower():
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
                 import time
                 time.sleep(2)  # Wait a bit for locks to release
                 subprocess.run([git_exe, "gc", "--prune=now"], capture_output=True)  # Clean up
                 return True
             
             # Fix 9: Network/remote issues - retry with different approach
             if "network" in error_output.lower() or "remote" in error_output.lower() or "connection" in error_output.lower():
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
        """Get git executable path from portable environment"""
        git_path = self.environment_manager.get_git_executable()
        if git_path and git_path.exists():
            return str(git_path)
        
        # Fallback to system git
        return "git"
    

    
    def _get_python_executable(self) -> str:
        """Get Python executable path from portable environment"""
        python_path = self.environment_manager.get_python_executable()
        if python_path and python_path.exists():
            return str(python_path)
        
        # Fallback to system python
        return "python"
    
    def _activate_portable_environment(self) -> bool:
        """Activate portable environment to make packages visible"""
        try:
            # Setup environment variables using portable environment manager
            env_vars = self.environment_manager.setup_environment_for_subprocess()
            
            # Update current process environment
            import os
            for key, value in env_vars.items():
                os.environ[key] = value
            
            logger.info("Portable environment variables set successfully")
            return True
                
        except Exception as e:
            logger.error(f"Error activating portable environment: {e}")
            return False
    
    def _get_pip_executable(self, repo_name: str) -> List[str]:
        """Get pip executable command from repository's environment"""
        if self.config_manager.config and self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            venv_path = install_path / "envs" / repo_name
            # For copied portable Python, python.exe is in the root of the copied directory
            python_path = venv_path / "python.exe" if os.name == 'nt' else venv_path / "bin" / "python"
            if python_path.exists():
                return [str(python_path), "-m", "pip"]
        
        # Fallback to system python with pip
        return ["python", "-m", "pip"]
    
    def _get_uv_executable(self, repo_name: str) -> List[str]:
        """Get uv executable command from repository's environment"""
        if self.config_manager.config and self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            venv_path = install_path / "envs" / repo_name
            # For copied portable Python, python.exe is in the root of the copied directory
            python_path = venv_path / "python.exe" if os.name == 'nt' else venv_path / "bin" / "python"
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
                    return True
            except Exception:
                pass  # UV not available, continue with installation
            
            pip_exe = self._get_pip_executable(repo_name)
            self._run_pip_with_progress(pip_exe + ["install", "uv"], "Installing uv")
            
            # Verify installation
            try:
                result = subprocess.run(uv_cmd + ["--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
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
    

    
    def _install_dependencies(self, repo_path: Path) -> bool:
        """Install dependencies in venv with new architecture - try server first, then local requirements"""
        try:
            repo_name = repo_path.name.lower()
            
            # Create venv environment for the repository
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            # Try to get installation plan from server first
            server_plan = self.server_client.get_installation_plan(repo_name)
            if server_plan:
                if self._execute_server_installation_plan(server_plan, repo_path, repo_name):
                    return True
                else:
                    logger.warning(f"Server installation failed for {repo_name}, falling back to local requirements")
            
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
        """Create environment for repository by copying portable Python installation"""
        try:
            if not self.config_manager.config or not self.config_manager.config.install_path:
                logger.error("Install path not configured")
                return False
            
            install_path = Path(self.config_manager.config.install_path)
            envs_path = install_path / "envs"
            venv_path = envs_path / repo_name
            ps_env_python_path = install_path / "ps_env" / "python"
            
            # Check if portable Python exists
            if not ps_env_python_path.exists():
                logger.error(f"Portable Python not found at: {ps_env_python_path}")
                return False
            
            # Create envs directory if it doesn't exist
            envs_path.mkdir(parents=True, exist_ok=True)
            
            # Remove existing environment if exists
            if venv_path.exists():
                import shutil
                shutil.rmtree(venv_path)
            
            # Copy portable Python installation to create isolated environment
            import shutil
            logger.info(f"Creating environment by copying portable Python: {ps_env_python_path} -> {venv_path}")
            shutil.copytree(ps_env_python_path, venv_path)
            
            # Verify that Python executable exists in the new environment
            python_exe = venv_path / "python.exe" if os.name == 'nt' else venv_path / "bin" / "python"
            if python_exe.exists():
                logger.info(f"[OK] Environment created successfully for {repo_name}")
                return True
            else:
                logger.error(f"Python executable not found in copied environment: {python_exe}")
                return False
                
        except Exception as e:
            logger.error(f"Error creating environment: {e}")
            return False
    
    def _install_packages_in_venv(self, repo_name: str, requirements_path: Path) -> bool:
        """Install packages in venv environment using uv for regular packages and pip for torch"""
        try:
            # Install uv in venv first
            if not self._install_uv_in_venv(repo_name):
                logger.warning("Failed to install uv, falling back to pip for all packages")
                return self._install_packages_with_pip_only(repo_name, requirements_path)
            
            # Activate portable environment to make CUDA packages visible
            if not self._activate_portable_environment():
                logger.warning("Failed to activate portable environment, CUDA packages may not be visible")
            
            # Analyze requirements to separate torch and regular packages
            packages = self.analyzer.analyze_requirements(requirements_path)
            plan = self.analyzer.create_installation_plan(packages, None)
            
            pip_exe = self._get_pip_executable(repo_name)
            uv_cmd = self._get_uv_executable(repo_name)
            
            # Install torch packages with pip (they need special index URLs)
            if plan.torch_packages:
                torch_cmd = pip_exe + ["install"]
                
                for package in plan.torch_packages:
                    torch_cmd.append(str(package))
                
                if plan.torch_index_url:
                    torch_cmd.extend(["--index-url", plan.torch_index_url])
                
                self._run_pip_with_progress(torch_cmd, "Installing PyTorch packages")
            
            # Install ONNX packages with pip
            if plan.onnx_packages:
                onnx_package_name = plan.onnx_package_name or "onnxruntime"
                
                # Find the onnxruntime package to get version if specified
                onnxruntime_package = next((p for p in plan.onnx_packages if p.name == 'onnxruntime'), None)
                if onnxruntime_package and onnxruntime_package.version:
                    package_str = f"{onnx_package_name}=={onnxruntime_package.version}"
                else:
                    package_str = onnx_package_name

                self._run_pip_with_progress(pip_exe + ["install", package_str], f"Installing ONNX package: {package_str}")
            
            # Install InsightFace packages with special handling
            if plan.insightface_packages:
                for package in plan.insightface_packages:
                    self._handle_insightface_package(package)
            
            # Install Triton packages with special handling
            if plan.triton_packages:
                logger.info("Handling Triton packages...")
                for package in plan.triton_packages:
                    self._handle_triton_package(package)

            # Install regular packages with uv
            if plan.regular_packages:
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
            
            return True
                
        except Exception as e:
            logger.error(f"Error installing packages: {e}")
            return False
    
    def _install_packages_with_pip_only(self, repo_name: str, requirements_path: Path) -> bool:
        """Fallback method to install all packages with pip only"""
        try:
            # Use unified method with requirements file
            return self._install_package_with_progress(
                ["-r", str(requirements_path)], 
                f"Installing packages for {repo_name}", 
                repo_name
            )
                
        except Exception as e:
            logger.error(f"Error installing packages with pip: {e}")
            return False


    
    def _execute_server_installation_plan(self, server_plan: Dict, repo_path: Optional[Path], repo_name: str) -> bool:
        """Execute installation plan from server with enhanced error handling"""
        try:
            # Validate server plan before execution
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan structure for {repo_name}")
                return False
            
            # Install uv in venv first
            if not self._install_uv_in_venv(repo_name):
                logger.warning("Failed to install uv, some packages may use pip fallback")
            
            # Activate portable environment to make CUDA packages visible
            if not self._activate_portable_environment():
                logger.warning("Failed to activate portable environment, CUDA packages may not be visible")
            
            pip_exe = self._get_pip_executable(repo_name)
            
            # Execute installation steps in order
            steps = server_plan.get('steps', [])
            if not steps:
                logger.warning(f"No installation steps found in server plan for {repo_name}")
                return True  # Empty plan is considered successful
            
            for step_index, step in enumerate(steps):
                # Validate step structure
                if not isinstance(step, dict):
                    logger.error(f"Invalid step structure at index {step_index} for {repo_name}")
                    return False
                
                step_type = step.get('type', '')
                packages = step.get('packages', [])
                install_flags = step.get('install_flags', [])
                
                # Validate required fields
                if not step_type:
                    logger.error(f"Missing step type at index {step_index} for {repo_name}")
                    return False
                
                if not isinstance(packages, list):
                    logger.error(f"Invalid packages format at step {step_index} for {repo_name}")
                    return False
                
                if not packages:
                    logger.debug(f"Skipping empty package list at step {step_index} for {repo_name}")
                    continue
                
                # Special handling for regular steps - split packages by type
                if step_type == 'regular':
                    # Separate packages into special and regular categories
                    special_packages = {'onnxruntime': [], 'torch': [], 'insightface': [], 'triton': []}
                    regular_packages = []
                    
                    for package in packages:
                        if isinstance(package, str) and package.strip():
                            package_str = package.strip().lower()
                            if package_str.startswith('onnxruntime'):
                                special_packages['onnxruntime'].append(package)
                            elif package_str.startswith('torch') or package_str.startswith('torchvision') or package_str.startswith('torchaudio'):
                                special_packages['torch'].append(package)
                            elif package_str.startswith('insightface'):
                                special_packages['insightface'].append(package)
                            elif package_str.startswith('triton'):
                                special_packages['triton'].append(package)
                            else:
                                regular_packages.append(package)
                        elif isinstance(package, dict):
                            pkg_name = package.get('package_name', '').lower()
                            if pkg_name.startswith('onnxruntime'):
                                special_packages['onnxruntime'].append(package)
                            elif pkg_name.startswith('torch') or pkg_name.startswith('torchvision') or pkg_name.startswith('torchaudio'):
                                special_packages['torch'].append(package)
                            elif pkg_name.startswith('insightface'):
                                special_packages['insightface'].append(package)
                            elif pkg_name.startswith('triton'):
                                special_packages['triton'].append(package)
                            else:
                                regular_packages.append(package)
                        else:
                            regular_packages.append(package)
                    
                    # Process special packages first with their specific logic
                    for special_type, special_pkgs in special_packages.items():
                        if special_pkgs:
                            # Create a synthetic step for this special package type
                            special_step = {
                                'type': special_type,
                                'packages': special_pkgs,
                                'install_flags': install_flags,
                                'description': f'Install {special_type} packages'
                            }
                            # Recursively process this special step
                            if not self._process_installation_step(special_step, step_index, server_plan, repo_name, pip_exe):
                                return False
                    
                    # Process remaining regular packages if any
                    if regular_packages:
                        regular_step = {
                            'type': 'regular_only',
                            'packages': regular_packages,
                            'install_flags': install_flags,
                            'description': 'Install regular packages'
                        }
                        if not self._process_installation_step(regular_step, step_index, server_plan, repo_name, pip_exe):
                            return False
                    
                    continue  # Skip the normal processing for this step
                
                # Normal processing for non-regular steps
                if not self._process_installation_step(step, step_index, server_plan, repo_name, pip_exe):
                    return False
            
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Package installation failed for {repo_name}: {e}")
            return False
        except KeyError as e:
            logger.error(f"Missing required field in server plan for {repo_name}: {e}")
            return False
        except ValueError as e:
            logger.error(f"Invalid data in server plan for {repo_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error executing server installation plan for {repo_name}: {e}")
            return False
    
    def _process_installation_step(self, step: Dict, step_index: int, server_plan: Dict, repo_name: str, pip_exe: List[str]) -> bool:
        """Process a single installation step"""
        try:
            step_type = step.get('type', '')
            packages = step.get('packages', [])
            install_flags = step.get('install_flags', [])
            
            if not packages:
                logger.debug(f"Skipping empty package list at step {step_index} for {repo_name}")
                return True
            
            # Determine installation tool and build command
            install_cmd, use_uv, use_uv_first = self._prepare_install_command(step_type, repo_name, pip_exe)
            
            # Process packages and add to command
            self._add_packages_to_command(install_cmd, packages, step_type, server_plan)
            
            # Add additional flags and URLs
            self._add_install_flags_and_urls(install_cmd, install_flags, server_plan)
            
            # Execute installation
            return self._execute_install_command(install_cmd, step, step_type, step_index, use_uv, use_uv_first, pip_exe)
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Package installation failed for {repo_name}: {e}")
            return False
        except KeyError as e:
            logger.error(f"Missing required field in server plan for {repo_name}: {e}")
            return False
        except ValueError as e:
            logger.error(f"Invalid data in server plan for {repo_name}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error executing server installation plan for {repo_name}: {e}")
            return False
    
    def _prepare_install_command(self, step_type: str, repo_name: str, pip_exe: List[str]) -> tuple:
        """Prepare the base installation command based on step type"""
        if step_type in ['regular_only', 'onnxruntime', 'insightface', 'triton']:
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
                uv_cmd = self._get_uv_executable(repo_name)
                install_cmd = uv_cmd + ["pip", "install"]
                return install_cmd, True, True
            else:
                logger.warning(f"UV not available, using pip for {step_type} packages")
                install_cmd = pip_exe + ["install"]
                return install_cmd, False, False
        else:
            # Use pip for torch packages (may need specific index URLs)
            install_cmd = pip_exe + ["install"]
            return install_cmd, False, False
    
    def _add_packages_to_command(self, install_cmd: list, packages: list, step_type: str, server_plan: Dict):
        """Add packages to the installation command with special handling"""
        for package_index, package in enumerate(packages):
            if isinstance(package, str) and package.strip():
                package_str = self._process_string_package(package.strip(), step_type)
                install_cmd.append(package_str)
            elif isinstance(package, dict):
                self._process_dict_package(install_cmd, package, step_type, server_plan, package_index)
    
    def _process_string_package(self, package_str: str, step_type: str) -> str:
        """Process string package with GPU auto-detection for onnxruntime"""
        if step_type == 'onnxruntime' and package_str.startswith('onnxruntime'):
            return self._apply_gpu_detection_to_onnx(package_str)
        elif step_type == 'insightface' and package_str.startswith('insightface') and os.name == "nt":
            return "https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl"
        return package_str
    
    def _process_dict_package(self, install_cmd: list, package: Dict, step_type: str, server_plan: Dict, package_index: int):
        """Process dictionary package with special handling"""
        pkg_name = package.get('package_name', '')
        pkg_version = package.get('version', '')
        index_url = package.get('index_url', '')
        
        if not pkg_name:
            logger.warning(f"Package {package_index}: Empty pkg_name, skipping package")
            if index_url and '--index-url' not in install_cmd:
                install_cmd.extend(['--index-url', index_url])
            return
        
        # Apply special handling based on step type
        pkg_name = self._apply_special_package_handling(pkg_name, step_type, server_plan)
        
        # Handle special case for InsightFace on Windows
        if step_type == 'insightface' and pkg_name == 'insightface' and os.name == "nt":
            install_cmd.append("https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl")
            return
        
        # Build package string with version
        pkg_str = self._build_package_string(pkg_name, pkg_version)
        install_cmd.append(pkg_str)
    
    def _apply_gpu_detection_to_onnx(self, package_str: str) -> str:
        """Apply GPU auto-detection for onnxruntime packages"""
        gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
        
        if not (gpu_config_obj and gpu_config_obj.name):
            return package_str
        
        gpu_name_upper = gpu_config_obj.name.upper()
        
        # Parse package string to extract version if present
        if '==' in package_str:
            _, version = package_str.split('==', 1)
            return self._get_gpu_specific_onnx_package(gpu_name_upper, f"=={version}")
        elif '>=' in package_str:
            _, version = package_str.split('>=', 1)
            return self._get_gpu_specific_onnx_package(gpu_name_upper, f">={version}")
        else:
            return self._get_gpu_specific_onnx_package(gpu_name_upper, "")
    
    def _get_gpu_specific_onnx_package(self, gpu_name_upper: str, version_suffix: str) -> str:
        """Get GPU-specific onnxruntime package name"""
        if gpu_name_upper.startswith('NVIDIA'):
            return f"onnxruntime-gpu{version_suffix}"
        elif gpu_name_upper.startswith('AMD') and os.name == "nt":
            return f"onnxruntime-directml{version_suffix}"
        elif gpu_name_upper.startswith('AMD') and os.name == "posix":
            return f"onnxruntime-rocm{version_suffix}"
        elif gpu_name_upper.startswith('INTEL'):
            return f"onnxruntime-directml{version_suffix}"
        else:
            return f"onnxruntime{version_suffix}"
    
    def _apply_special_package_handling(self, pkg_name: str, step_type: str, server_plan: Dict) -> str:
        """Apply special handling for specific package types"""
        if step_type == 'onnxruntime' and pkg_name == "onnxruntime":
            gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
            
            if gpu_config_obj and gpu_config_obj.name:
                gpu_name_upper = gpu_config_obj.name.upper()
                if gpu_name_upper.startswith('NVIDIA'):
                    return "onnxruntime-gpu"
                elif gpu_name_upper.startswith('AMD') and os.name == "nt":
                    return "onnxruntime-directml"
                elif gpu_name_upper.startswith('AMD') and os.name == "posix":
                    return "onnxruntime-rocm"
                elif gpu_name_upper.startswith('INTEL'):
                    return "onnxruntime-directml"
            
            # Check for custom onnx package name in server plan
            onnx_package_name = server_plan.get('onnx_package_name')
            if onnx_package_name:
                return onnx_package_name
        
        return pkg_name
    
    def _build_package_string(self, pkg_name: str, pkg_version: str) -> str:
        """Build package string with version"""
        if pkg_version:
            if pkg_version.startswith('>=') or pkg_version.startswith('=='):
                return f"{pkg_name}{pkg_version}"
            else:
                return f"{pkg_name}=={pkg_version}"
        else:
            return pkg_name
    
    def _add_install_flags_and_urls(self, install_cmd: list, install_flags: list, server_plan: Dict):
        """Add installation flags and index URLs to command"""
        # Add torch index URL if specified
        if server_plan.get('torch_index_url') and '--index-url' not in install_cmd:
            install_cmd.extend(['--index-url', server_plan['torch_index_url']])
        
        # Add install flags
        if install_flags:
            install_cmd.extend(install_flags)
    
    def _execute_install_command(self, install_cmd: list, step: Dict, step_type: str, step_index: int, use_uv: bool, use_uv_first: bool, pip_exe: List[str]) -> bool:
        """Execute the installation command with appropriate tool"""
        description = step.get('description', step_type)
        if description.startswith('Install '):
            step_description = description.replace('Install ', 'Installing ', 1)
        else:
            step_description = f"Installing {description}"
        
        if step_type in ['regular', 'onnxruntime', 'insightface', 'triton'] and use_uv_first:
            return self._try_uv_with_pip_fallback(install_cmd, step_description, step_index, pip_exe)
        elif use_uv:
            self._run_uv_with_progress(install_cmd, step_description)
        else:
            self._run_pip_with_progress(install_cmd, step_description)
        
        return True
    
    def _try_uv_with_pip_fallback(self, install_cmd: list, step_description: str, step_index: int, pip_exe: List[str]) -> bool:
        """Try uv first, then fallback to pip if it fails"""
        try:
            self._run_uv_with_progress(install_cmd, step_description)
        except subprocess.CalledProcessError as e:
            logger.warning(f"UV installation failed, trying pip fallback: {e}")
            # Extract packages and flags from uv command
            packages_and_flags = install_cmd[3:]  # Skip uv_exe, "pip", "install"
            
            if packages_and_flags:
                pip_install_cmd = pip_exe + ["install"] + packages_and_flags
                self._run_pip_with_progress(pip_install_cmd, f"{step_description} (pip fallback)")
            else:
                logger.warning(f"No packages to install in fallback for step {step_index}")
        
        return True
    
    def _execute_installation_plan(self, plan: InstallationPlan, original_requirements: Path, repo_name: str) -> bool:
        """Execute the installation plan using base Python"""
        try:
            # Install PyTorch packages with specific index
            if plan.torch_packages:
                torch_packages = [str(package) for package in plan.torch_packages]
                repo_name = getattr(self, '_current_repo_name', 'default')
                
                if plan.torch_index_url is not None:
                    self._install_package_with_progress(
                        torch_packages, 
                        "Installing PyTorch packages", 
                        repo_name, 
                        index_url=plan.torch_index_url
                    )
            
            # Install ONNX Runtime packages with GPU auto-detection for fallback
            if plan.onnx_packages:
                gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
                
                for package in plan.onnx_packages:
                    # Auto-detect GPU version for onnxruntime when falling back to local requirements
                    package_str = str(package)
                    if package.name == "onnxruntime" and gpu_config_obj and gpu_config_obj.name:
                        gpu_name_upper = gpu_config_obj.name.upper()
                        if gpu_name_upper.startswith('NVIDIA'):
                            # Replace onnxruntime with onnxruntime-gpu for NVIDIA GPUs
                            if package.version:
                                package_str = f"onnxruntime-gpu=={package.version}"
                            else:
                                package_str = "onnxruntime-gpu"
                        elif gpu_name_upper.startswith('AMD') and os.name == "nt":
                            # AMD GPU on Windows - use DirectML
                            if package.version:
                                package_str = f"onnxruntime-directml=={package.version}"
                            else:
                                package_str = "onnxruntime-directml"
                        elif gpu_name_upper.startswith('AMD') and os.name == "posix":
                            # AMD GPU on Linux - use ROCm (if available)
                            if package.version:
                                package_str = f"onnxruntime-rocm=={package.version}"
                            else:
                                package_str = "onnxruntime-rocm"
                        elif gpu_name_upper.startswith('INTEL'):
                            # Intel GPU - use DirectML
                            if package.version:
                                package_str = f"onnxruntime-directml=={package.version}"
                            else:
                                package_str = "onnxruntime-directml"

                    repo_name = getattr(self, '_current_repo_name', 'default')
                    self._install_package_with_progress([package_str], f"Installing ONNX package: {package_str}", repo_name)
            
            # Install InsightFace packages (if any)
            if plan.insightface_packages:
                for package in plan.insightface_packages:
                    self._handle_insightface_package(package)
            
            # Install Triton packages (if any)
            if plan.triton_packages:
                for package in plan.triton_packages:
                    self._handle_triton_package(package)
            
            # Install regular packages using unified method
            if plan.regular_packages:
                modified_requirements = original_requirements.parent / "requirements_modified.txt"
                with open(modified_requirements, 'w', encoding='utf-8') as f:
                    for package in plan.regular_packages:
                        f.write(package.original_line + '\n')
                
                if modified_requirements.stat().st_size > 0:
                    repo_name = getattr(self, '_current_repo_name', 'default')
                    self._install_package_with_progress(["-r", str(modified_requirements)], "Installing regular packages", repo_name)
                
                try:
                    modified_requirements.unlink()
                except Exception:
                    pass
            
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Package installation failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Error executing installation plan: {e}")
            return False
    
    def _setup_facefusion(self, repo_path: Path):
        """Special setup for FaceFusion"""
        # Create models directory
        models_dir = repo_path / "models"
        models_dir.mkdir(exist_ok=True)
    
    def _generate_startup_script(self, repo_path: Path, repo_info: Dict):
        """Generate startup script using copied Python environment with manual CUDA/library path setup.
        
        This version uses portable environment system with copied Python installations
        instead of traditional venv, providing better isolation and compatibility.
        """
        try:
            repo_name = repo_path.name.lower()
            
            # Determine main file using our intelligent finder
            main_file = repo_info.get("main_file")
            if not main_file:
                main_file = self.main_file_finder.find_main_file(repo_name, repo_path, repo_info["url"])
            
            if not main_file:
                logger.error("[ERROR] Could not determine main file for repository!")
                logger.error("📝 Please manually specify the main file to run:")
                logger.error(f"   Available Python files in {repo_path}:")
                for py_file in repo_path.glob("*.py"):
                    logger.error(f"   - {py_file.name}")
                return False
            
            # Create startup script
            bat_file = repo_path / f"start_{repo_name}.bat"
            
            if not self.config_manager.config or not self.config_manager.config.install_path:
                logger.error("Install path not configured")
                return False
            
            install_path = Path(self.config_manager.config.install_path)
            
            # Get CUDA paths from configuration
            cuda_paths = None
            cuda_paths_section = "REM CUDA paths not configured"
            if (self.config_manager.config and 
                self.config_manager.config.gpu_config and 
                self.config_manager.config.gpu_config.cuda_paths):
                cuda_paths = self.config_manager.config.gpu_config.cuda_paths
                cuda_paths_section = f"""set PATH={cuda_paths.cuda_bin};%PATH%
set PATH={cuda_paths.cuda_lib};%PATH%
set PATH={cuda_paths.cuda_lib_64};%PATH%
set PATH={cuda_paths.cuda_nvml_bin};%PATH%
set PATH={cuda_paths.cuda_nvml_lib};%PATH%
set PATH={cuda_paths.cuda_nvvm_bin};%PATH%
set PATH={cuda_paths.cuda_nvvm_lib};%PATH%
echo All CUDA paths added to environment"""
            
            # Path to the copied Python environment
            env_path = install_path / "envs" / repo_name
            python_exe = env_path / "python.exe"
            ffmpeg_path = install_path / "ffmpeg"
            
            # Get program args from repo info
            program_args = repo_info.get('program_args', '')
            
            # Setup tmp directory path
            tmp_path = install_path / "tmp"
            
            bat_content = f"""@echo off
echo Launch {repo_name}...

REM Setup temporary directory
set USERPROFILE={tmp_path}
set TEMP={tmp_path}
set TMP={tmp_path}

REM Security and compatibility settings
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set PYTHONDONTWRITEBYTECODE=1

REM === PORTABLE ENVIRONMENT SETUP ===
REM Using portable environment system with copied Python installations
REM instead of traditional venv for better isolation and compatibility.

REM === ADD CUDA PATHS ===
REM Add all CUDA paths if available
{cuda_paths_section}

REM === ADD COPIED PYTHON ENVIRONMENT PATHS ===
REM Add the copied Python environment to PATH
set PATH={env_path};%PATH%
set PATH={env_path}\\Scripts;%PATH%
echo Python environment and ffmpeg paths added to PATH
set PATH={ffmpeg_path};%PATH%

REM Change to repository directory and run
cd /d "{repo_path}"
"{python_exe}" -c "import sys; sys.path.insert(0, r'{repo_path}'); exec(open(r'{repo_path}\\{main_file}').read())" {program_args}
set EXIT_CODE=%ERRORLEVEL%

REM Check result
if %EXIT_CODE% neq 0 (
    echo.
    echo Program finished with error (code: %EXIT_CODE%)
    echo Check logs above for more information about the error.
    echo.
) else (
    echo.
    echo Program finished successfully
    echo.
)

pause
"""
            
            # Write batch file
            with open(bat_file, 'w', encoding='utf-8') as f:
                f.write(bat_content)
            
            return True
                 
        except Exception as e:
            logger.error(f"Error generating startup script: {e}")
            return False

    def _send_download_stats(self, repo_name: str):
        """Send download statistics to server"""
        try:
            if not self.server_client.is_server_available():
                return  # Server not available, skip stats
            
            # Send download record to server using the server client
            url = f"{self.server_client.server_url}/api/repositories/{repo_name.lower()}/download"
            response = self.server_client.session.post(
                url,
                json={
                    'repository_name': repo_name.lower(),
                    'success': True,
                    'timestamp': None  # Server will set timestamp
                },
                timeout=self.server_client.timeout
            )
            
            if response.status_code == 200:
                logger.debug(f"Successfully sent download statistics for {repo_name}")
            elif response.status_code == 404:
                logger.debug(f"Repository {repo_name} not found on server for stats")
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
            else:
                # Fallback to regular subprocess without progress
                subprocess.run(pip_cmd, check=True, capture_output=True, text=True)
                
        except subprocess.CalledProcessError as e:
            logger.error(f"{description} failed: {e}")
            raise
        except Exception as e:
             logger.error(f"Error during {description}: {e}")
             raise
    
    def _run_uv_with_progress(self, uv_cmd: List[str], description: str):
        """Run uv command with progress bar if tqdm is available"""
        TQDM_AVAILABLE = True
        try:
            if TQDM_AVAILABLE:
                # Run with progress bar
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
            else:
                # Fallback to regular subprocess without progress
                subprocess.run(uv_cmd, check=True, capture_output=True, text=True)
                
        except subprocess.CalledProcessError as e:
            logger.error(f"{description} failed: {e}")
            raise
        except Exception as e:
             logger.error(f"Error during {description}: {e}")
             raise
     
    def _run_git_with_progress(self, git_cmd: List[str], description: str):
         """Run git command with progress bar if tqdm is available"""
         TQDM_AVAILABLE = True
         try:
             if TQDM_AVAILABLE:
                 # Run with progress bar
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
             else:
                 # Fallback to regular subprocess without progress
                 result = subprocess.run(git_cmd, check=True, capture_output=True, text=True)
                 
         except subprocess.CalledProcessError as e:
             logger.error(f"{description} failed: {e}")
             raise
         except Exception as e:
             logger.error(f"Error during {description}: {e}")
             raise
    
    def _execute_server_plan_with_pip_only(self, server_plan: Dict, repo_name: str) -> bool:
        """
        Execute server installation plan using pip only (fallback when uv fails)
        
        Args:
            server_plan: Installation plan from server
            repo_name: Repository name
            
        Returns:
            True if installation successful
        """
        try:
            steps = server_plan.get('steps', [])
            pip_exe = self._get_pip_executable(repo_name)
            
            for step in steps:
                step_type = step.get('type', 'regular')
                packages = step.get('packages', [])
                
                if not packages:
                    continue
                
                if step_type == 'torch':
                    # Install PyTorch packages with special index URL
                    torch_index_url = server_plan.get('torch_index_url') or self._get_default_torch_index_url()
                    
                    self._install_package_with_progress(
                        packages, 
                        f"Installing PyTorch packages: {', '.join(packages)}", 
                        repo_name, 
                        index_url=torch_index_url
                    )
                
                elif step_type == 'onnxruntime':
                    # Install ONNX Runtime with appropriate package name
                    onnx_package_name = server_plan.get('onnx_package_name') or self._get_default_onnx_package()
                    
                    # Replace generic onnxruntime with specific package
                    onnx_packages = []
                    for package in packages:
                        if isinstance(package, str) and package.startswith('onnxruntime'):
                            # Extract version if present
                            if '==' in package:
                                version = package.split('==')[1]
                                onnx_packages.append(f"{onnx_package_name}=={version}")
                            else:
                                onnx_packages.append(onnx_package_name)
                        else:
                            onnx_packages.append(package if isinstance(package, str) else package.get('package_name', str(package)))
                    
                    self._install_package_with_progress(onnx_packages, f"Installing ONNX packages: {', '.join(onnx_packages)}", repo_name)
                
                elif step_type == 'triton':
                    # Install Triton packages
                    triton_packages = []
                    for package in packages:
                        package_name = package if isinstance(package, str) else package.get('package_name', str(package))
                        triton_packages.append(package_name)
                    
                    if triton_packages:
                        self._install_package_with_progress(triton_packages, f"Installing Triton packages: {', '.join(triton_packages)}", repo_name)
                
                else:  # regular and insightface packages
                    # Install all other packages
                    regular_packages = []
                    for package in packages:
                        package_name = package if isinstance(package, str) else package.get('package_name', str(package))
                        regular_packages.append(package_name)
                    
                    if regular_packages:
                        self._install_package_with_progress(regular_packages, f"Installing packages: {', '.join(regular_packages)}", repo_name)
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing server plan with pip only for {repo_name}: {e}")
            return False
    
    def _get_default_torch_index_url(self) -> str:
        """Get default PyTorch index URL based on GPU configuration"""
        try:
            gpu_config = self.config_manager.config.gpu_config if self.config_manager.config else None
            return self.analyzer._get_torch_index_url_from_config(gpu_config)
        except Exception as e:
            logger.warning(f"Error getting default torch index URL: {e}")
            return "https://download.pytorch.org/whl/cpu"
    
    def _get_default_onnx_package(self) -> str:
        """Get default ONNX Runtime package name based on GPU configuration"""
        try:
            gpu_config = self.config_manager.config.gpu_config if self.config_manager.config else None
            return self.analyzer._get_onnx_package_name_from_config(gpu_config)
        except Exception as e:
            logger.warning(f"Error getting default ONNX package: {e}")
            return "onnxruntime"
    
    def _handle_insightface_package_from_name(self, package_name: str):
        """Handle InsightFace package installation from package name"""
        try:
            # Parse version if present
            version = None
            if '==' in package_name:
                name, version = package_name.split('==', 1)
            else:
                name = package_name
            
            # Create a PackageInfo object for InsightFace handling
            temp_package = PackageInfo(
                name=name,
                version=version,
                package_type=PackageType.INSIGHTFACE,
                original_line=package_name
            )
            
            # Use existing InsightFace handling logic
            self._handle_insightface_package(temp_package)
            
        except Exception as e:
            logger.error(f"Error handling InsightFace package {package_name}: {e}")
            # Fallback to unified installation method
            repo_name = getattr(self, '_current_repo_name', 'default')
            self._install_package_with_progress([package_name], f"Installing InsightFace package: {package_name}", repo_name)
    
    def _handle_insightface_package(self, package: PackageInfo):
        """Handle InsightFace package installation with special requirements"""
        try:
            # InsightFace often requires specific versions and dependencies
            package_str = str(package)
            
            # Use unified installation method
            repo_name = getattr(self, '_current_repo_name', 'default')
            success = self._install_package_with_progress([package_str], f"Installing InsightFace package: {package_str}", repo_name)
            
            if not success:
                raise Exception(f"Failed to install InsightFace package: {package_str}")
            
        except Exception as e:
            logger.error(f"Error installing InsightFace package {package}: {e}")
            raise
    
    def _install_package_with_progress(self, packages: list, description: str, repo_name: str, index_url: Optional[str] = None, install_flags: Optional[list] = None) -> bool:
        """Unified package installation method that tries uv first, then falls back to pip
        
        Args:
            packages: List of package names/specs to install
            description: Description for progress display
            repo_name: Repository name for venv context
            index_url: Optional index URL for package installation
            install_flags: Optional additional flags for installation
            
        Returns:
            True if installation successful, False otherwise
        """
        try:
            if not packages:
                logger.warning(f"No packages provided for installation: {description}")
                return True
            
            # Check if uv is available in the virtual environment
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
                # Use uv for installation
                uv_cmd = self._get_uv_executable(repo_name)
                install_cmd = uv_cmd + ["pip", "install"] + packages
                
                if index_url:
                    install_cmd.extend(["--index-url", index_url])
                
                if install_flags:
                    install_cmd.extend(install_flags)
                
                try:
                    self._run_uv_with_progress(install_cmd, description)
                    return True
                except subprocess.CalledProcessError as e:
                    logger.warning(f"UV installation failed, trying pip fallback: {e}")
                    # Fall through to pip fallback
            
            # Use pip as fallback or primary method
            pip_exe = self._get_pip_executable(repo_name)
            install_cmd = pip_exe + ["install"] + packages
            
            if index_url:
                install_cmd.extend(["--index-url", index_url])
            
            if install_flags:
                install_cmd.extend(install_flags)
            
            self._run_pip_with_progress(install_cmd, description)
            return True
            
        except Exception as e:
            logger.error(f"Error during package installation ({description}): {e}")
            return False
    
    def _install_package(self, packages: list, repo_name: str, index_url: Optional[str] = None, install_flags: Optional[list] = None) -> bool:
        """Unified package installation method without progress display
        
        Args:
            packages: List of package names/specs to install
            repo_name: Repository name for venv context
            index_url: Optional index URL for package installation
            install_flags: Optional additional flags for installation
            
        Returns:
            True if installation successful, False otherwise
        """
        try:
            if not packages:
                logger.warning("No packages provided for installation")
                return True
            
            # Check if uv is available in the virtual environment
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
                # Use uv for installation
                uv_cmd = self._get_uv_executable(repo_name)
                install_cmd = uv_cmd + ["pip", "install"] + packages
                
                if index_url:
                    install_cmd.extend(["--index-url", index_url])
                
                if install_flags:
                    install_cmd.extend(install_flags)
                
                try:
                    subprocess.run(install_cmd, check=True, capture_output=True, text=True)
                    return True
                except subprocess.CalledProcessError as e:
                    logger.warning(f"UV installation failed, trying pip fallback: {e}")
                    # Fall through to pip fallback
            
            # Use pip as fallback or primary method
            pip_exe = self._get_pip_executable(repo_name)
            install_cmd = pip_exe + ["install"] + packages
            
            if index_url:
                install_cmd.extend(["--index-url", index_url])
            
            if install_flags:
                install_cmd.extend(install_flags)
            
            subprocess.run(install_cmd, check=True, capture_output=True, text=True)
            return True
            
        except Exception as e:
            logger.error(f"Error during package installation: {e}")
            return False
    
    def _handle_triton_package(self, package: PackageInfo):
        """Handle Triton package installation with special requirements"""
        try:
            # Triton has specific installation requirements
            package_str = str(package)
            
            # Use unified installation method
            repo_name = getattr(self, '_current_repo_name', 'default')
            success = self._install_package_with_progress([package_str], f"Installing Triton package: {package_str}", repo_name)
            
            if not success:
                raise Exception(f"Failed to install Triton package: {package_str}")
            
        except Exception as e:
            logger.error(f"Error installing Triton package {package}: {e}")
            raise


    
    def update_repository(self, repo_name: str) -> bool:
        """Update an existing repository.
        
        Args:
            repo_name: Name of the repository to update
            
        Returns:
            True if update successful, False otherwise
        """
        try:
            logger.info(f"Updating repository: {repo_name}")
            
            repos_path = self.base_path / "repos"
            repo_path = repos_path / repo_name
            
            if not repo_path.exists():
                logger.error(f"Repository {repo_name} not found at {repo_path}")
                return False
            
            # Set current repo name for context
            self._current_repo_name = repo_name
            
            # Update the repository using git pull
            try:
                git_exe = self.base_path / "ps_env" / "Library" / "cmd" / "git.exe"
                result = subprocess.run(
                    [str(git_exe), "pull"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    check=True
                )
                logger.info(f"Git pull output: {result.stdout}")
                
                # Reinstall dependencies after update
                if not self._install_dependencies(repo_path):
                    logger.warning(f"Failed to reinstall dependencies for {repo_name}")
                
                logger.info(f"[OK] Repository {repo_name} updated successfully")
                return True
                
            except subprocess.CalledProcessError as e:
                logger.error(f"Git pull failed for {repo_name}: {e.stderr}")
                return False
            
        except Exception as e:
            logger.error(f"Error updating repository {repo_name}: {e}")
            return False
    
    def list_installed_repositories(self) -> list:
        """Get list of installed repositories.
        
        Returns:
            List of dictionaries with repository information
        """
        repos = []
        repos_path = self.base_path / "repos"
        
        if not repos_path.exists():
            logger.info("No repositories directory found")
            return repos
        
        for item in repos_path.iterdir():
            item: Path  # Type annotation for PyRight
            if item.is_dir() and not item.name.startswith('.'):
                # Check if launcher exists
                bat_file = item / f"start_{item.name}.bat"
                sh_file = item / f"start_{item.name}.sh"
                has_launcher = bat_file.exists() or sh_file.exists()
                
                repo_info = {
                    'name': item.name,
                    'path': str(item),
                    'has_launcher': has_launcher
                }
                repos.append(repo_info)
        
        logger.info(f"Found repositories: {len(repos)}")
        for repo in repos:
            launcher_status = "[OK]" if repo['has_launcher'] else "[ERROR]"
            logger.info(f"  - {repo['name']} {launcher_status}")
        
        return repos
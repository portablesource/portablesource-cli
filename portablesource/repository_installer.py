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
                return None
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
                return None
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
                    return plan_data['installation_plan']
                else:
                    logger.error(f"Invalid installation plan data received for '{name}'")
                    return None
            elif response.status_code == 404:
                return None
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
        
        if 'success' in data and 'repository' in data:
            if not data.get('success', False):
                return False
            
            repository = data.get('repository')
            if not repository or not isinstance(repository, dict):
                return False
            
            if 'repositoryUrl' not in repository:
                return False
            
            url = repository.get('repositoryUrl')
            if url and not isinstance(url, str):
                return False
            
            # Strip whitespace from URL and validate it's not empty
            if url:
                url = url.strip()
                if not url:
                    return False
                repository['repositoryUrl'] = url
            
            optional_fields = ['filePath', 'programArgs', 'description']
            for field in optional_fields:
                if field in repository and repository[field] is not None and not isinstance(repository[field], str):
                    return False
            
            return True
        
        if 'url' not in data:
            return False
        
        url = data.get('url')
        if url and not isinstance(url, str):
            return False
        
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
        
        if 'steps' not in plan:
            return False
        
        steps = plan['steps']
        if not isinstance(steps, list):
            return False
        
        for step in steps:
            if not isinstance(step, dict):
                return False
            if 'type' not in step or 'packages' not in step:
                return False
            if not isinstance(step['packages'], list):
                return False
            
            valid_types = ['torch', 'regular', 'onnxruntime', 'insightface', 'triton']
            if step['type'] not in valid_types:
                return False
            
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
        
        expected_fields = ['requirements', 'packages', 'dependencies']
        if not any(field in data for field in expected_fields):
            return False
        
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
        line = line.split('#')[0].strip()
        
        if '--index-url' in line or '--extra-index-url' in line:
            return None
            
        if not line or line.startswith('-'):
            return None

        match = re.match(r'^([a-zA-Z0-9_-]+)(?:\[([^\]]+)\])?(.*)$', line)
        if not match:
            return None
        
        package_name = match.group(1).lower()
        extras = match.group(2).split(',') if match.group(2) else None
        version_part = match.group(3)
        
        version = None
        if version_part:
            version_match = re.search(r'[=<>!]+([^\s,;]+)', version_part)
            if version_match:
                version = version_match.group(1)
        
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
                lines = f.readlines()
                
                for line_num, line in enumerate(lines, 1):
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
        
        logger.info(f"Successfully parsed {len(packages)} packages from requirements")
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
        
        if plan.torch_packages:
            plan.torch_index_url = self._get_torch_index_url_from_config(gpu_config_obj)
        
        if plan.onnx_packages:
            plan.onnx_package_name = self._get_onnx_package_name_from_config(gpu_config_obj)
        
        return plan
    
    def _get_torch_index_url_from_config(self, gpu_config) -> str:
        """Get PyTorch index URL based on GPU configuration"""
        if not gpu_config or not gpu_config.name or not gpu_config.name.upper().startswith('NVIDIA'):
            return "https://download.pytorch.org/whl/cpu"
        
        cuda_version = gpu_config.cuda_version if gpu_config else None
        
        if cuda_version:
            if hasattr(cuda_version, 'value'):
                cuda_version_str = cuda_version.value
            else:
                cuda_version_str = str(cuda_version)
            
            if cuda_version_str in ["12.8", "128"]:
                return "https://download.pytorch.org/whl/cu128"
            elif cuda_version_str in ["12.4", "124"]:
                return "https://download.pytorch.org/whl/cu124"
            elif cuda_version_str in ["11.8", "118"]:
                return "https://download.pytorch.org/whl/cu118"
        
        return "https://download.pytorch.org/whl/cpu"
    
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
        
        server_info = self.server_client.get_repository_info(repo_name)
        
        if server_info:
            if 'success' in server_info and 'repository' in server_info:
                repository = server_info['repository']
                main_file = repository.get('filePath')
            else:
                main_file = server_info.get('main_file')
            
            if main_file and self._validate_main_file(repo_path, main_file):
                return main_file
            else:
                logger.warning(f"Server returned main file '{main_file}' but it doesn't exist in repository")
        
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
                        main_file = server_info.get('main_file')
                    
                    if main_file and self._validate_main_file(repo_path, main_file):
                        return main_file
        
        search_results = self.server_client.search_repositories(repo_name)
        for result in search_results:
            main_file = result.get('main_file')
            if main_file and self._validate_main_file(repo_path, main_file):
                return main_file
        
        for main_file in self.common_main_files:
            if self._validate_main_file(repo_path, main_file):
                return main_file
        
        python_files = list(repo_path.glob("*.py"))
        
        excluded_patterns = ['test_', 'setup.py', 'config.py', '__', 'install']
        main_candidates = []
        
        for py_file in python_files:
            filename = py_file.name.lower()
            if not any(pattern in filename for pattern in excluded_patterns):
                main_candidates.append(py_file.name)
        
        if len(main_candidates) == 1:
            return main_candidates[0]
        elif len(main_candidates) > 1:
            for candidate in main_candidates:
                if any(pattern in candidate.lower() for pattern in ['main', 'run', 'start', 'app']):
                    return candidate
        
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
        if install_path:
            if isinstance(install_path, str):
                self.base_path = Path(install_path)
            else:
                self.base_path = install_path
        else:
            self.base_path = Path.cwd()
            
        if config_manager is None:
            from .utils import load_install_path_from_registry
            install_path_from_registry = load_install_path_from_registry()
            if install_path_from_registry:
                config_path = install_path_from_registry / "portablesource_config.json"
            else:
                config_path = self.base_path / "portablesource_config.json"
            self.config_manager = ConfigManager(config_path)
            self.config_manager.load_config()
        else:
            self.config_manager = config_manager
        self.analyzer = RequirementsAnalyzer(config_manager=self.config_manager)
        
        self.environment_manager = PortableEnvironmentManager(self.base_path, self.config_manager)
        
        self.server_client = ServerAPIClient(server_url)
        self.main_file_finder = MainFileFinder(self.server_client)
        

        if not self.server_client.is_server_available():
            logger.warning("PortableSource server not available - using fallback methods only")
        
        self.fallback_repositories = {
            "facefusion": {
                "url": "https://github.com/facefusion/facefusion",
                "branch": "master",
                "main_file": "facefusion.py",
                "program_args": "run",
                "special_setup": None
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
            
            is_url = self._is_repository_url(repo_url_or_name)
            if is_url:
                self._current_repo_name = self._extract_repo_name(repo_url_or_name)
            else:
                self._current_repo_name = repo_url_or_name.lower()
            
            if is_url:
                return self._handle_url_installation(repo_url_or_name, install_path)
            else:
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
            # For URL installations, always clone the repository and use local requirements.txt
            # This ensures that the repository code is available and dependencies are installed correctly
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
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan data for {repo_name}")
                return False

            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
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
            repo_info = self._get_repository_info(repo_name)
            
            if not repo_info:
                logger.error(f"Repository '{repo_name}' not found")
                return False
            
            repo_path = install_path / repo_name
            
            if not self._clone_or_update_repository(repo_info, repo_path):
                return False
            
            if not self._install_dependencies(repo_path):
                return False
            
            if repo_info.get("special_setup"):
                repo_info["special_setup"](repo_path)
            
            self._generate_startup_script(repo_path, repo_info)
            
            self._send_download_stats(repo_name)

            return True
            
        except Exception as e:
            logger.error(f"Error handling name installation for {repo_name}: {e}")
            return False
    
    def _get_repository_info(self, repo_url_or_name: str) -> Optional[Dict]:
        """Get repository information from server API or fallback methods"""
        
        if repo_url_or_name.startswith(("http://", "https://", "git@")):
            repo_url = repo_url_or_name
            repo_name = self._extract_repo_name(repo_url)
        elif "/" in repo_url_or_name and not repo_url_or_name.startswith("http"):
            repo_url = f"https://github.com/{repo_url_or_name}"
            repo_name = repo_url_or_name.split('/')[-1].lower()
        else:
            repo_name = repo_url_or_name.lower()
            repo_url = None
        
        if repo_name is not None:
            server_info = self.server_client.get_repository_info(repo_name)
        if server_info and repo_name is not None:
            if 'success' in server_info and 'repository' in server_info:
                repository = server_info['repository']
                return {
                    "url": repository.get("repositoryUrl", repo_url).strip() if repository.get("repositoryUrl") else repo_url,
                    "main_file": repository.get("filePath", "main.py"),
                    "program_args": repository.get("programArgs", ""),
                    "special_setup": self._get_special_setup(repo_name)
                }
            else:
                return {
                    "url": server_info.get("url", repo_url),
                    "main_file": server_info.get("main_file", "main.py"),
                    "program_args": server_info.get("program_args", ""),
                    "special_setup": self._get_special_setup(repo_name)
                }
        
        # Try fallback repositories
        if repo_name in self.fallback_repositories:
            return self.fallback_repositories[repo_name]
        
        if repo_url and repo_name is not None:
            return {
                "url": repo_url,
                "main_file": None,
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
            repo_name = self._extract_repo_name(repo_url)
            if repo_name is not None:
                repo_info = {
                    "url": repo_url,
                    "main_file": None,
                    "special_setup": self._get_special_setup(repo_name)
                }
            
                repo_path = install_path / repo_name
            
            if not self._clone_or_update_repository(repo_info, repo_path):
                return False
            
            if not self._install_dependencies(repo_path):
                return False
            
            if repo_info.get("special_setup"):
                repo_info["special_setup"](repo_path)
            
            self._generate_startup_script(repo_path, repo_info)
            
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
        
        if 'steps' not in plan:
            return False
        
        steps = plan['steps']
        if not isinstance(steps, list):
            return False
        
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
        
        if 'url' not in data:
            return False
        
        url = data.get('url')
        if url and not isinstance(url, str):
            return False
        
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
        
        expected_fields = ['requirements', 'packages', 'dependencies']
        if not any(field in data for field in expected_fields):
            return False
        
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
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan data for {repo_name}")
                return False
            
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
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
            "wangp": self._setup_wangp
        }
        return special_setups.get(repo_name.lower(), None)
    

    
    def _clone_or_update_repository(self, repo_info: Dict, repo_path: Path) -> bool:
        """Clone or update repository with automatic error fixing"""
        try:
            git_exe = self._get_git_executable()
            
            if repo_path.exists():
                os.chdir(repo_path)
                
                if (repo_path / ".git").exists():
                    if not self._update_repository_with_fixes(git_exe, repo_path):
                        return False
                else:
                    logger.warning(f"Directory exists but is not a git repository: {repo_path}")
                    return False
            else:
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
                
                if attempt < max_attempts - 1:
                    if self._fix_git_issues(git_exe, repo_path, error_output):
                        continue
                
                if attempt == max_attempts - 1:
                    logger.error(f"Failed to update repository after {max_attempts} attempts")
                    return False
        
        return False
    
    def _fix_git_issues(self, git_exe: str, repo_path: Path, error_output: str) -> bool:
         """Try to fix common git issues automatically"""
         try:
             if "diverged" in error_output.lower() or "non-fast-forward" in error_output.lower():
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             if "uncommitted changes" in error_output.lower() or "would be overwritten" in error_output.lower():
                 subprocess.run([git_exe, "stash"], check=True, capture_output=True)
                 return True
             
             if "merge conflict" in error_output.lower() or "conflict" in error_output.lower():
                 subprocess.run([git_exe, "merge", "--abort"], capture_output=True)  # Don't check=True as it might fail
                 subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                 subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                 return True
             
             if "detached head" in error_output.lower():
                 try:
                     subprocess.run([git_exe, "checkout", "main"], check=True, capture_output=True)
                 except subprocess.CalledProcessError:
                     subprocess.run([git_exe, "checkout", "master"], check=True, capture_output=True)
                 return True
             
             if "index" in error_output.lower() and "corrupt" in error_output.lower():
                 subprocess.run([git_exe, "reset", "--mixed"], check=True, capture_output=True)
                 return True
             
             if "no tracking information" in error_output.lower():
                 subprocess.run([git_exe, "branch", "--set-upstream-to=origin/main"], check=True, capture_output=True)
                 return True
             
             if "128" in error_output or "fatal:" in error_output.lower():
                 try:
                     subprocess.run([git_exe, "fetch", "origin"], check=True, capture_output=True)
                     subprocess.run([git_exe, "reset", "--hard", "origin/main"], check=True, capture_output=True)
                     return True
                 except subprocess.CalledProcessError:
                     try:
                         subprocess.run([git_exe, "reset", "--hard", "origin/master"], check=True, capture_output=True)
                         return True
                     except subprocess.CalledProcessError:
                         subprocess.run([git_exe, "clean", "-fd"], capture_output=True)
                         subprocess.run([git_exe, "reset", "--hard", "HEAD"], capture_output=True)
                         return True
             
             if "permission denied" in error_output.lower() or "unable to create" in error_output.lower():
                 import time
                 time.sleep(2)
                 subprocess.run([git_exe, "gc", "--prune=now"], capture_output=True)
                 return True
             
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
        
        return "git"
    
    def _get_python_executable(self) -> str:
        """Get Python executable path from portable environment"""
        python_path = self.environment_manager.get_python_executable()
        if python_path and python_path.exists():
            return str(python_path)
        
        return "python"
    
    def _activate_portable_environment(self) -> bool:
        """Activate portable environment to make packages visible"""
        try:
            env_vars = self.environment_manager.setup_environment_for_subprocess()
            
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
            python_path = venv_path / "python.exe" if os.name == 'nt' else venv_path / "bin" / "python"
            if python_path.exists():
                return [str(python_path), "-m", "pip"]
        
        return ["python", "-m", "pip"]
    
    def _get_uv_executable(self, repo_name: str) -> List[str]:
        """Get uv executable command from repository's environment"""
        if self.config_manager.config and self.config_manager.config.install_path:
            install_path = Path(self.config_manager.config.install_path)
            venv_path = install_path / "envs" / repo_name
            python_path = venv_path / "python.exe" if os.name == 'nt' else venv_path / "bin" / "python"
            if python_path.exists():
                return [str(python_path), "-m", "uv"]
        
        return ["python", "-m", "uv"]
    
    def _install_uv_in_venv(self, repo_name: str) -> bool:
        """Install uv in the venv environment"""
        try:
            uv_cmd = self._get_uv_executable(repo_name)
            try:
                result = subprocess.run(uv_cmd + ["--version"], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    return True
            except Exception:
                pass  

            pip_exe = self._get_pip_executable(repo_name)
            self._run_pip_with_progress(pip_exe + ["install", "uv"], "Installing uv")
            
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

            
            if not self._create_venv_environment(repo_name):
                logger.error(f"Failed to create venv environment for {repo_name}")
                return False
            
            server_plan = self.server_client.get_installation_plan(repo_name)
            if server_plan:

                if self._execute_server_installation_plan(server_plan, repo_path, repo_name):
                    return True
                else:
                    logger.warning(f"Server installation failed for {repo_name}, falling back to local requirements")
            else:
                pass
            
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
                return True  # No requirements to install, consider it successful
            

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
            
            if not ps_env_python_path.exists():
                logger.error(f"Portable Python not found at: {ps_env_python_path}")
                return False
            
            envs_path.mkdir(parents=True, exist_ok=True)
            
            if venv_path.exists():
                import shutil
                shutil.rmtree(venv_path)
            
            import shutil
            logger.info(f"Creating environment by copying portable Python: {ps_env_python_path} -> {venv_path}")
            shutil.copytree(ps_env_python_path, venv_path)
            
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
            if not requirements_path or not requirements_path.exists():
                logger.warning(f"Requirements file not found: {requirements_path}")
                return True  # No requirements to install, consider it successful
                

            
            if not self._install_uv_in_venv(repo_name):
                logger.warning("Failed to install uv, falling back to pip for all packages")
                return self._install_packages_with_pip_only(repo_name, requirements_path)
            
            if not self._activate_portable_environment():
                logger.warning("Failed to activate portable environment, CUDA packages may not be visible")
            
            packages = self.analyzer.analyze_requirements(requirements_path)
            
            gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
            plan = self.analyzer.create_installation_plan(packages, gpu_config_obj)
            
            pip_exe = self._get_pip_executable(repo_name)
            uv_cmd = self._get_uv_executable(repo_name)
            
            if plan.torch_packages:
                torch_cmd = pip_exe + ["install", "--force-reinstall"]
                
                for package in plan.torch_packages:
                    torch_cmd.append(str(package))
                
                if plan.torch_index_url:
                    torch_cmd.extend(["--index-url", plan.torch_index_url])
                
                self._run_pip_with_progress(torch_cmd, "Installing PyTorch packages")
            
            if plan.onnx_packages:
                onnx_package_name = plan.onnx_package_name or "onnxruntime"
                
                onnxruntime_package = next((p for p in plan.onnx_packages if p.name == 'onnxruntime'), None)
                if onnxruntime_package and onnxruntime_package.version:
                    package_str = f"{onnx_package_name}=={onnxruntime_package.version}"
                else:
                    package_str = onnx_package_name

                self._run_pip_with_progress(pip_exe + ["install", package_str], f"Installing ONNX package: {package_str}")
            
            if plan.insightface_packages:
                for package in plan.insightface_packages:
                    self._handle_insightface_package(package)
            
            if plan.triton_packages:
                logger.info("Handling Triton packages...")
                for package in plan.triton_packages:
                    self._handle_triton_package(package)

            if plan.regular_packages:
                temp_requirements = requirements_path.parent / "requirements_regular_temp.txt"
                with open(temp_requirements, 'w', encoding='utf-8') as f:
                    for package in plan.regular_packages:
                        f.write(package.original_line + '\n')
                
                try:
                    uv_install_cmd = uv_cmd + ["pip", "install", "-r", str(temp_requirements)]
                    self._run_uv_with_progress(uv_install_cmd, "Installing regular packages with uv")
                finally:
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
            if not requirements_path or not requirements_path.exists():
                logger.warning(f"Requirements file not found: {requirements_path}")
                return True  # No requirements to install, consider it successful
                
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
            if not self._validate_server_plan(server_plan):
                logger.error(f"Invalid server plan structure for {repo_name}")
                return False
            
            if not self._install_uv_in_venv(repo_name):
                logger.warning("Failed to install uv, some packages may use pip fallback")
            
            if not self._activate_portable_environment():
                logger.warning("Failed to activate portable environment, CUDA packages may not be visible")
            
            pip_exe = self._get_pip_executable(repo_name)
            
            steps = server_plan.get('steps', [])
            if not steps:
                logger.warning(f"No installation steps found in server plan for {repo_name}")
                return True
            
            for step_index, step in enumerate(steps):
                if not isinstance(step, dict):
                    logger.error(f"Invalid step structure at index {step_index} for {repo_name}")
                    return False
                
                step_type = step.get('type', '')
                packages = step.get('packages', [])
                install_flags = step.get('install_flags', [])
                
                if not step_type:
                    logger.error(f"Missing step type at index {step_index} for {repo_name}")
                    return False
                
                if not isinstance(packages, list):
                    logger.error(f"Invalid packages format at step {step_index} for {repo_name}")
                    return False
                
                if not packages:
                    logger.debug(f"Skipping empty package list at step {step_index} for {repo_name}")
                    continue
                
                if step_type == 'regular':
                    special_packages = {'onnxruntime': [], 'torch': [], 'insightface': [], 'triton': []}
                    regular_packages = []
                    
                    for package in packages:
                        if isinstance(package, str) and package.strip():
                            package_str = package.strip().lower()
                            if package_str.startswith('onnxruntime'):
                                special_packages['onnxruntime'].append(package)
                            elif package_str in ['torch'] or package_str.startswith('torch==') or package_str.startswith('torch>=') or package_str.startswith('torch<=') or package_str.startswith('torch>') or package_str.startswith('torch<') or package_str.startswith('torch!=') or package_str.startswith('torch~=') or \
                                 package_str in ['torchvision'] or package_str.startswith('torchvision==') or package_str.startswith('torchvision>=') or package_str.startswith('torchvision<=') or package_str.startswith('torchvision>') or package_str.startswith('torchvision<') or package_str.startswith('torchvision!=') or package_str.startswith('torchvision~=') or \
                                 package_str in ['torchaudio'] or package_str.startswith('torchaudio==') or package_str.startswith('torchaudio>=') or package_str.startswith('torchaudio<=') or package_str.startswith('torchaudio>') or package_str.startswith('torchaudio<') or package_str.startswith('torchaudio!=') or package_str.startswith('torchaudio~='):
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
                            elif pkg_name in ['torch', 'torchvision', 'torchaudio']:
                                special_packages['torch'].append(package)
                            elif pkg_name.startswith('insightface'):
                                special_packages['insightface'].append(package)
                            elif pkg_name.startswith('triton'):
                                special_packages['triton'].append(package)
                            else:
                                regular_packages.append(package)
                        else:
                            regular_packages.append(package)
                    
                    for special_type, special_pkgs in special_packages.items():
                        if special_pkgs:
                            special_step = {
                                'type': special_type,
                                'packages': special_pkgs,
                                'install_flags': install_flags,
                                'description': f'Install {special_type} packages'
                            }
                            if not self._process_installation_step(special_step, step_index, server_plan, repo_name, pip_exe):
                                return False
                    
                    if regular_packages:
                        regular_step = {
                            'type': 'regular_only',
                            'packages': regular_packages,
                            'install_flags': install_flags,
                            'description': 'Install regular packages'
                        }
                        if not self._process_installation_step(regular_step, step_index, server_plan, repo_name, pip_exe):
                            return False
                    
                    continue 
                
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
            
            install_cmd, use_uv, use_uv_first = self._prepare_install_command(step_type, repo_name, pip_exe)
            
            self._add_packages_to_command(install_cmd, packages, step_type, server_plan)
            
            self._add_install_flags_and_urls(install_cmd, install_flags, server_plan)
            
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
        if step_type in ['regular_only', 'onnxruntime', 'insightface', 'triton', 'torch']:
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
        
        pkg_name = self._apply_special_package_handling(pkg_name, step_type, server_plan)
        
        if step_type == 'insightface' and pkg_name == 'insightface' and os.name == "nt":
            install_cmd.append("https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl")
            return
        
        pkg_str = self._build_package_string(pkg_name, pkg_version)
        install_cmd.append(pkg_str)
    
    def _apply_gpu_detection_to_onnx(self, package_str: str) -> str:
        """Apply GPU auto-detection for onnxruntime packages"""
        gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
        
        if not (gpu_config_obj and gpu_config_obj.name):
            return package_str
        
        gpu_name_upper = gpu_config_obj.name.upper()
        
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
        # Check if this is a torch installation step by looking at the command
        # Only add torch index URL if ALL packages are torch-related (torch, torchvision, torchaudio)
        torch_packages = ['torch', 'torchvision', 'torchaudio']
        package_args = [arg for arg in install_cmd if not arg.startswith('-') and 'pip' not in arg and 'install' not in arg and 'python' not in arg and 'uv' not in arg]
        
        # Extract package names from version specifications like "torch>=2.4.0"
        package_names = []
        for arg in package_args:
            # Split by common version operators
            for op in ['>=', '<=', '==', '!=', '>', '<', '~=']:
                if op in arg:
                    package_names.append(arg.split(op)[0].strip())
                    break
            else:
                package_names.append(arg.strip())
        
        is_pure_torch_step = package_names and all(
            pkg_name.lower() in torch_packages for pkg_name in package_names
        )
        
        # Check if any torch packages are being installed
        has_torch_packages = any(pkg_name.lower() in torch_packages for pkg_name in package_names)
        
        if has_torch_packages and '--index-url' not in install_cmd:
            # For any torch packages, use GPU-specific index URL and force reinstall
            torch_index_url = server_plan.get('torch_index_url') or self._get_default_torch_index_url()
            install_cmd.extend(['--index-url', torch_index_url])
            if '--force-reinstall' not in install_cmd:
                install_cmd.append('--force-reinstall')
        elif server_plan.get('torch_index_url') and '--index-url' not in install_cmd:
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
            packages_and_flags = install_cmd[3:]
            
            if packages_and_flags:
                pip_install_cmd = pip_exe + ["install"] + packages_and_flags
                self._run_pip_with_progress(pip_install_cmd, f"{step_description} (pip fallback)")
            else:
                logger.warning(f"No packages to install in fallback for step {step_index}")
        
        return True
    
    def _execute_installation_plan(self, plan: InstallationPlan, original_requirements: Path, repo_name: str) -> bool:
        """Execute the installation plan using base Python"""
        try:
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
            
            if plan.onnx_packages:
                gpu_config_obj = self.config_manager.config.gpu_config if self.config_manager.config else None
                
                for package in plan.onnx_packages:
                    package_str = str(package)
                    if package.name == "onnxruntime" and gpu_config_obj and gpu_config_obj.name:
                        gpu_name_upper = gpu_config_obj.name.upper()
                        if gpu_name_upper.startswith('NVIDIA'):
                            if package.version:
                                package_str = f"onnxruntime-gpu=={package.version}"
                            else:
                                package_str = "onnxruntime-gpu"
                        elif gpu_name_upper.startswith('AMD') and os.name == "nt":
                            if package.version:
                                package_str = f"onnxruntime-directml=={package.version}"
                            else:
                                package_str = "onnxruntime-directml"
                        elif gpu_name_upper.startswith('AMD') and os.name == "posix":
                            if package.version:
                                package_str = f"onnxruntime-rocm=={package.version}"
                            else:
                                package_str = "onnxruntime-rocm"
                        elif gpu_name_upper.startswith('INTEL'):
                            if package.version:
                                package_str = f"onnxruntime-directml=={package.version}"
                            else:
                                package_str = "onnxruntime-directml"

                    repo_name = getattr(self, '_current_repo_name', 'default')
                    self._install_package_with_progress([package_str], f"Installing ONNX package: {package_str}", repo_name)
            
            if plan.insightface_packages:
                for package in plan.insightface_packages:
                    self._handle_insightface_package(package)
            
            if plan.triton_packages:
                for package in plan.triton_packages:
                    self._handle_triton_package(package)
            
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
    
    def _setup_wangp(self, repo_path: Path):
        """Special setup for wangp repository - install mmgp==3.5.6"""
        try:
            repo_name = repo_path.name
            logger.info(f"Running special setup for {repo_name}: installing mmgp==3.5.6")
            
            # Try to use uv first, fallback to pip
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
                uv_cmd = self._get_uv_executable(repo_name)
                install_cmd = uv_cmd + ["pip", "install", "mmgp==3.5.6"]
                self._run_uv_with_progress(install_cmd, "Installing mmgp==3.5.6 for wangp")
            else:
                pip_cmd = self._get_pip_executable(repo_name)
                install_cmd = pip_cmd + ["install", "mmgp==3.5.6"]
                self._run_pip_with_progress(install_cmd, "Installing mmgp==3.5.6 for wangp")
            
            logger.info(f"Special setup for {repo_name} completed successfully")
            
        except Exception as e:
            logger.error(f"Error during special setup for wangp: {e}")
            # Don't fail the entire installation for this
    
    def _generate_startup_script(self, repo_path: Path, repo_info: Dict):
        """Generate startup script using copied Python environment with manual CUDA/library path setup.
        
        This version uses portable environment system with copied Python installations
        instead of traditional venv, providing better isolation and compatibility.
        """
        try:
            repo_name = repo_path.name.lower()
            
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
            
            bat_file = repo_path / f"start_{repo_name}.bat"
            
            if not self.config_manager.config or not self.config_manager.config.install_path:
                logger.error("Install path not configured")
                return False
            
            install_path = Path(self.config_manager.config.install_path)
            program_args = repo_info.get('program_args', '') or ''

            if (self.config_manager.config and 
                self.config_manager.config.gpu_config and 
                self.config_manager.config.gpu_config.cuda_paths):
                cuda_paths_section = f"""set tmp_path=X:\\tmp
set cuda_bin=X:\\CUDA\\bin
set cuda_lib=X:\\CUDA\\lib
set cuda_lib_64=X:\\CUDA\\lib\\x64
set cuda_nvml_bin=X:\\CUDA\\nvm\\bin
set cuda_nvml_lib=X:\\CUDA\\nvml\\lib
set cuda_nvvm_bin=X:\\CUDA\\nvml\\bin
set cuda_nvvm_lib=X:\\CUDA\\nvvm\\lib

set PATH=%cuda_bin%;%PATH%
set PATH=%cuda_lib%;%PATH%
set PATH=%cuda_lib_64%;%PATH%
set PATH=%cuda_nvml_bin%;%PATH%
set PATH=%cuda_nvml_lib%;%PATH%
set PATH=%cuda_nvvm_bin%;%PATH%
set PATH=%cuda_nvvm_lib%;%PATH%

echo All CUDA paths added to environment"""
            else:
                cuda_paths_section = "REM No CUDA paths configured"
            
            bat_content = f"""@echo off
echo Launch {repo_name}...

subst X: {install_path}
X:

set env_path=X:\\ps_env
set ffmpeg_path=X:\\ps_env\\ffmpeg
set python_exe=X:\\envs\\{repo_name}\\python.exe
set repo_path=X:\\repos\\{repo_name}
REM Setup temporary directory
set USERPROFILE=%tmp_path%
set TEMP=%tmp_path%
set TMP=%tmp_path%

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
set PATH=%env_path%;%PATH%
set PATH=%env_path%\\\\Scripts;%PATH%
echo Python environment and ffmpeg paths added to PATH
set PATH=%ffmpeg_path%;%PATH%

REM Change to repository directory and run
cd /d "%repo_path%"
"%python_exe%" {main_file} {program_args}
echo Cleaning up...
subst X: /D

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
                return
                
            url = f"{self.server_client.server_url}/api/repositories/{repo_name.lower()}/download"
            response = self.server_client.session.post(
                url,
                json={
                    'repository_name': repo_name.lower(),
                    'success': True,
                    'timestamp': None
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
    
    def _run_pip_with_progress(self, pip_cmd: List[str], description: str):
        """Run pip command with progress bar if tqdm is available"""
        TQDM_AVAILABLE = True
        try:
            if TQDM_AVAILABLE:
                process = subprocess.Popen(
                    pip_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                with tqdm(desc=description, unit="line", dynamic_ncols=True) as pbar:
                    output_lines = []
                    if process.stdout:
                        for line in process.stdout:
                            output_lines.append(line)
                            pbar.update(1)
                            
                            if "Installing" in line or "Downloading" in line or "ERROR" in line:
                                pbar.set_postfix_str(line.strip()[:50])
                
                process.wait()
                
                if process.returncode != 0:
                    error_output = ''.join(output_lines)
                    raise subprocess.CalledProcessError(process.returncode, pip_cmd, error_output)
            else:
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
                            
                            if "Installing" in line or "Downloading" in line or "ERROR" in line or "Resolved" in line:
                                pbar.set_postfix_str(line.strip()[:50])
                
                # Wait for completion
                process.wait()
                
                if process.returncode != 0:
                    error_output = ''.join(output_lines)
                    raise subprocess.CalledProcessError(process.returncode, uv_cmd, error_output)
            else:
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
                 process = subprocess.Popen(
                     git_cmd,
                     stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT,
                     text=True,
                     bufsize=1,
                     universal_newlines=True
                 )
                 
                 with tqdm(desc=description, unit="line", dynamic_ncols=True) as pbar:
                     output_lines = []
                     if process.stdout:
                         for line in process.stdout:
                             output_lines.append(line)
                             pbar.update(1)
                             
                             if any(keyword in line.lower() for keyword in ["cloning", "receiving", "resolving", "updating", "error"]):
                                 pbar.set_postfix_str(line.strip()[:50])
                 
                 process.wait()
                 
                 if process.returncode != 0:
                     error_output = ''.join(output_lines)
                     error = subprocess.CalledProcessError(process.returncode, git_cmd, error_output)
                     error.output = error_output
                     raise error
             else:
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
                    torch_index_url = server_plan.get('torch_index_url') or self._get_default_torch_index_url()
                    
                    self._install_package_with_progress(
                        packages, 
                        f"Installing PyTorch packages: {', '.join(packages)}", 
                        repo_name, 
                        index_url=torch_index_url
                    )
                
                elif step_type == 'onnxruntime':
                    onnx_package_name = server_plan.get('onnx_package_name') or self._get_default_onnx_package()
                    
                    onnx_packages = []
                    for package in packages:
                        if isinstance(package, str) and package.startswith('onnxruntime'):
                            if '==' in package:
                                version = package.split('==')[1]
                                onnx_packages.append(f"{onnx_package_name}=={version}")
                            else:
                                onnx_packages.append(onnx_package_name)
                        else:
                            onnx_packages.append(package if isinstance(package, str) else package.get('package_name', str(package)))
                    
                    self._install_package_with_progress(onnx_packages, f"Installing ONNX packages: {', '.join(onnx_packages)}", repo_name)
                
                elif step_type == 'triton':
                    triton_packages = []
                    for package in packages:
                        package_name = package if isinstance(package, str) else package.get('package_name', str(package))
                        triton_packages.append(package_name)
                    
                    if triton_packages:
                        self._install_package_with_progress(triton_packages, f"Installing Triton packages: {', '.join(triton_packages)}", repo_name)
                
                else:
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
            version = None
            if '==' in package_name:
                name, version = package_name.split('==', 1)
            else:
                name = package_name
            
            temp_package = PackageInfo(
                name=name,
                version=version,
                package_type=PackageType.INSIGHTFACE,
                original_line=package_name
            )
            
            self._handle_insightface_package(temp_package)
            
        except Exception as e:
            logger.error(f"Error handling InsightFace package {package_name}: {e}")
            repo_name = getattr(self, '_current_repo_name', 'default')
            self._install_package_with_progress([package_name], f"Installing InsightFace package: {package_name}", repo_name)
    
    def _handle_insightface_package(self, package: PackageInfo):
        """Handle InsightFace package installation with special requirements"""
        try:
            package_str = str(package)
            
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
            
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
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
            
            uv_available = self._install_uv_in_venv(repo_name)
            
            if uv_available:
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
            package_str = str(package)
            
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
            
            self._current_repo_name = repo_name
            
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
            item: Path
            if item.is_dir() and not item.name.startswith('.'):
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
#!/usr/bin/env python3
"""
Configuration System for PortableSource

This module manages configuration for GPU detection, CUDA versions, and system paths.
"""

SERVER_DOMAIN = "portables.dev"

import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
from .Version import __version__ as ver
import os

logger = logging.getLogger(__name__)


class GPUGeneration(Enum):
    """GPU generations for CUDA compatibility"""
    PASCAL = "pascal"          # GTX 10xx series
    TURING = "turing"          # GTX 16xx, RTX 20xx series  
    AMPERE = "ampere"          # RTX 30xx series
    ADA_LOVELACE = "ada"       # RTX 40xx series
    BLACKWELL = "blackwell"    # RTX 50xx series
    UNKNOWN = "unknown"


class CUDAVersion(Enum):
    """Available CUDA versions"""
    CUDA_118 = "118"
    CUDA_124 = "124"
    CUDA_128 = "128"

class CUDALinks(Enum):
    """Available CUDA download links"""
    CUDA_118 = "https://files.portables.dev/CUDA/CUDA_118.7z"  
    CUDA_124 = "https://files.portables.dev/CUDA/CUDA_124.7z"  
    CUDA_128 = "https://files.portables.dev/CUDA/CUDA_128.7z"  

class TOOLinks(Enum):
    """Available tool (ffmpeg, git, python) download links"""
    GIT_URL = "https://files.portables.dev/git.7z"
    FFMPEG_URL = "https://files.portables.dev/ffmpeg.7z"
    PYTHON311_URL = "https://files.portables.dev/python311.7z"

@dataclass
class CUDAPaths:
    """CUDA installation paths"""
    base_path: str
    
    def __post_init__(self):
        slash = "\\" if os.name == "nt" else "/"
        
        self.cuda_bin = f"{self.base_path}{slash}bin"
        self.cuda_lib = f"{self.base_path}{slash}lib"
        self.cuda_include = f"{self.base_path}{slash}include"
        self.cuda_nvml = f"{self.base_path}{slash}nvml"
        self.cuda_nvvm = f"{self.base_path}{slash}nvvm"
        
        self.cuda_lib_64 = f"{self.cuda_lib}{slash}x64"

        self.cuda_nvml_include = f"{self.cuda_nvml}{slash}include"
        self.cuda_nvml_bin = f"{self.cuda_nvml}{slash}bin"
        self.cuda_nvml_lib = f"{self.cuda_nvml}{slash}lib"
                
        self.cuda_nvvm_include = f"{self.cuda_nvvm}{slash}include"
        self.cuda_nvvm_bin = f"{self.cuda_nvvm}{slash}bin"
        self.cuda_nvvm_lib = f"{self.cuda_nvvm}{slash}lib"

@dataclass
class GPUConfig:
    """GPU configuration"""
    name: str = ""
    generation: GPUGeneration = GPUGeneration.UNKNOWN
    cuda_version: Optional[CUDAVersion] = None
    cuda_paths: Optional[CUDAPaths] = None
    compute_capability: str = ""
    memory_gb: int = 0
    recommended_backend: str = "cpu"
    supports_tensorrt: bool = False
    
    def __post_init__(self):
        # CUDA version should be set explicitly in configure_gpu method
        # Don't set default CUDA version here to avoid overriding detection logic
        pass


@dataclass
class PortableSourceConfig:
    """Main configuration class"""
    version: str = ver
    install_path: str = ""
    gpu_config: Optional[GPUConfig] = None
    environment_vars: Optional[Dict[str, str]] = None
    environment_setup_completed: bool = False  # Tracks if environment setup is completed
    
    def __post_init__(self):
        # Don't create default GPUConfig here - let it be configured properly via detection
        # if self.gpu_config is None:
        #     self.gpu_config = GPUConfig()
        if self.environment_vars is None:
            self.environment_vars = {}


class ConfigManager:
    """Configuration manager for PortableSource"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.cwd() / "portablesource_config.json"
        # Don't create default config here - let it be loaded or created when needed
        self.config = None
        
        # GPU generation patterns
        self.gpu_patterns = {
            GPUGeneration.PASCAL: [
                "GTX 10", "GTX 1050", "GTX 1060", "GTX 1070", "GTX 1080",
                "TITAN X", "TITAN XP"
            ],
            GPUGeneration.TURING: [
                "GTX 16", "GTX 1650", "GTX 1660",
                "RTX 20", "RTX 2060", "RTX 2070", "RTX 2080",
                "TITAN RTX"
            ],
            GPUGeneration.AMPERE: [
                "RTX 30", "RTX 3060", "RTX 3070", "RTX 3080", "RTX 3090",
                "RTX A", "A40", "A100"
            ],
            GPUGeneration.ADA_LOVELACE: [
                "RTX 40", "RTX 4060", "RTX 4070", "RTX 4080", "RTX 4090",
                "RTX ADA", "L40", "L4"
            ],
            GPUGeneration.BLACKWELL: [
                "RTX 50", "RTX 5060", "RTX 5070", "RTX 5080", "RTX 5090"
            ]
        }
        
        # CUDA version mapping (только для NVIDIA GPU)
        self.cuda_mapping = {
            GPUGeneration.PASCAL: CUDAVersion.CUDA_118,
            GPUGeneration.TURING: CUDAVersion.CUDA_124,
            GPUGeneration.AMPERE: CUDAVersion.CUDA_124,
            GPUGeneration.ADA_LOVELACE: CUDAVersion.CUDA_128,
            GPUGeneration.BLACKWELL: CUDAVersion.CUDA_128
        }
        
    def _get_config_file_path(self) -> Path:
        """Get the configuration file path, preferring install_path if available"""
        # Use the config_path that was passed during initialization
        return self.config_path
        
    
    def detect_gpu_generation(self, gpu_name: str) -> GPUGeneration:
        """
        Detect GPU generation from name
        
        Args:
            gpu_name: Name of the GPU
            
        Returns:
            GPUGeneration enum
        """
        gpu_name_upper = gpu_name.upper()
        
        for generation, patterns in self.gpu_patterns.items():
            if any(pattern.upper() in gpu_name_upper for pattern in patterns):
                return generation
        
        logger.warning(f"Unknown GPU generation for: {gpu_name}")
        return GPUGeneration.UNKNOWN
    
    def get_recommended_cuda_version(self, generation: GPUGeneration) -> Optional[CUDAVersion]:
        """
        Get recommended CUDA version for GPU generation
        
        Args:
            generation: GPU generation
            
        Returns:
            CUDAVersion enum or None for non-NVIDIA GPUs
        """
        return self.cuda_mapping.get(generation)
    
    def configure_gpu(self, gpu_name: str, memory_gb: int = 0) -> GPUConfig:
        """
        Configure GPU settings
        
        Args:
            gpu_name: Name of the GPU
            memory_gb: GPU memory in GB
            
        Returns:
            GPUConfig object
        """
        generation = self.detect_gpu_generation(gpu_name)
        cuda_version = self.get_recommended_cuda_version(generation)
        
        # Determine compute capability
        compute_capability = self._get_compute_capability(generation)
        
        # Determine backend and TensorRT support
        supports_tensorrt = False
        
        gpu_name_upper = gpu_name.upper()
        if any(keyword in gpu_name_upper for keyword in ["NVIDIA", "GEFORCE", "QUADRO", "TESLA", "RTX", "GTX"]) and generation != GPUGeneration.UNKNOWN:
            # NVIDIA GPU detected with known generation
            if cuda_version:
                # Check if CUDA >= 12.4 and generation >= Turing for TensorRT
                if (cuda_version in [CUDAVersion.CUDA_124, CUDAVersion.CUDA_128] and 
                    generation in [GPUGeneration.TURING, GPUGeneration.AMPERE, GPUGeneration.ADA_LOVELACE, GPUGeneration.BLACKWELL]):
                    supports_tensorrt = True
                    backend = "cuda,tensorrt"
                else:
                    backend = "cuda"
            else:
                backend = "cpu"
        elif any(brand in gpu_name_upper for brand in ["AMD", "RADEON", "RX "]):
            backend = "dml"
        elif any(brand in gpu_name_upper for brand in ["INTEL", "UHD", "IRIS", "ARC"]):
            backend = "openvino"
        else:
            backend = "cpu"
        
        # Create CUDA paths if CUDA version is available
        cuda_paths = None
        if cuda_version and self.config and self.config.install_path:
            cuda_base_path = Path(self.config.install_path) / "ps_env" / "CUDA"
            cuda_paths = CUDAPaths(str(cuda_base_path))
        
        gpu_config = GPUConfig(
            name=gpu_name,
            generation=generation,
            cuda_version=cuda_version,
            cuda_paths=cuda_paths,
            compute_capability=compute_capability,
            memory_gb=memory_gb,
            recommended_backend=backend,
            supports_tensorrt=supports_tensorrt
        )
        
        # Ensure config exists before setting gpu_config
        if not self.config:
            self._create_default_config()
        if self.config:
            self.config.gpu_config = gpu_config
        return gpu_config
    
    def configure_gpu_from_detection(self) -> GPUConfig:
        """
        Automatically configure GPU settings using GPU detection
        
        Returns:
            GPUConfig object
        """
        from portablesource.get_gpu import GPUDetector
        
        gpu_detector = GPUDetector()
        gpu_info_list = gpu_detector.get_gpu_info()
        
        if not gpu_info_list:
            logger.warning("No GPU detected, using CPU configuration")
            return self.configure_gpu("Unknown GPU", 0)
        
        # Find the best NVIDIA GPU first, then fallback to others
        nvidia_gpus = [gpu for gpu in gpu_info_list if gpu.gpu_type.name == "NVIDIA"]
        
        if nvidia_gpus:
            # Sort by priority (newer GPUs first)
            nvidia_gpus.sort(key=lambda x: gpu_detector._get_gpu_priority(x.name), reverse=True)
            primary_gpu = nvidia_gpus[0]
        else:
            # Use the first available GPU
            primary_gpu = gpu_info_list[0]
        
        # Convert memory from MB to GB
        memory_gb = primary_gpu.memory // 1024 if primary_gpu.memory else 0
        
        #logger.info(f"Detected GPU: {primary_gpu.name} ({memory_gb}GB)")
        #logger.info(f"GPU Type: {primary_gpu.gpu_type.value}")
        if primary_gpu.cuda_version:
            pass
            #logger.info(f"Recommended CUDA: {primary_gpu.cuda_version.value}")
        
        return self.configure_gpu(primary_gpu.name, memory_gb)
    
    def configure_install_path(self, install_path: str) -> str:
        """
        Configure installation path
        
        Args:
            install_path: Base installation path
            
        Returns:
            Configured install path
        """
        install_path = str(Path(install_path))
        # Ensure config exists before setting install_path
        if not self.config:
            self._create_default_config()
        if self.config:
            self.config.install_path = install_path
        
        return install_path
    
    def configure_environment_vars(self) -> Dict[str, str]:
        """
        Configure environment variables
        
        Returns:
            Dictionary of environment variables
        """
        env_vars = {}
        
        # Ensure config exists
        if not self.config:
            self._create_default_config()
            
        if self.config and self.config.install_path:
            # Только базовые временные директории
            tmp_path = str(Path(self.config.install_path) / "tmp")
            env_vars["USERPROFILE"] = tmp_path
            env_vars["TEMP"] = tmp_path
            env_vars["TMP"] = tmp_path
        
        if self.config:
            self.config.environment_vars = env_vars
        return env_vars

    def configure_cuda_paths(self) -> None:
        """
        Configure CUDA paths based on installation path and GPU config
        """
        # logger.info("Starting CUDA paths configuration...")
        # Ensure config exists
        if not self.config:
            # logger.info("Config not found, creating default config...")
            self._create_default_config()
            
        if not self.config or not self.config.install_path:
            logger.error("Installation path not set, cannot configure CUDA paths")
            return
        
        if not self.config.gpu_config or not self.config.gpu_config.cuda_version:
            logger.warning("No CUDA version configured, skipping CUDA paths setup")
            return
        
        # logger.info(f"Configuring CUDA paths for install path: {self.config.install_path}")
        # logger.info(f"CUDA version: {self.config.gpu_config.cuda_version.value}")
        
        # CUDA is installed in ps_env/CUDA directory
        cuda_base_path = Path(self.config.install_path) / "ps_env" / "CUDA"
        # logger.info(f"Setting CUDA base path to: {cuda_base_path}")
        
        # logger.info("Creating CUDAPaths object...")
        self.config.gpu_config.cuda_paths = CUDAPaths(str(cuda_base_path))
        # logger.info(f"CUDA paths configured successfully for version {self.config.gpu_config.cuda_version.value}")
        # logger.info("CUDA paths configuration completed")
    
    def get_cuda_download_link(self, cuda_version: Optional[CUDAVersion] = None) -> Optional[str]:
        """
        Get download link for CUDA package based on version
        
        Args:
            cuda_version: CUDA version to get link for. If None, uses GPU config version
            
        Returns:
            Download link string or None if not available
        """
        if cuda_version is None:
            # Ensure config exists
            if not self.config:
                self._create_default_config()
                
            if self.config and self.config.gpu_config and self.config.gpu_config.cuda_version:
                cuda_version = self.config.gpu_config.cuda_version
            else:
                logger.warning("No CUDA version specified and no GPU config available")
                return None
        
        # Map CUDA versions to download links
        link_mapping = {
            CUDAVersion.CUDA_118: CUDALinks.CUDA_118.value,
            CUDAVersion.CUDA_124: CUDALinks.CUDA_124.value,
            CUDAVersion.CUDA_128: CUDALinks.CUDA_128.value
        }
        
        download_link = link_mapping.get(cuda_version)
        if not download_link:
            logger.warning(f"No download link available for CUDA version {cuda_version.value}")
            return None
        
        return download_link
    
    def _get_compute_capability(self, generation: GPUGeneration) -> str:
        """
        Get approximate compute capability for GPU generation
        
        Args:
            generation: GPU generation
            
        Returns:
            Compute capability string
        """
        capability_map = {
            GPUGeneration.PASCAL: "6.1",
            GPUGeneration.TURING: "7.5",
            GPUGeneration.AMPERE: "8.6",
            GPUGeneration.ADA_LOVELACE: "8.9",
            GPUGeneration.BLACKWELL: "9.0",
            GPUGeneration.UNKNOWN: "5.0"
        }
        return capability_map.get(generation, "5.0")
    
    def save_config(self) -> bool:
        """
        Save configuration to file in install path
        
        Returns:
            True if saved successfully
        """
        try:
            # Ensure config exists
            if not self.config:
                self._create_default_config()
                
            if not self.config:
                logger.error("No configuration to save")
                return False
                
            config_file_path = self._get_config_file_path()
            
            # Ensure directory exists
            config_file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert config to dictionary with enum serialization
            config_dict = {
                "version": self.config.version,
                "install_path": self.config.install_path,
                "gpu_config": {
                    "name": self.config.gpu_config.name,
                    "generation": self.config.gpu_config.generation.value,
                    "cuda_version": self.config.gpu_config.cuda_version.value if self.config.gpu_config.cuda_version else None,
                    "cuda_paths": {
                        "base_path": self.config.gpu_config.cuda_paths.base_path
                    } if self.config.gpu_config.cuda_paths else None,
                    "compute_capability": self.config.gpu_config.compute_capability,
                    "memory_gb": self.config.gpu_config.memory_gb,
                    "recommended_backend": self.config.gpu_config.recommended_backend,
                    "supports_tensorrt": self.config.gpu_config.supports_tensorrt
                } if self.config.gpu_config else None,
                "environment_vars": self.config.environment_vars,
                "environment_setup_completed": self.config.environment_setup_completed
            }
            
            with open(config_file_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            logger.info(f"Configuration saved to: {config_file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return False
    
    def load_config(self) -> bool:
        """
        Load configuration from file in install path
        
        Returns:
            True if loaded successfully
        """
        try:
            config_file_path = self._get_config_file_path()
            
            if not config_file_path.exists():
                logger.info("No configuration file found, creating default configuration")
                self._create_default_config()
                return False
            
            with open(config_file_path, 'r', encoding='utf-8') as f:
                config_dict = json.load(f)
            
            # Reconstruct config object
            self.config = self._dict_to_config(config_dict)
            logger.info(f"Configuration loaded from: {config_file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            # Create default config as fallback
            self._create_default_config()
            return False
    
    def _create_default_config(self):
        """
        Create default configuration without calling __post_init__
        """
        self.config = PortableSourceConfig.__new__(PortableSourceConfig)
        self.config.version = ver
        self.config.install_path = ""
        self.config.gpu_config = None
        self.config.environment_vars = {}
        self.config.environment_setup_completed = False
    
    def _dict_to_config(self, config_dict: Dict[str, Any]) -> PortableSourceConfig:
        """
        Convert dictionary to PortableSourceConfig object
        
        Args:
            config_dict: Configuration dictionary
            
        Returns:
            PortableSourceConfig object
        """
        # Convert enums back from strings
        if 'gpu_config' in config_dict and config_dict['gpu_config']:
            gpu_config = config_dict['gpu_config']
            if 'generation' in gpu_config:
                gpu_config['generation'] = GPUGeneration(gpu_config['generation'])
            if 'cuda_version' in gpu_config and gpu_config['cuda_version'] is not None:
                gpu_config['cuda_version'] = CUDAVersion(gpu_config['cuda_version'])
            if 'cuda_paths' in gpu_config and gpu_config['cuda_paths'] is not None:
                gpu_config['cuda_paths'] = CUDAPaths(gpu_config['cuda_paths']['base_path'])
        
        # Reconstruct config object without calling __post_init__
        config = PortableSourceConfig.__new__(PortableSourceConfig)
        
        # Set default values manually
        config.version = "1.0.0"
        config.install_path = ""
        config.gpu_config = None
        config.environment_vars = {}
        config.environment_setup_completed = False
        
        if 'version' in config_dict:
            config.version = config_dict['version']
        
        if 'install_path' in config_dict:
            config.install_path = config_dict['install_path']
        
        if 'gpu_config' in config_dict and config_dict['gpu_config']:
            config.gpu_config = GPUConfig(**config_dict['gpu_config'])
        
        if 'environment_vars' in config_dict:
            config.environment_vars = config_dict['environment_vars']
        
        if 'environment_setup_completed' in config_dict:
            config.environment_setup_completed = config_dict['environment_setup_completed']
        
        return config
    
    def msvc_bt_config(self):
        msvc_bt_url = "https://aka.ms/vs/17/release/vs_buildtools.exe"
        msvc_bt_args = " --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.NativeDesktop --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.Llvm.Clang"
        return msvc_bt_url, msvc_bt_args

    def is_environment_setup_completed(self) -> bool:
        """
        Check if environment setup has been completed successfully
        
        Returns:
            True if environment setup is completed
        """
        # Ensure config exists
        if not self.config:
            self._create_default_config()
            
        return self.config.environment_setup_completed if self.config else False
    
    def mark_environment_setup_completed(self, completed: bool = True) -> bool:
        """
        Mark environment setup as completed and save config
        
        Args:
            completed: Whether setup is completed
            
        Returns:
            True if saved successfully
        """
        # Ensure config exists
        if not self.config:
            self._create_default_config()
            
        if self.config:
            self.config.environment_setup_completed = completed
            return self.save_config()
        return False

    def get_config_summary(self) -> str:
        """
        Get configuration summary
        
        Returns:
            Configuration summary string
        """
        # Ensure config exists
        if not self.config:
            self._create_default_config()
            
        if not self.config:
            return "Configuration not available"
            
        if self.config.gpu_config:
            gpu_name = self.config.gpu_config.name
            gpu_generation = self.config.gpu_config.generation.value
            cuda_version = self.config.gpu_config.cuda_version.value if self.config.gpu_config.cuda_version else 'None'
            cuda_paths_configured = "Yes" if self.config.gpu_config.cuda_paths else "No"
            compute_capability = self.config.gpu_config.compute_capability
            memory_gb = self.config.gpu_config.memory_gb
            backend = self.config.gpu_config.recommended_backend
            tensorrt_support = self.config.gpu_config.supports_tensorrt
        else:
            gpu_name = "Not configured"
            gpu_generation = "Unknown"
            cuda_version = "None"
            cuda_paths_configured = "No"
            compute_capability = "Unknown"
            memory_gb = 0
            backend = "cpu"
            tensorrt_support = False
        
        env_vars_count = len(self.config.environment_vars) if self.config.environment_vars else 0
        setup_status = "[OK] Completed" if self.config.environment_setup_completed else "[ERROR] Not completed"
        
        summary = f"""
PortableSource Configuration Summary
====================================

Environment Setup: {setup_status}

GPU Configuration:
  Name: {gpu_name}
  Generation: {gpu_generation}
  CUDA Version: {cuda_version}
  CUDA Paths Configured: {cuda_paths_configured}
  Compute Capability: {compute_capability}
  Memory: {memory_gb}GB
  Backend: {backend}
  TensorRT Support: {tensorrt_support}

Install Path: {self.config.install_path}

Environment Variables: {env_vars_count} configured
"""
        return summary.strip()
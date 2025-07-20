#!/usr/bin/env python3
"""
Configuration System for PortableSource

This module manages configuration for GPU detection, CUDA versions, and system paths.
"""

SERVER_DOMAIN = "localhost:5173"

import os
import json
import logging
from pathlib import Path
from sqlite3.dbapi2 import version_info
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum
from portablesource.Version import __version__ as ver

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
    CUDA_118 = "11.8"
    CUDA_124 = "12.4"
    CUDA_128 = "12.8"


@dataclass
class GPUConfig:
    """GPU configuration"""
    name: str = ""
    generation: GPUGeneration = GPUGeneration.UNKNOWN
    cuda_version: Optional[CUDAVersion] = None
    compute_capability: str = ""
    memory_gb: int = 0
    recommended_backend: str = "cpu"
    supports_tensorrt: bool = False
    conda_packages: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.conda_packages is None:
            self.conda_packages = []
        if self.cuda_version is None:
            self.cuda_version = CUDAVersion.CUDA_118


@dataclass
class PortableSourceConfig:
    """Main configuration class"""
    version: str = ver
    install_path: str = ""
    gpu_config: Optional[GPUConfig] = None
    environment_vars: Optional[Dict[str, str]] = None
    
    def __post_init__(self):
        if self.gpu_config is None:
            self.gpu_config = GPUConfig()
        if self.environment_vars is None:
            self.environment_vars = {}


class ConfigManager:
    """Configuration manager for PortableSource"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path.cwd() / "portablesource_config.json"
        self.config = PortableSourceConfig()
        
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
        if self.config.install_path:
            return Path(self.config.install_path) / "portablesource_config.json"
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
                logger.info(f"Detected GPU generation: {generation.value} for {gpu_name}")
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
        Configure GPU settings with new logic
        
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
        
        # Determine conda packages and backend based on new logic
        conda_packages = []
        supports_tensorrt = False
        
        gpu_name_upper = gpu_name.upper()
        if any(keyword in gpu_name_upper for keyword in ["NVIDIA", "GEFORCE", "QUADRO", "TESLA", "RTX", "GTX"]) and generation != GPUGeneration.UNKNOWN:
            # NVIDIA GPU detected with known generation
            if cuda_version:
                # Include CUDA version in package name for proper indexing
                conda_packages.append(f"cudatoolkit={cuda_version.value}")
                conda_packages.append("cudnn")
                
                # Check if CUDA >= 12.4 and generation >= Turing for TensorRT
                if (cuda_version in [CUDAVersion.CUDA_124, CUDAVersion.CUDA_128] and 
                    generation in [GPUGeneration.TURING, GPUGeneration.AMPERE, GPUGeneration.ADA_LOVELACE, GPUGeneration.BLACKWELL]):
                    conda_packages.append("tensorrt")
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
        
        gpu_config = GPUConfig(
            name=gpu_name,
            generation=generation,
            cuda_version=cuda_version,
            compute_capability=compute_capability,
            memory_gb=memory_gb,
            recommended_backend=backend,
            supports_tensorrt=supports_tensorrt,
            conda_packages=conda_packages
        )
        
        self.config.gpu_config = gpu_config
        logger.info(f"Configured GPU: {gpu_config}")
        return gpu_config
    
    def configure_install_path(self, install_path: str) -> str:
        """
        Configure installation path
        
        Args:
            install_path: Base installation path
            
        Returns:
            Configured install path
        """
        install_path = str(Path(install_path))
        self.config.install_path = install_path
        
        logger.info(f"Configured install path: {install_path}")
        return install_path
    
    def configure_environment_vars(self) -> Dict[str, str]:
        """
        Configure environment variables
        
        Returns:
            Dictionary of environment variables
        """
        env_vars = {}
        
        if self.config.install_path:
            # Только базовые временные директории
            tmp_path = str(Path(self.config.install_path) / "tmp")
            env_vars["USERPROFILE"] = tmp_path
            env_vars["TEMP"] = tmp_path
            env_vars["TMP"] = tmp_path
        
        self.config.environment_vars = env_vars
        logger.info(f"Configured basic environment variables: {len(env_vars)} variables")
        return env_vars
    
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
                    "compute_capability": self.config.gpu_config.compute_capability,
                    "memory_gb": self.config.gpu_config.memory_gb,
                    "recommended_backend": self.config.gpu_config.recommended_backend,
                    "supports_tensorrt": self.config.gpu_config.supports_tensorrt,
                    "conda_packages": self.config.gpu_config.conda_packages
                } if self.config.gpu_config else None,
                "environment_vars": self.config.environment_vars
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
                logger.info("No configuration file found, using defaults")
                return False
            
            with open(config_file_path, 'r', encoding='utf-8') as f:
                config_dict = json.load(f)
            
            # Reconstruct config object
            self.config = self._dict_to_config(config_dict)
            logger.info(f"Configuration loaded from: {config_file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            return False
    
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
        
        # Reconstruct config object
        config = PortableSourceConfig()
        
        if 'version' in config_dict:
            config.version = config_dict['version']
        
        if 'install_path' in config_dict:
            config.install_path = config_dict['install_path']
        
        if 'gpu_config' in config_dict and config_dict['gpu_config']:
            config.gpu_config = GPUConfig(**config_dict['gpu_config'])
        
        if 'environment_vars' in config_dict:
            config.environment_vars = config_dict['environment_vars']
        
        return config
    

    
    def get_config_summary(self) -> str:
        """
        Get configuration summary
        
        Returns:
            Configuration summary string
        """
        if self.config.gpu_config:
            conda_packages = ", ".join(self.config.gpu_config.conda_packages) if self.config.gpu_config.conda_packages else "None"
            gpu_name = self.config.gpu_config.name
            gpu_generation = self.config.gpu_config.generation.value
            cuda_version = self.config.gpu_config.cuda_version.value if self.config.gpu_config.cuda_version else 'None'
            compute_capability = self.config.gpu_config.compute_capability
            memory_gb = self.config.gpu_config.memory_gb
            backend = self.config.gpu_config.recommended_backend
            tensorrt_support = self.config.gpu_config.supports_tensorrt
        else:
            conda_packages = "None"
            gpu_name = "Not configured"
            gpu_generation = "Unknown"
            cuda_version = "None"
            compute_capability = "Unknown"
            memory_gb = 0
            backend = "cpu"
            tensorrt_support = False
        
        env_vars_count = len(self.config.environment_vars) if self.config.environment_vars else 0
        
        summary = f"""
PortableSource Configuration Summary
====================================

GPU Configuration:
  Name: {gpu_name}
  Generation: {gpu_generation}
  CUDA Version: {cuda_version}
  Compute Capability: {compute_capability}
  Memory: {memory_gb}GB
  Backend: {backend}
  TensorRT Support: {tensorrt_support}
  Conda Packages: {conda_packages}

Install Path: {self.config.install_path}

Environment Variables: {env_vars_count} configured
"""
        return summary.strip()
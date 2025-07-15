#!/usr/bin/env python3
"""
Configuration System for PortableSource

This module manages configuration for GPU detection, CUDA versions, and system paths.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from enum import Enum

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
class CUDAPaths:
    """CUDA installation paths configuration"""
    base_path: str = ""
    bin_paths: List[str] = None
    lib_paths: List[str] = None
    include_paths: List[str] = None
    
    def __post_init__(self):
        if self.bin_paths is None:
            self.bin_paths = []
        if self.lib_paths is None:
            self.lib_paths = []
        if self.include_paths is None:
            self.include_paths = []


@dataclass
class GPUConfig:
    """GPU configuration"""
    name: str = ""
    generation: GPUGeneration = GPUGeneration.UNKNOWN
    cuda_version: CUDAVersion = CUDAVersion.CUDA_118
    compute_capability: str = ""
    memory_gb: int = 0
    recommended_backend: str = "cpu"


@dataclass
class SystemPaths:
    """System paths configuration"""
    install_path: str = ""
    python_path: str = ""
    git_path: str = ""
    ffmpeg_path: str = ""
    cuda_paths: CUDAPaths = None
    
    def __post_init__(self):
        if self.cuda_paths is None:
            self.cuda_paths = CUDAPaths()


@dataclass
class PortableSourceConfig:
    """Main configuration class"""
    version: str = "1.1.0"
    gpu_config: GPUConfig = None
    system_paths: SystemPaths = None
    environment_vars: Dict[str, str] = None
    
    def __post_init__(self):
        if self.gpu_config is None:
            self.gpu_config = GPUConfig()
        if self.system_paths is None:
            self.system_paths = SystemPaths()
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
        
        # CUDA version mapping (исправлено: CUDA 12.4 от RTX 20xx серии)
        self.cuda_mapping = {
            GPUGeneration.PASCAL: CUDAVersion.CUDA_118,  # GTX 10xx серии
            GPUGeneration.TURING: CUDAVersion.CUDA_124,  # RTX 20xx и GTX 16xx
            GPUGeneration.AMPERE: CUDAVersion.CUDA_124,
            GPUGeneration.ADA_LOVELACE: CUDAVersion.CUDA_128,
            GPUGeneration.BLACKWELL: CUDAVersion.CUDA_128,
            GPUGeneration.UNKNOWN: CUDAVersion.CUDA_118
        }
        
        # Default CUDA paths structure (базовые пути CUDA)
        self.default_cuda_paths = {
            "bin_paths": [
                "CUDA/bin",
                "CUDA/libnvvp",
                "CUDA/nvvm/bin"
            ],
            "lib_paths": [
                "CUDA/lib/x64",
                "CUDA/lib",
                "CUDA/nvvm/lib/x64"
            ],
            "include_paths": [
                "CUDA/include",
                "CUDA/nvvm/include"
            ]
        }
        
        # Полный список всех CUDA компонентов (включая cuDNN и TensorRT)
        self.complete_cuda_paths = {
            "bin_paths": [
                # Основные CUDA исполняемые файлы и DLL
                "CUDA/bin",
                "CUDA/libnvvp", 
                "CUDA/nvvm/bin",
                # Compute Sanitizer
                "CUDA/compute-sanitizer"
            ],
            "lib_paths": [
                # Основные библиотеки CUDA
                "CUDA/lib/x64",
                "CUDA/lib",
                "CUDA/lib/Win32",
                "CUDA/nvvm/lib/x64",
                "CUDA/nvvm/lib"
            ],
            "include_paths": [
                # Заголовочные файлы CUDA, cuDNN, TensorRT
                "CUDA/include",
                "CUDA/nvvm/include"
            ]
        }
        
        # Специфичные DLL файлы для разных компонентов
        self.cuda_dll_files = {
            # Основные CUDA DLL
            "cuda_core": [
                "cudart64_12.dll",
                "cuinj64_128.dll",
                "nvfatbin_120_0.dll",
                "nvJitLink_120_0.dll",
                "nvrtc-builtins64_128.dll",
                "nvrtc64_120_0.dll"
            ],
            # cuBLAS
            "cublas": [
                "cublas64_12.dll",
                "cublasLt64_12.dll",
                "nvblas64_12.dll"
            ],
            # cuDNN (всегда включены)
            "cudnn": [
                "cudnn64_9.dll",
                "cudnn_adv64_9.dll", 
                "cudnn_cnn64_9.dll",
                "cudnn_engines_precompiled64_9.dll",
                "cudnn_engines_runtime_compiled64_9.dll",
                "cudnn_graph64_9.dll",
                "cudnn_heuristic64_9.dll",
                "cudnn_ops64_9.dll"
            ],
            # TensorRT (только для RTX 20xx+)
            "tensorrt": [
                "nvinfer_10.dll",
                "nvinfer_builder_resource_10.dll",
                "nvinfer_dispatch_10.dll",
                "nvinfer_lean_10.dll",
                "nvinfer_plugin_10.dll",
                "nvinfer_vc_plugin_10.dll",
                "nvonnxparser_10.dll",
                "trtexec.exe"
            ],
            # cuFFT
            "cufft": [
                "cufft64_11.dll",
                "cufftw64_11.dll"
            ],
            # cuRAND
            "curand": [
                "curand64_10.dll"
            ],
            # cuSolver
            "cusolver": [
                "cusolver64_11.dll",
                "cusolverMg64_11.dll"
            ],
            # cuSparse
            "cusparse": [
                "cusparse64_12.dll"
            ],
            # NPP (NVIDIA Performance Primitives)
            "npp": [
                "nppc64_12.dll",
                "nppial64_12.dll",
                "nppicc64_12.dll", 
                "nppidei64_12.dll",
                "nppif64_12.dll",
                "nppig64_12.dll",
                "nppim64_12.dll",
                "nppist64_12.dll",
                "nppisu64_12.dll",
                "nppitc64_12.dll",
                "npps64_12.dll"
            ],
            # nvJPEG
            "nvjpeg": [
                "nvjpeg64_12.dll"
            ]
        }
        
        # Библиотеки (.lib файлы)
        self.cuda_lib_files = {
            "cuda_core": [
                "cuda.lib",
                "cudadevrt.lib", 
                "cudart.lib",
                "cudart_static.lib",
                "nvrtc.lib",
                "nvJitLink.lib"
            ],
            "cudnn": [
                "cudnn.lib",
                "cudnn64_9.lib",
                "cudnn_adv.lib",
                "cudnn_adv64_9.lib",
                "cudnn_cnn.lib", 
                "cudnn_cnn64_9.lib",
                "cudnn_engines_precompiled.lib",
                "cudnn_engines_precompiled64_9.lib",
                "cudnn_engines_runtime_compiled.lib",
                "cudnn_engines_runtime_compiled64_9.lib",
                "cudnn_graph.lib",
                "cudnn_graph64_9.lib",
                "cudnn_heuristic.lib",
                "cudnn_heuristic64_9.lib",
                "cudnn_ops.lib",
                "cudnn_ops64_9.lib"
            ],
            "tensorrt": [
                "nvinfer_10.lib",
                "nvinfer_dispatch_10.lib",
                "nvinfer_lean_10.lib",
                "nvinfer_plugin_10.lib",
                "nvinfer_vc_plugin_10.lib",
                "nvonnxparser_10.lib"
            ]
        }
        
        # Заголовочные файлы
        self.cuda_header_files = {
            "cuda_core": [
                "cuda.h",
                "cuda_runtime.h",
                "cuda_runtime_api.h",
                "driver_types.h",
                "vector_types.h"
            ],
            "cudnn": [
                "cudnn.h",
                "cudnn_adv.h",
                "cudnn_backend.h",
                "cudnn_cnn.h",
                "cudnn_graph.h",
                "cudnn_ops.h",
                "cudnn_version.h"
            ],
            "tensorrt": [
                "NvInfer.h",
                "NvInferImpl.h",
                "NvInferLegacyDims.h",
                "NvInferPlugin.h",
                "NvInferPluginBase.h",
                "NvInferPluginUtils.h",
                "NvInferRuntime.h",
                "NvInferRuntimeBase.h",
                "NvInferRuntimeCommon.h",
                "NvInferRuntimePlugin.h",
                "NvInferVersion.h",
                "NvOnnxConfig.h",
                "NvOnnxParser.h"
            ]
        }
    
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
    
    def get_recommended_cuda_version(self, generation: GPUGeneration) -> CUDAVersion:
        """
        Get recommended CUDA version for GPU generation
        
        Args:
            generation: GPU generation
            
        Returns:
            CUDAVersion enum
        """
        return self.cuda_mapping.get(generation, CUDAVersion.CUDA_118)
    
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
        
        # Determine compute capability (approximate)
        compute_capability = self._get_compute_capability(generation)
        
        # Determine recommended backend
        gpu_name_upper = gpu_name.upper()
        if any(keyword in gpu_name_upper for keyword in ["NVIDIA", "GEFORCE", "QUADRO", "TESLA", "RTX", "GTX"]):
            backend = "cuda"
        elif any(brand in gpu_name_upper for brand in ["AMD", "RADEON", "RX "]):
            backend = "directml"
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
            recommended_backend=backend
        )
        
        self.config.gpu_config = gpu_config
        logger.info(f"Configured GPU: {gpu_config}")
        return gpu_config
    
    def add_cuda_component_paths(self, component_paths: Dict[str, List[str]]) -> None:
        """
        Add additional CUDA component paths (cuDNN, TensorRT, etc.)
        
        Args:
            component_paths: Dictionary with 'bin_paths', 'lib_paths', 'include_paths'
        """
        if not self.config.system_paths or not self.config.system_paths.cuda_paths:
            logger.warning("CUDA paths not configured, cannot add component paths")
            return
        
        install_base = Path(self.config.system_paths.install_path) / "system"
        
        # Add bin paths
        if 'bin_paths' in component_paths:
            for path in component_paths['bin_paths']:
                full_path = str(install_base / path)
                if full_path not in self.config.system_paths.cuda_paths.bin_paths:
                    self.config.system_paths.cuda_paths.bin_paths.append(full_path)
        
        # Add lib paths
        if 'lib_paths' in component_paths:
            for path in component_paths['lib_paths']:
                full_path = str(install_base / path)
                if full_path not in self.config.system_paths.cuda_paths.lib_paths:
                    self.config.system_paths.cuda_paths.lib_paths.append(full_path)
        
        # Add include paths
        if 'include_paths' in component_paths:
            for path in component_paths['include_paths']:
                full_path = str(install_base / path)
                if full_path not in self.config.system_paths.cuda_paths.include_paths:
                    self.config.system_paths.cuda_paths.include_paths.append(full_path)
        
        logger.info(f"Added CUDA component paths: {component_paths}")
    
    def _configure_cuda_components(self) -> None:
        """
        Автоматически настраивает CUDA компоненты в зависимости от GPU
        """
        if not self.config.gpu_config or not self.config.system_paths:
            return
        
        generation = self.config.gpu_config.generation
        
        # Используем полные пути для всех компонентов
        self.add_cuda_component_paths(self.complete_cuda_paths)
        logger.info("Added complete CUDA paths (including cuDNN)")
        
        # Проверяем поддержку TensorRT (только для RTX 20xx и выше)
        if generation in [
            GPUGeneration.TURING, GPUGeneration.AMPERE, 
            GPUGeneration.ADA_LOVELACE, GPUGeneration.BLACKWELL
        ]:
            logger.info("TensorRT support enabled (RTX 20xx+ GPU detected)")
            # TensorRT файлы уже включены в complete_cuda_paths
        else:
            logger.info("TensorRT not supported for this GPU generation")
    
    def get_cuda_components_for_generation(self, generation: GPUGeneration) -> List[str]:
        """
        Получить список CUDA компонентов для конкретного поколения GPU
        
        Args:
            generation: Поколение GPU
            
        Returns:
            Список компонентов
        """
        base_components = ["cuda_core", "cublas", "cudnn", "cufft", "curand", 
                          "cusolver", "cusparse", "npp", "nvjpeg"]
        
        # TensorRT только для RTX 20xx и выше
        if generation in [
            GPUGeneration.TURING, GPUGeneration.AMPERE, 
            GPUGeneration.ADA_LOVELACE, GPUGeneration.BLACKWELL
        ]:
            base_components.append("tensorrt")
        
        return base_components
    
    def get_required_dll_files(self, generation: GPUGeneration) -> List[str]:
        """
        Получить список необходимых DLL файлов для поколения GPU
        
        Args:
            generation: Поколение GPU
            
        Returns:
            Список DLL файлов
        """
        components = self.get_cuda_components_for_generation(generation)
        dll_files = []
        
        for component in components:
            if component in self.cuda_dll_files:
                dll_files.extend(self.cuda_dll_files[component])
        
        return dll_files
    
    def get_required_lib_files(self, generation: GPUGeneration) -> List[str]:
        """
        Получить список необходимых LIB файлов для поколения GPU
        
        Args:
            generation: Поколение GPU
            
        Returns:
            Список LIB файлов
        """
        components = self.get_cuda_components_for_generation(generation)
        lib_files = []
        
        for component in components:
            if component in self.cuda_lib_files:
                lib_files.extend(self.cuda_lib_files[component])
        
        return lib_files
    
    def get_required_header_files(self, generation: GPUGeneration) -> List[str]:
        """
        Получить список необходимых заголовочных файлов для поколения GPU
        
        Args:
            generation: Поколение GPU
            
        Returns:
            Список заголовочных файлов
        """
        components = self.get_cuda_components_for_generation(generation)
        header_files = []
        
        for component in components:
            if component in self.cuda_header_files:
                header_files.extend(self.cuda_header_files[component])
        
        return header_files
    
    def configure_system_paths(self, install_path: str) -> SystemPaths:
        """
        Configure system paths for new architecture
        
        Args:
            install_path: Base installation path
            
        Returns:
            SystemPaths object
        """
        install_path = Path(install_path)
        
        # New architecture paths - все в conda окружении
        miniconda_path = install_path / "miniconda"
        conda_env_path = miniconda_path / "envs" / "portablesource"
        
        python_path = str(conda_env_path / "python.exe")
        git_path = str(conda_env_path / "Scripts")  # git будет в conda окружении
        ffmpeg_path = str(conda_env_path / "bin")   # ffmpeg в conda окружении
        
        # CUDA paths - в conda окружении
        cuda_base = str(conda_env_path / "lib" / "site-packages" / "nvidia")
        cuda_paths = CUDAPaths(
            base_path=cuda_base,
            bin_paths=[str(conda_env_path / "bin")],
            lib_paths=[str(conda_env_path / "lib")],
            include_paths=[str(conda_env_path / "include")]
        )
        
        system_paths = SystemPaths(
            install_path=str(install_path),
            python_path=python_path,
            git_path=git_path,
            ffmpeg_path=ffmpeg_path,
            cuda_paths=cuda_paths
        )
        
        self.config.system_paths = system_paths
        
        logger.info(f"Configured system paths for new architecture: {system_paths}")
        return system_paths
    
    def configure_environment_vars(self) -> Dict[str, str]:
        """
        Configure environment variables
        
        Returns:
            Dictionary of environment variables
        """
        env_vars = {}
        
        if self.config.system_paths and self.config.gpu_config:
            # Basic paths
            paths = [
                self.config.system_paths.git_path,
                self.config.system_paths.python_path,
                f"{self.config.system_paths.python_path}/Scripts",
                self.config.system_paths.ffmpeg_path
            ]
            
            # Add CUDA paths if NVIDIA GPU
            if self.config.gpu_config.recommended_backend == "cuda":
                paths.extend(self.config.system_paths.cuda_paths.bin_paths)
                paths.extend(self.config.system_paths.cuda_paths.lib_paths)
                
                # CUDA-specific environment variables
                env_vars["CUDA_PATH"] = self.config.system_paths.cuda_paths.base_path
                env_vars["CUDA_MODULE_LOADING"] = "LAZY"
                
                # Version-specific CUDA paths
                if self.config.gpu_config.cuda_version == CUDAVersion.CUDA_118:
                    env_vars["CUDA_PATH_V11_8"] = self.config.system_paths.cuda_paths.base_path
                elif self.config.gpu_config.cuda_version == CUDAVersion.CUDA_124:
                    env_vars["CUDA_PATH_V12_4"] = self.config.system_paths.cuda_paths.base_path
                elif self.config.gpu_config.cuda_version == CUDAVersion.CUDA_128:
                    env_vars["CUDA_PATH_V12_8"] = self.config.system_paths.cuda_paths.base_path
            
            # Temporary directories
            tmp_path = str(Path(self.config.system_paths.install_path) / "tmp")
            env_vars["USERPROFILE"] = tmp_path
            env_vars["TEMP"] = tmp_path
            env_vars["TMP"] = tmp_path
            
            # PATH variable
            env_vars["PATH"] = ";".join(paths)
        
        self.config.environment_vars = env_vars
        logger.info(f"Configured environment variables: {len(env_vars)} variables")
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
        Save configuration to file
        
        Returns:
            True if saved successfully
        """
        try:
            config_dict = asdict(self.config)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            logger.info(f"Configuration saved to: {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save configuration: {e}")
            return False
    
    def load_config(self) -> bool:
        """
        Load configuration from file
        
        Returns:
            True if loaded successfully
        """
        try:
            if not self.config_path.exists():
                logger.info("No configuration file found, using defaults")
                return False
            
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_dict = json.load(f)
            
            # Reconstruct config object
            self.config = self._dict_to_config(config_dict)
            logger.info(f"Configuration loaded from: {self.config_path}")
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
            if 'cuda_version' in gpu_config:
                gpu_config['cuda_version'] = CUDAVersion(gpu_config['cuda_version'])
        
        # Reconstruct nested objects
        config = PortableSourceConfig()
        
        if 'version' in config_dict:
            config.version = config_dict['version']
        
        if 'gpu_config' in config_dict and config_dict['gpu_config']:
            config.gpu_config = GPUConfig(**config_dict['gpu_config'])
        
        if 'system_paths' in config_dict and config_dict['system_paths']:
            system_paths_dict = config_dict['system_paths']
            if 'cuda_paths' in system_paths_dict and system_paths_dict['cuda_paths']:
                cuda_paths = CUDAPaths(**system_paths_dict['cuda_paths'])
                system_paths_dict['cuda_paths'] = cuda_paths
            config.system_paths = SystemPaths(**system_paths_dict)
        
        if 'environment_vars' in config_dict:
            config.environment_vars = config_dict['environment_vars']
        
        return config
    
    def generate_bat_file(self, output_path: Path, app_command: str) -> bool:
        """
        Generate Windows batch file with proper environment setup
        
        Args:
            output_path: Path to save the batch file
            app_command: Command to run the application
            
        Returns:
            True if generated successfully
        """
        try:
            if not self.config.system_paths or not self.config.environment_vars:
                logger.error("Configuration not complete, cannot generate batch file")
                return False
            
            # Generate batch file content
            bat_content = self._generate_bat_content(app_command)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(bat_content)
            
            logger.info(f"Batch file generated: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to generate batch file: {e}")
            return False
    
    def _generate_bat_content(self, app_command: str) -> str:
        """
        Generate batch file content
        
        Args:
            app_command: Command to run the application
            
        Returns:
            Batch file content
        """
        install_path = self.config.system_paths.install_path
        
        bat_content = f"""@echo off
setlocal enabledelayedexpansion

REM Clean up temporary directories
for /d %%i in (tmp\\tmp*,tmp\\pip*) do rd /s /q "%%i" 2>nul
del /q tmp\\tmp* > nul 2>&1
rd /s /q pip\\cache 2>nul

REM Set environment variables
set "USERPROFILE={install_path}\\tmp"
set "TEMP={install_path}\\tmp"
set "TMP={install_path}\\tmp"

REM Set PATH with all required directories
set "PATH={self.config.environment_vars.get('PATH', '')}"

"""
        
        # Add CUDA-specific variables if needed
        if self.config.gpu_config.recommended_backend == "cuda":
            bat_content += f"""REM CUDA Environment Variables
set "CUDA_MODULE_LOADING=LAZY"
set "CUDA_PATH={self.config.system_paths.cuda_paths.base_path}"
"""
            
            if self.config.gpu_config.cuda_version == CUDAVersion.CUDA_118:
                bat_content += f'set "CUDA_PATH_V11_8={self.config.system_paths.cuda_paths.base_path}"\n'
            elif self.config.gpu_config.cuda_version == CUDAVersion.CUDA_124:
                bat_content += f'set "CUDA_PATH_V12_4={self.config.system_paths.cuda_paths.base_path}"\n'
            elif self.config.gpu_config.cuda_version == CUDAVersion.CUDA_128:
                bat_content += f'set "CUDA_PATH_V12_8={self.config.system_paths.cuda_paths.base_path}"\n'
        
        bat_content += f"""
REM Run the application
{app_command}

pause
endlocal
REM Generated by PortableSource v{self.config.version}
"""
        
        return bat_content
    
    def get_config_summary(self) -> str:
        """
        Get configuration summary
        
        Returns:
            Configuration summary string
        """
        summary = f"""
PortableSource Configuration Summary
====================================

GPU Configuration:
  Name: {self.config.gpu_config.name}
  Generation: {self.config.gpu_config.generation.value}
  CUDA Version: {self.config.gpu_config.cuda_version.value}
  Compute Capability: {self.config.gpu_config.compute_capability}
  Memory: {self.config.gpu_config.memory_gb}GB
  Backend: {self.config.gpu_config.recommended_backend}

System Paths:
  Install Path: {self.config.system_paths.install_path}
  Python: {self.config.system_paths.python_path}
  Git: {self.config.system_paths.git_path}
  FFmpeg: {self.config.system_paths.ffmpeg_path}
  CUDA Base: {self.config.system_paths.cuda_paths.base_path}

Environment Variables: {len(self.config.environment_vars)} configured
"""
        return summary.strip()


# Main execution for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test configuration
    config_manager = ConfigManager()
    
    # Configure for RTX 4090
    config_manager.configure_gpu("NVIDIA GeForce RTX 4090", memory_gb=24)
    config_manager.configure_system_paths("C:\\portablesource")
    config_manager.configure_environment_vars()
    
    # Print summary
    print(config_manager.get_config_summary())
    
    # Save configuration
    config_manager.save_config()
    
    # Generate example batch file
    bat_path = Path("example_start.bat")
    config_manager.generate_bat_file(bat_path, "python\\python.exe app.py")
    
    print(f"\nExample batch file generated: {bat_path}") 
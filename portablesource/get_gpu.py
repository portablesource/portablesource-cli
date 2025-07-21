#!/usr/bin/env python3
"""
GPU Detection Module for PortableSource

This module provides functionality to detect and identify GPU hardware
on Windows and Linux systems for AI application compatibility.
"""

import subprocess
import platform
import logging
import re
import os
from typing import Optional, List
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GPUType(Enum):
    """Enumeration of supported GPU types"""
    NVIDIA = "NVIDIA"
    AMD = "AMD"
    INTEL = "INTEL"
    DIRECTML = "DIRECTML"  # For AMD/Intel on Windows
    UNKNOWN = "UNKNOWN"


class CUDAVersion(Enum):
    """CUDA versions based on GPU generation"""
    CUDA_118 = "11.8"  # For GTX 10xx (Pascal)
    CUDA_124 = "12.4"  # For GTX 16xx, RTX 20xx, RTX 30xx (Turing/Ampere)
    CUDA_128 = "12.8"  # For RTX 40xx, RTX 50xx (Ada Lovelace/Blackwell)


class GPUInfo:
    """Class to hold GPU information"""
    def __init__(self, name: str, gpu_type: GPUType, memory: Optional[int] = None, 
                 compute_capability: Optional[str] = None, cuda_version: Optional[CUDAVersion] = None):
        self.name = name
        self.gpu_type = gpu_type
        self.memory = memory  # VRAM in MB
        self.compute_capability = compute_capability
        self.cuda_version = cuda_version
    
    def __repr__(self):
        return f"GPUInfo(name='{self.name}', type={self.gpu_type.value}, memory={self.memory}MB, cuda={self.cuda_version.value if self.cuda_version else 'None'})"


class GPUDetector:
    """Main GPU detection class"""
    
    def __init__(self):
        self.system = platform.system()
    
    def get_gpu_info(self) -> List[GPUInfo]:
        """
        Get detailed information about all available GPUs
        
        Returns:
            List of GPUInfo objects
        """
        if self.system == "Windows":
            return self._get_windows_gpu_info()
        elif self.system == "Linux":
            return self._get_linux_gpu_info()
        else:
            logger.warning(f"Unsupported system: {self.system}")
            return []
    
    def get_primary_gpu_type(self) -> GPUType:
        """
        Get the primary GPU type for AI acceleration
        
        Returns:
            GPUType enum value
        """
        gpu_info_list = self.get_gpu_info()
        
        if not gpu_info_list:
            logger.warning("No GPU detected")
            return GPUType.UNKNOWN
        
        # Prioritize NVIDIA for AI workloads
        for gpu in gpu_info_list:
            if gpu.gpu_type == GPUType.NVIDIA:
                return GPUType.NVIDIA
        
        # Then AMD
        for gpu in gpu_info_list:
            if gpu.gpu_type == GPUType.AMD:
                return GPUType.AMD if self.system == "Linux" else GPUType.DIRECTML
        
        # Then Intel
        for gpu in gpu_info_list:
            if gpu.gpu_type == GPUType.INTEL:
                return GPUType.INTEL if self.system == "Linux" else GPUType.DIRECTML
        
        # Fallback
        return GPUType.UNKNOWN
    
    def get_recommended_cuda_version(self) -> Optional[CUDAVersion]:
        """
        Get recommended CUDA version based on GPU
        
        Returns:
            CUDAVersion enum value or None
        """
        gpu_info_list = self.get_gpu_info()
        
        # Find the best NVIDIA GPU
        nvidia_gpus = [gpu for gpu in gpu_info_list if gpu.gpu_type == GPUType.NVIDIA]
        
        if not nvidia_gpus:
            return None
        
        # Sort by preference (newer GPUs first)
        nvidia_gpus.sort(key=lambda x: self._get_gpu_priority(x.name), reverse=True)
        primary_gpu = nvidia_gpus[0]
        
        return primary_gpu.cuda_version
    
    def _get_windows_gpu_info(self) -> List[GPUInfo]:
        """Get GPU information on Windows using nvidia-smi first, then fallback to wmic/powershell"""
        gpu_list = []
        
        # First try nvidia-smi for NVIDIA GPUs (most reliable)
        nvidia_gpus = self._get_nvidia_gpus_via_smi()
        gpu_list.extend(nvidia_gpus)
        
        # ВСЕГДА пытаемся найти остальные GPU через wmic/PowerShell (AMD, Intel, встроенные)
        other_gpus = self._get_other_gpus_windows()
        
        # Фильтруем дубликаты NVIDIA GPU из wmic результатов
        nvidia_names = {gpu.name.lower() for gpu in nvidia_gpus}
        for gpu in other_gpus:
            # Если это не NVIDIA GPU или мы его еще не нашли через nvidia-smi
            if gpu.gpu_type != GPUType.NVIDIA or gpu.name.lower() not in nvidia_names:
                gpu_list.append(gpu)
        
        # Если все еще нет GPU, добавляем fallback
        if not gpu_list:
            logger.warning("No GPU detected via standard methods, adding fallback entry")
            # Try to detect if we have any NVIDIA/AMD related environment variables
            if any(env_var in os.environ for env_var in ["CUDA_PATH", "CUDA_HOME", "CUDA_ROOT"]):
                gpu_list.append(GPUInfo("NVIDIA GPU (Detected via CUDA environment)", GPUType.NVIDIA, cuda_version=CUDAVersion.CUDA_118))
            elif any(env_var in os.environ for env_var in ["HIP_PATH", "ROCM_PATH"]):
                gpu_list.append(GPUInfo("AMD GPU (Detected via ROCm environment)", GPUType.AMD))
            else:
                gpu_list.append(GPUInfo("Integrated Graphics", GPUType.UNKNOWN))
        
        return gpu_list
    
    def _get_nvidia_gpus_via_smi(self) -> List[GPUInfo]:
        """Get NVIDIA GPU information using nvidia-smi"""
        gpu_list = []
        
        try:
            # Try nvidia-smi with CSV output for detailed info
            output = subprocess.check_output([
                "nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"
            ], stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", errors="ignore")
            
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            
            for line in lines:
                parts = line.split(', ')
                if len(parts) >= 2:
                    name = parts[0].strip()
                    try:
                        memory_mb = int(parts[1].strip())
                    except ValueError:
                        memory_mb = None
                    
                    cuda_version = self._get_cuda_version_for_gpu(name)
                    gpu_list.append(GPUInfo(name, GPUType.NVIDIA, memory_mb, cuda_version=cuda_version))
            
        except (subprocess.CalledProcessError, FileNotFoundError):
            # nvidia-smi not available or no NVIDIA GPUs
            pass
        except Exception as e:
            logger.error(f"Error running nvidia-smi: {e}")
        
        return gpu_list
    
    def _get_other_gpus_windows(self) -> List[GPUInfo]:
        """Get non-NVIDIA GPU information using wmic or PowerShell"""
        gpu_list = []
        
        try:
            # First try wmic with proper column order
            output = subprocess.check_output([
                "wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM", "/format:csv"
            ], stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", errors="ignore")
            
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            
            # Skip header line and empty lines
            for line in lines[1:]:
                if not line or line.count(',') < 2:
                    continue
                
                # CSV format: Node,AdapterRAM,Name
                parts = line.split(',')
                if len(parts) >= 3:
                    try:
                        adapter_ram = parts[1].strip()
                        name = parts[2].strip()
                        
                        if not name or name == "":
                            continue
                        
                        # Convert memory from bytes to MB
                        memory = None
                        if adapter_ram and adapter_ram.isdigit():
                            memory = int(adapter_ram) // (1024 * 1024)
                        
                        gpu_type = self._classify_gpu(name)
                        cuda_version = self._get_cuda_version_for_gpu(name) if gpu_type == GPUType.NVIDIA else None
                        
                        gpu_list.append(GPUInfo(name, gpu_type, memory, cuda_version=cuda_version))
                        
                    except (ValueError, IndexError):
                        continue
                        
        except (subprocess.CalledProcessError, FileNotFoundError):
            # If wmic fails, try PowerShell
            try:
                powershell_cmd = [
                    "powershell", "-Command",
                    "Get-WmiObject -Class Win32_VideoController | Where-Object {$_.Name -ne $null} | Select-Object Name,AdapterRAM | ConvertTo-Csv -NoTypeInformation"
                ]
                output = subprocess.check_output(powershell_cmd, stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", errors="ignore")
                
                lines = [line.strip() for line in output.split('\n') if line.strip()]
                
                # Skip header line
                for line in lines[1:]:
                    if not line or line.count(',') < 1:
                        continue
                    
                    # CSV format: "Name","AdapterRAM"
                    parts = [part.strip('"') for part in line.split(',')]
                    if len(parts) >= 2:
                        try:
                            name = parts[0].strip()
                            adapter_ram = parts[1].strip()
                            
                            if not name or name == "":
                                continue
                            
                            # Convert memory from bytes to MB
                            memory = None
                            if adapter_ram and adapter_ram.isdigit():
                                memory = int(adapter_ram) // (1024 * 1024)
                            
                            gpu_type = self._classify_gpu(name)
                            cuda_version = self._get_cuda_version_for_gpu(name) if gpu_type == GPUType.NVIDIA else None
                            
                            gpu_list.append(GPUInfo(name, gpu_type, memory, cuda_version=cuda_version))
                            
                        except (ValueError, IndexError):
                            continue
                            
            except Exception:
                # If both fail, return empty list
                logger.error("Both wmic and PowerShell GPU detection failed")
                return gpu_list
        
        return gpu_list
    
    def _get_linux_gpu_info(self) -> List[GPUInfo]:
        """Get GPU information on Linux using nvidia-smi first, then lspci"""
        gpu_list = []
        
        # First try nvidia-smi for NVIDIA GPUs
        nvidia_gpus = self._get_nvidia_gpus_via_smi()
        gpu_list.extend(nvidia_gpus)
        
        # ВСЕГДА пытаемся lspci для всех остальных GPU (AMD, Intel, встроенные)
        try:
            output = subprocess.check_output([
                "lspci", "-nn"
            ], stderr=subprocess.DEVNULL, timeout=10).decode("utf-8", errors="ignore")
            
            # Look for VGA and 3D controllers
            gpu_lines = [line for line in output.split('\n') 
                        if re.search(r'VGA|3D|Display', line, re.IGNORECASE)]
            
            # Фильтруем дубликаты NVIDIA GPU из lspci результатов
            nvidia_names = {gpu.name.lower() for gpu in nvidia_gpus}
            
            for line in gpu_lines:
                # Extract GPU name from lspci output
                match = re.search(r':\s*(.+?)\s*\[', line)
                if match:
                    name = match.group(1).strip()
                    gpu_type = self._classify_gpu(name)
                    
                    # Если это не NVIDIA GPU или мы его еще не нашли через nvidia-smi
                    if gpu_type != GPUType.NVIDIA or name.lower() not in nvidia_names:
                        cuda_version = self._get_cuda_version_for_gpu(name) if gpu_type == GPUType.NVIDIA else None
                        gpu_list.append(GPUInfo(name, gpu_type, cuda_version=cuda_version))
            
        except subprocess.TimeoutExpired:
            logger.error("GPU detection timed out")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get GPU info via lspci: {e}")
        except FileNotFoundError:
            logger.error("lspci command not found")
        except Exception as e:
            logger.error(f"Unexpected error in Linux GPU detection: {e}")
        
        return gpu_list
    
    def _classify_gpu(self, gpu_name: str) -> GPUType:
        """
        Classify GPU type based on name
        
        Args:
            gpu_name: Name of the GPU
            
        Returns:
            GPUType enum value
        """
        gpu_name_upper = gpu_name.upper()
        
        if any(keyword in gpu_name_upper for keyword in ["NVIDIA", "GEFORCE", "QUADRO", "TESLA", "RTX", "GTX"]):
            return GPUType.NVIDIA
        elif any(keyword in gpu_name_upper for keyword in ["AMD", "RADEON", "RX ", "VEGA", "NAVI"]):
            return GPUType.AMD
        elif any(keyword in gpu_name_upper for keyword in ["INTEL", "UHD", "IRIS", "ARC"]):
            return GPUType.INTEL
        else:
            return GPUType.UNKNOWN
    
    def _get_cuda_version_for_gpu(self, gpu_name: str) -> CUDAVersion:
        """
        Determine CUDA version based on GPU name
        
        Args:
            gpu_name: Name of the GPU
            
        Returns:
            CUDAVersion enum value
        """
        gpu_name_upper = gpu_name.upper()
        
        # RTX 50xx series (Ada Lovelace Next-Gen) - CUDA 12.8
        if any(model in gpu_name_upper for model in ["RTX 50", "RTX 51", "RTX 52", "RTX 53", "RTX 54"]):
            return CUDAVersion.CUDA_128
        
        # RTX 40xx series (Ada Lovelace) - CUDA 12.8
        elif any(model in gpu_name_upper for model in ["RTX 40", "RTX 41", "RTX 42", "RTX 43", "RTX 44"]):
            return CUDAVersion.CUDA_128
        
        # RTX 30xx series (Ampere) - CUDA 12.4
        elif any(model in gpu_name_upper for model in ["RTX 30", "RTX 31", "RTX 32", "RTX 33", "RTX 34"]):
            return CUDAVersion.CUDA_124
        
        # GTX 16xx series (Turing) - CUDA 12.4
        elif any(model in gpu_name_upper for model in ["GTX 16", "GTX 17"]):
            return CUDAVersion.CUDA_124
        
        # RTX 20xx series (Turing) - CUDA 12.4
        elif any(model in gpu_name_upper for model in ["RTX 20", "RTX 21", "RTX 22", "RTX 23", "RTX 24"]):
            return CUDAVersion.CUDA_124
        
        # GTX 10xx series (Pascal) - CUDA 11.8
        elif any(model in gpu_name_upper for model in ["GTX 10"]):
            return CUDAVersion.CUDA_118
        
        # Older cards or unknown - CUDA 11.8
        else:
            return CUDAVersion.CUDA_118
    
    def _get_gpu_priority(self, gpu_name: str) -> int:
        """
        Get priority score for GPU (higher is better)
        
        Args:
            gpu_name: Name of the GPU
            
        Returns:
            Priority score
        """
        gpu_name_upper = gpu_name.upper()
        
        # RTX 50xx series
        if any(model in gpu_name_upper for model in ["RTX 50", "RTX 51", "RTX 52", "RTX 53", "RTX 54"]):
            return 1000
        
        # RTX 40xx series
        elif any(model in gpu_name_upper for model in ["RTX 40", "RTX 41", "RTX 42", "RTX 43", "RTX 44"]):
            return 900
        
        # RTX 30xx series
        elif any(model in gpu_name_upper for model in ["RTX 30", "RTX 31", "RTX 32", "RTX 33", "RTX 34"]):
            return 800
        
        # RTX 20xx series
        elif any(model in gpu_name_upper for model in ["RTX 20", "RTX 21", "RTX 22", "RTX 23", "RTX 24"]):
            return 700
        
        # GTX 16xx series
        elif any(model in gpu_name_upper for model in ["GTX 16", "GTX 17"]):
            return 600
        
        # GTX 10xx series
        elif any(model in gpu_name_upper for model in ["GTX 10"]):
            return 500
        
        # Older cards
        else:
            return 100
    
    def is_ai_capable(self) -> bool:
        """
        Check if the system has AI-capable GPU
        
        Returns:
            True if AI acceleration is available
        """
        primary_gpu = self.get_primary_gpu_type()
        return primary_gpu in [GPUType.NVIDIA, GPUType.AMD, GPUType.DIRECTML]
    
    def get_recommended_backend(self) -> str:
        """
        Get recommended AI backend based on GPU
        
        Returns:
            Recommended backend string
        """
        primary_gpu = self.get_primary_gpu_type()
        
        if primary_gpu == GPUType.NVIDIA:
            return "cuda"
        elif primary_gpu in [GPUType.AMD, GPUType.DIRECTML]:
            return "directml"
        elif primary_gpu == GPUType.INTEL:
            return "openvino"
        else:
            return "cpu"


# Legacy function for backward compatibility
def get_gpu() -> Optional[str]:
    """
    Legacy function for backward compatibility
    
    Returns:
        GPU type string or None
    """
    detector = GPUDetector()
    gpu_type = detector.get_primary_gpu_type()
    
    if gpu_type == GPUType.NVIDIA:
        return "NVIDIA"
    elif gpu_type in [GPUType.AMD, GPUType.DIRECTML]:
        return "DIRECTML"
    else:
        return None


# Main execution for testing
#if __name__ == "__main__":
    #detector = GPUDetector()
    
    #print("=== GPU Detection Results ===")
    #gpu_info_list = detector.get_gpu_info()
    
    #if gpu_info_list:
        #for i, gpu in enumerate(gpu_info_list, 1):
            #print(f"{i}. {gpu}")
    #else:
        #print("No GPUs detected")
    
    #print(f"\nPrimary GPU Type: {detector.get_primary_gpu_type().value}")
    #print(f"AI Capable: {detector.is_ai_capable()}")
    #print(f"Recommended Backend: {detector.get_recommended_backend()}")
    
    # CUDA version recommendation
    #cuda_version = detector.get_recommended_cuda_version()
    #if cuda_version:
        #print(f"Recommended CUDA Version: {cuda_version.value}")
    #else:
        #print("CUDA not applicable")
    
    # Test legacy function
    #print(f"Legacy get_gpu(): {get_gpu()}")

//! GPU detection and management

use crate::{Result, PortableSourceError};
use crate::config::{GpuConfig, GpuGeneration};
use std::process::Command;
use serde::Deserialize;
#[cfg(windows)]
use wmi::{COMLibrary, WMIConnection};

#[derive(Debug, Clone, PartialEq)]
pub enum GpuType {
    Nvidia,
    Amd,
    Intel,
    Unknown,
}

#[derive(Debug, Clone)]
pub struct GpuInfo {
    pub name: String,
    pub gpu_type: GpuType,
    pub memory_mb: u32,
    pub driver_version: Option<String>,
}

pub struct GpuDetector;

impl GpuDetector {
    pub fn new() -> Self {
        Self
    }
    
    /// Detect NVIDIA GPU using nvidia-smi
    pub fn detect_nvidia_gpu(&self) -> Result<Option<GpuInfo>> {
        let output = Command::new("nvidia-smi")
            .args(&["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"])
            .output();
            
        match output {
            Ok(output) if output.status.success() => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                if let Some(line) = stdout.lines().next() {
                    self.parse_nvidia_smi_output(line)
                } else {
                    Ok(None)
                }
            }
            _ => {
                log::debug!("nvidia-smi not available or failed");
                Ok(None)
            }
        }
    }
    
    fn parse_nvidia_smi_output(&self, line: &str) -> Result<Option<GpuInfo>> {
        let parts: Vec<&str> = line.split(',').map(|s| s.trim()).collect();
        
        if parts.len() >= 3 {
            let name = parts[0].to_string();
            let memory_mb = parts[1].parse::<u32>()
                .map_err(|_| PortableSourceError::gpu_detection("Failed to parse GPU memory"))?;
            let driver_version = Some(parts[2].to_string());
            
            Ok(Some(GpuInfo {
                name,
                gpu_type: GpuType::Nvidia,
                memory_mb,
                driver_version,
            }))
        } else {
            Err(PortableSourceError::gpu_detection("Invalid nvidia-smi output format"))
        }
    }
    
    /// Detect GPU using Windows WMI (via wmi crate), fallback to shell if needed
    pub fn detect_gpu_wmi(&self) -> Result<Vec<GpuInfo>> {
        #[cfg(windows)]
        {
            if let Ok(com) = COMLibrary::new() {
                if let Ok(wmi_con) = WMIConnection::new(com.into()) {
                    #[derive(Deserialize)]
                    #[allow(non_snake_case)]
                    struct Win32VideoController {
                        #[serde(rename = "Name")] Name: Option<String>,
                        #[serde(rename = "AdapterRAM")] AdapterRAM: Option<u64>,
                        #[serde(rename = "DriverVersion")] DriverVersion: Option<String>,
                    }
                    if let Ok(results) = wmi_con.query::<Win32VideoController>() {
                        let mut gpus = Vec::new();
                        for r in results {
                            let name = r.Name.unwrap_or_default();
                            if name.is_empty() { continue; }
                            let adapter_ram = r.AdapterRAM.unwrap_or(0);
                            let memory_mb = (adapter_ram / (1024 * 1024)) as u32;
                            let driver_version = r.DriverVersion;
                            let gpu_type = self.determine_gpu_type(&name);
                            gpus.push(GpuInfo { name, gpu_type, memory_mb, driver_version });
                        }
                        if !gpus.is_empty() { return Ok(gpus); }
                    }
                }
            }
        }

        // Fallback: shell WMIC (Windows) or empty on other OS
        let output = Command::new("wmic")
            .args(&["path", "win32_VideoController", "get", "name,AdapterRAM,DriverVersion", "/format:csv"])
            .output();

        match output {
            Ok(output) if output.status.success() => {
                let stdout = String::from_utf8_lossy(&output.stdout);
                let mut gpus = Vec::new();
                for line in stdout.lines().skip(1) {
                    if line.trim().is_empty() { continue; }
                    let parts: Vec<&str> = line.split(',').collect();
                    if parts.len() >= 4 {
                        let name = parts[3].trim().to_string();
                        if name.is_empty() || name == "Name" { continue; }
                        let memory_bytes = parts[2].trim().parse::<u64>().unwrap_or(0);
                        let memory_mb = (memory_bytes / (1024 * 1024)) as u32;
                        let driver_version = {
                            let dv = parts.get(1).map(|s| s.trim()).unwrap_or("");
                            if dv.is_empty() || dv == "DriverVersion" { None } else { Some(dv.to_string()) }
                        };
                        let gpu_type = self.determine_gpu_type(&name);
                        gpus.push(GpuInfo { name, gpu_type, memory_mb, driver_version });
                    }
                }
                Ok(gpus)
            }
            _ => Ok(Vec::new()),
        }
    }

    
    fn determine_gpu_type(&self, name: &str) -> GpuType {
        let name_upper = name.to_uppercase();
        
        if name_upper.contains("NVIDIA") || name_upper.contains("GEFORCE") || name_upper.contains("QUADRO") || name_upper.contains("TESLA") {
            GpuType::Nvidia
        } else if name_upper.contains("AMD") || name_upper.contains("RADEON") {
            GpuType::Amd
        } else if name_upper.contains("INTEL") {
            GpuType::Intel
        } else {
            GpuType::Unknown
        }
    }
    
    /// Get the best available GPU (prioritize NVIDIA)
    pub fn get_best_gpu(&self) -> Result<Option<GpuInfo>> {
        // First try nvidia-smi for accurate NVIDIA detection
        if let Some(nvidia_gpu) = self.detect_nvidia_gpu()? {
            return Ok(Some(nvidia_gpu));
        }
        
        // Fall back to WMI detection
        let gpus = self.detect_gpu_wmi()?;
        
        // Prioritize NVIDIA GPUs
        for gpu in &gpus {
            if gpu.gpu_type == GpuType::Nvidia {
                return Ok(Some(gpu.clone()));
            }
        }
        
        // Return first available GPU
        Ok(gpus.into_iter().next())
    }
    
    /// Check if NVIDIA GPU is available
    pub fn has_nvidia_gpu(&self) -> bool {
        self.detect_nvidia_gpu().unwrap_or(None).is_some()
    }
    
    /// Create GPU configuration from detected GPU
    pub fn create_gpu_config(&self, gpu_info: &GpuInfo, config_manager: &crate::config::ConfigManager) -> GpuConfig {
        let generation = config_manager.detect_gpu_generation(&gpu_info.name);
        let cuda_version = config_manager.get_recommended_cuda_version(&generation);
        
        let compute_capability = self.get_compute_capability(&generation);
        let memory_gb = gpu_info.memory_mb / 1024;
        
        let recommended_backend = match gpu_info.gpu_type {
            GpuType::Nvidia if generation != GpuGeneration::Unknown => "cuda".to_string(),
            _ => "cpu".to_string(),
        };
        
        let supports_tensorrt = matches!(gpu_info.gpu_type, GpuType::Nvidia) 
            && !matches!(generation, GpuGeneration::Unknown);
        
        GpuConfig {
            name: gpu_info.name.clone(),
            generation,
            cuda_version,
            cuda_paths: None, // Will be set when CUDA is installed
            compute_capability,
            memory_gb,
            recommended_backend,
            supports_tensorrt,
        }
    }
    
    fn get_compute_capability(&self, generation: &GpuGeneration) -> String {
        match generation {
            GpuGeneration::Pascal => "6.1".to_string(),
            GpuGeneration::Turing => "7.5".to_string(),
            GpuGeneration::Ampere => "8.6".to_string(),
            GpuGeneration::AdaLovelace => "8.9".to_string(),
            GpuGeneration::Blackwell => "9.0".to_string(),
            GpuGeneration::Unknown => "0.0".to_string(),
        }
    }
}

// removed raw COM helpers; using wmi crate instead

impl Default for GpuDetector {
    fn default() -> Self {
        Self::new()
    }
}
//! GPU detection and management

use crate::{Result, PortableSourceError};
use std::process::Command;
#[cfg(windows)]
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
    
    /// Detect GPU using Windows WMI (via wmi crate), fallback to WMIC on Windows only
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

            // Fallback: WMIC CLI
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
        #[cfg(not(windows))]
        {
            Ok(Vec::new())
        }
    }

    #[cfg(unix)]
    fn detect_gpu_linux_lspci(&self) -> Vec<GpuInfo> {
        let mut gpus = Vec::new();
        let output = Command::new("sh")
            .arg("-c")
            .arg("lspci -mm | egrep -i 'VGA|3D|Display'")
            .output();
        if let Ok(out) = output {
            if out.status.success() {
                let text = String::from_utf8_lossy(&out.stdout);
                for line in text.lines() {
                    let l = line.to_string();
                    let up = l.to_uppercase();
                    let gpu_type = if up.contains("NVIDIA") { GpuType::Nvidia } else if up.contains("AMD") || up.contains("ATI") || up.contains("RADEON") { GpuType::Amd } else if up.contains("INTEL") { GpuType::Intel } else { GpuType::Unknown };
                    if gpu_type != GpuType::Unknown {
                        // Try to extract model name between quotes if present
                        let name = if let Some(start) = l.find('"') { if let Some(end) = l[start+1..].find('"') { l[start+1..start+1+end].to_string() } else { l.clone() } } else { l.clone() };
                        gpus.push(GpuInfo { name, gpu_type, memory_mb: 0, driver_version: None });
                    }
                }
            }
        }
        gpus
    }

    #[cfg(unix)]
    fn detect_gpu_linux_glxinfo(&self) -> Option<GpuInfo> {
        let out = Command::new("sh").arg("-c").arg("glxinfo -B 2>/dev/null | grep 'renderer string' || true").output().ok()?;
        if !out.status.success() { return None; }
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().next()?.to_string();
        let lower = line.to_lowercase();
        let gpu_type = if lower.contains("nvidia") { GpuType::Nvidia } else if lower.contains("amd") || lower.contains("radeon") { GpuType::Amd } else if lower.contains("intel") { GpuType::Intel } else { GpuType::Unknown };
        Some(GpuInfo { name: line, gpu_type, memory_mb: 0, driver_version: None })
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
        
        #[cfg(windows)]
        {
            // Fall back to WMI/WMIC on Windows
            let gpus = self.detect_gpu_wmi()?;
            for gpu in &gpus { if gpu.gpu_type == GpuType::Nvidia { return Ok(Some(gpu.clone())); } }
            return Ok(gpus.into_iter().next());
        }
        #[cfg(unix)]
        {
            // Linux: try lspci then glxinfo as best-effort
            let mut gpus = self.detect_gpu_linux_lspci();
            if gpus.is_empty() {
                if let Some(glx) = self.detect_gpu_linux_glxinfo() { gpus.push(glx); }
            }
            for gpu in &gpus { if gpu.gpu_type == GpuType::Nvidia { return Ok(Some(gpu.clone())); } }
            return Ok(gpus.into_iter().next());
        }
    }
    
    /// Check if NVIDIA GPU is available
    pub fn has_nvidia_gpu(&self) -> bool {
        self.detect_nvidia_gpu().unwrap_or(None).is_some()
    }

}

// removed raw COM helpers; using wmi crate instead

impl Default for GpuDetector {
    fn default() -> Self {
        Self::new()
    }
}
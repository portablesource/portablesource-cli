//! Configuration management for PortableSource

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use crate::{Result, PortableSourceError};
use crate::gpu::GpuDetector;
use log::{info, warn, error};
use std::process::Command;

// Constants
pub const SERVER_DOMAIN: &str = "portables.dev";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
pub enum GpuGeneration {
    #[serde(rename = "pascal")]
    Pascal,      // GTX 10xx series
    #[serde(rename = "turing")]
    Turing,      // GTX 16xx, RTX 20xx series
    #[serde(rename = "ampere")]
    Ampere,      // RTX 30xx series
    #[serde(rename = "ada")]
    AdaLovelace, // RTX 40xx series
    #[serde(rename = "blackwell")]
    Blackwell,   // RTX 50xx series
    #[serde(rename = "unknown")]
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum CudaVersion {
    #[serde(rename = "118")]
    Cuda118,
    #[serde(rename = "124")]
    Cuda124,
    #[serde(rename = "128")]
    Cuda128,
}

impl CudaVersion {
    pub fn get_download_url(&self) -> &'static str {
        match self {
            CudaVersion::Cuda118 => "https://files.portables.dev/CUDA/CUDA_118.7z",
            CudaVersion::Cuda124 => "https://files.portables.dev/CUDA/CUDA_124.7z",
            CudaVersion::Cuda128 => "https://files.portables.dev/CUDA/CUDA_128.7z",
        }
    }
}

#[derive(Debug, Clone)]
pub enum ToolLinks {
    Git,
    Ffmpeg,
    Python311,
    MsvcBuildTools,
    SevenZip,
}

impl ToolLinks {
    pub fn url(&self) -> &'static str {
        match self {
            ToolLinks::Git => "https://files.portables.dev/git.7z",
            ToolLinks::Ffmpeg => "https://files.portables.dev/ffmpeg.7z",
            ToolLinks::Python311 => "https://files.portables.dev/python311.7z",
            ToolLinks::MsvcBuildTools => "https://files.portables.dev/msvc_build_tools.7z",
            ToolLinks::SevenZip => "https://huggingface.co/datasets/NeuroDonu/PortableSource/resolve/main/7z.exe",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CudaPaths {
    pub base_path: PathBuf,
    pub cuda_bin: PathBuf,
    pub cuda_lib: PathBuf,
    pub cuda_include: PathBuf,
    pub cuda_nvml: PathBuf,
    pub cuda_nvvm: PathBuf,
    pub cuda_lib_64: PathBuf,
    pub cuda_nvml_include: PathBuf,
    pub cuda_nvml_bin: PathBuf,
    pub cuda_nvml_lib: PathBuf,
    pub cuda_nvvm_include: PathBuf,
    pub cuda_nvvm_bin: PathBuf,
    pub cuda_nvvm_lib: PathBuf,
}

impl CudaPaths {
    pub fn new<P: AsRef<Path>>(base_path: P) -> Self {
        let base = base_path.as_ref().to_path_buf();
        
        let cuda_bin = base.join("bin");
        let cuda_lib = base.join("lib");
        let cuda_include = base.join("include");
        let cuda_nvml = base.join("nvml");
        let cuda_nvvm = base.join("nvvm");
        
        let cuda_lib_64 = cuda_lib.join("x64");
        
        let cuda_nvml_include = cuda_nvml.join("include");
        let cuda_nvml_bin = cuda_nvml.join("bin");
        let cuda_nvml_lib = cuda_nvml.join("lib");
        
        let cuda_nvvm_include = cuda_nvvm.join("include");
        let cuda_nvvm_bin = cuda_nvvm.join("bin");
        let cuda_nvvm_lib = cuda_nvvm.join("lib");
        
        Self {
            base_path: base,
            cuda_bin,
            cuda_lib,
            cuda_include,
            cuda_nvml,
            cuda_nvvm,
            cuda_lib_64,
            cuda_nvml_include,
            cuda_nvml_bin,
            cuda_nvml_lib,
            cuda_nvvm_include,
            cuda_nvvm_bin,
            cuda_nvvm_lib,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GpuConfig {
    pub name: String,
    pub generation: GpuGeneration,
    pub cuda_version: Option<CudaVersion>,
    pub cuda_paths: Option<CudaPaths>,
    pub compute_capability: String,
    pub memory_gb: u32,
    pub recommended_backend: String,
    pub supports_tensorrt: bool,
}

impl Default for GpuConfig {
    fn default() -> Self {
        Self {
            name: String::new(),
            generation: GpuGeneration::Unknown,
            cuda_version: None,
            cuda_paths: None,
            compute_capability: String::new(),
            memory_gb: 0,
            recommended_backend: "cpu".to_string(),
            supports_tensorrt: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortableSourceConfig {
    pub version: String,
    pub install_path: PathBuf,
    pub gpu_config: Option<GpuConfig>,
    pub environment_vars: Option<HashMap<String, String>>,
    pub environment_setup_completed: bool,
}

impl Default for PortableSourceConfig {
    fn default() -> Self {
        Self {
            version: VERSION.to_string(),
            install_path: PathBuf::new(),
            gpu_config: None,
            environment_vars: None,
            environment_setup_completed: false,
        }
    }
}

#[derive(Clone)]
pub struct ConfigManager {
    config: PortableSourceConfig,
    config_path: PathBuf,
    gpu_patterns: HashMap<GpuGeneration, Vec<&'static str>>,
    cuda_mapping: HashMap<GpuGeneration, CudaVersion>,
}

impl ConfigManager {
    pub fn new(config_path: Option<PathBuf>) -> Result<Self> {
        let default_path = || {
            // Prefer install path from registry if present
            if let Ok(Some(p)) = crate::utils::load_install_path_from_registry() {
                return p.join("portablesource_config.json");
            }
            dirs::config_dir()
                .unwrap_or_else(|| PathBuf::from("."))
                .join("portablesource")
                .join("config.json")
        };
        let config_path = config_path.unwrap_or_else(default_path);
        
        // Initialize GPU patterns
        let mut gpu_patterns = HashMap::new();
        gpu_patterns.insert(GpuGeneration::Pascal, vec![
            "GTX 10", "GTX 1050", "GTX 1060", "GTX 1070", "GTX 1080",
            "TITAN X", "TITAN XP"
        ]);
        gpu_patterns.insert(GpuGeneration::Turing, vec![
            "GTX 16", "GTX 1650", "GTX 1660",
            "RTX 20", "RTX 2060", "RTX 2070", "RTX 2080",
            "TITAN RTX"
        ]);
        gpu_patterns.insert(GpuGeneration::Ampere, vec![
            "RTX 30", "RTX 3060", "RTX 3070", "RTX 3080", "RTX 3090",
            "RTX A", "A40", "A100"
        ]);
        gpu_patterns.insert(GpuGeneration::AdaLovelace, vec![
            "RTX 40", "RTX 4060", "RTX 4070", "RTX 4080", "RTX 4090",
            "RTX ADA", "L40", "L4"
        ]);
        gpu_patterns.insert(GpuGeneration::Blackwell, vec![
            "RTX 50", "RTX 5060", "RTX 5070", "RTX 5080", "RTX 5090"
        ]);
        
        // Initialize CUDA mapping
        let mut cuda_mapping = HashMap::new();
        cuda_mapping.insert(GpuGeneration::Pascal, CudaVersion::Cuda118);
        cuda_mapping.insert(GpuGeneration::Turing, CudaVersion::Cuda124);
        cuda_mapping.insert(GpuGeneration::Ampere, CudaVersion::Cuda124);
        cuda_mapping.insert(GpuGeneration::AdaLovelace, CudaVersion::Cuda128);
        cuda_mapping.insert(GpuGeneration::Blackwell, CudaVersion::Cuda128);
        
        let mut manager = Self {
            config: PortableSourceConfig::default(),
            config_path,
            gpu_patterns,
            cuda_mapping,
        };
        
        // Try to load existing config
        if manager.config_path.exists() {
            manager.load_config()?;
        }
        
        Ok(manager)
    }

    pub fn set_config_path_to_install_dir(&mut self) {
        if !self.config.install_path.as_os_str().is_empty() {
            self.config_path = self.config.install_path.join("portablesource_config.json");
        }
    }
    
    pub fn get_config(&self) -> &PortableSourceConfig {
        &self.config
    }
    
    pub fn get_config_mut(&mut self) -> &mut PortableSourceConfig {
        &mut self.config
    }
    
    pub fn set_install_path(&mut self, path: PathBuf) -> Result<()> {
        // Avoid redundant saves if path is unchanged
        if self.config.install_path == path {
            return Ok(());
        }
        // Validate path
        if !path.exists() {
            std::fs::create_dir_all(&path)
                .map_err(|e| PortableSourceError::installation(format!("Failed to create install path: {}", e)))?;
        }
        self.config.install_path = path;
        self.save_config()
    }
    
    pub fn detect_gpu_generation(&self, gpu_name: &str) -> GpuGeneration {
        let gpu_name_upper = gpu_name.to_uppercase();
        
        for (generation, patterns) in &self.gpu_patterns {
            if patterns.iter().any(|pattern| gpu_name_upper.contains(&pattern.to_uppercase())) {
                return generation.clone();
            }
        }
        
        warn!("Unknown GPU generation for: {}", gpu_name);
        GpuGeneration::Unknown
    }
    
    pub fn get_recommended_cuda_version(&self, generation: &GpuGeneration) -> Option<CudaVersion> {
        self.cuda_mapping.get(generation).cloned()
    }
    
    pub fn configure_gpu(&mut self, gpu_name: &str, memory_gb: u32) -> Result<GpuConfig> {
         let generation = self.detect_gpu_generation(gpu_name);
         let cuda_version = self.get_recommended_cuda_version(&generation);
         
         let compute_capability = self.get_compute_capability(&generation);
         
         let gpu_name_upper = gpu_name.to_uppercase();
         let (recommended_backend, supports_tensorrt) = if gpu_name_upper.contains("NVIDIA") || 
             gpu_name_upper.contains("GEFORCE") || gpu_name_upper.contains("QUADRO") || 
             gpu_name_upper.contains("TESLA") || gpu_name_upper.contains("RTX") || 
             gpu_name_upper.contains("GTX") {
             
             if generation != GpuGeneration::Unknown {
                 if let Some(ref cuda_ver) = cuda_version {
                     let supports_trt = matches!(cuda_ver, CudaVersion::Cuda124 | CudaVersion::Cuda128) &&
                         matches!(generation, GpuGeneration::Turing | GpuGeneration::Ampere | 
                                 GpuGeneration::AdaLovelace | GpuGeneration::Blackwell);
                     
                     if supports_trt {
                         ("cuda,tensorrt".to_string(), true)
                     } else {
                         ("cuda".to_string(), false)
                     }
                 } else {
                     ("cpu".to_string(), false)
                 }
             } else {
                 ("cpu".to_string(), false)
             }
         } else if gpu_name_upper.contains("AMD") || gpu_name_upper.contains("RADEON") || gpu_name_upper.contains("RX ") {
             ("dml".to_string(), false)
         } else if gpu_name_upper.contains("INTEL") || gpu_name_upper.contains("UHD") || 
                   gpu_name_upper.contains("IRIS") || gpu_name_upper.contains("ARC") {
             ("openvino".to_string(), false)
         } else {
             ("cpu".to_string(), false)
         };
         
         let cuda_paths = if cuda_version.is_some() && !self.config.install_path.as_os_str().is_empty() {
             let cuda_base_path = self.config.install_path
                 .join("ps_env")
                 .join("CUDA");
             Some(CudaPaths::new(cuda_base_path))
         } else {
             None
         };
         
         let gpu_config = GpuConfig {
             name: gpu_name.to_string(),
             generation,
             cuda_version,
             cuda_paths,
             compute_capability,
             memory_gb,
             recommended_backend,
             supports_tensorrt,
         };
         
         self.config.gpu_config = Some(gpu_config.clone());
         Ok(gpu_config)
     }
     
     pub fn configure_gpu_from_detection(&mut self) -> Result<GpuConfig> {
         let gpu_detector = GpuDetector::new();
         match gpu_detector.get_best_gpu()? {
             Some(primary_gpu) => {
                 let memory_gb = primary_gpu.memory_mb / 1024;
                 self.configure_gpu(&primary_gpu.name, memory_gb)
             }
             None => {
                 warn!("No GPU detected, using CPU configuration");
                 self.configure_gpu("Unknown GPU", 0)
             }
         }
     }

    /// Populate config based on existing ps_env content and nvidia-smi CUDA version
    pub fn hydrate_from_existing_env(&mut self) -> Result<()> {
        if self.config.install_path.as_os_str().is_empty() { return Ok(()); }
        let ps_env = self.config.install_path.join("ps_env");
        if !ps_env.exists() { return Ok(()); }

        // If CUDA folder exists, configure CUDA paths
        let cuda_dir = ps_env.join("CUDA");
        if cuda_dir.exists() {
            self.configure_cuda_paths();
        }

        // Ensure GPU config exists or fix placeholder values
        let needs_gpu_fill = match &self.config.gpu_config {
            None => true,
            Some(g) => g.name.is_empty() || g.name == "Unknown GPU" || g.memory_gb == 0,
        };
        if needs_gpu_fill {
            let gpu_detector = GpuDetector::new();
            if let Some(best) = gpu_detector.get_best_gpu().ok().flatten() {
                let _ = self.configure_gpu(&best.name, best.memory_mb / 1024);
            } else {
                let _ = self.configure_gpu("Unknown GPU", 0);
            }
        }

        // If nvidia-smi reports CUDA version, set it
        if let Some(cuda_ver) = detect_cuda_version_from_nvidia_smi() {
            if let Some(ref mut gpu_cfg) = self.config.gpu_config {
                gpu_cfg.cuda_version = Some(cuda_ver);
            } else {
                let mut gpu_cfg = GpuConfig::default();
                gpu_cfg.cuda_version = Some(cuda_ver);
                self.config.gpu_config = Some(gpu_cfg);
            }
            // After setting version, ensure CUDA paths
            self.configure_cuda_paths();
        }

        // Mark environment as setup if core tools exist
        let python_exe = if cfg!(windows) { ps_env.join("python").join("python.exe") } else { ps_env.join("python").join("bin").join("python") };
        let git_exe = if cfg!(windows) { ps_env.join("git").join("cmd").join("git.exe") } else { ps_env.join("git").join("bin").join("git") };
        let ffmpeg_exe = if cfg!(windows) { ps_env.join("ffmpeg").join("ffmpeg.exe") } else { ps_env.join("ffmpeg").join("ffmpeg") };
        if python_exe.exists() && git_exe.exists() && ffmpeg_exe.exists() {
            self.config.environment_setup_completed = true;
        }

        Ok(())
    }
     
     pub fn configure_install_path(&mut self, install_path: &str) -> String {
         let path = PathBuf::from(install_path);
         let install_path_str = path.to_string_lossy().to_string();
         self.config.install_path = path;
         install_path_str
     }
    
    pub fn configure_environment_vars(&mut self) -> HashMap<String, String> {
        let mut env_vars = HashMap::new();
        
        if !self.config.install_path.as_os_str().is_empty() {
            let tmp_path = self.config.install_path.join("tmp");
            let tmp_path_str = tmp_path.to_string_lossy().to_string();
            
            env_vars.insert("USERPROFILE".to_string(), tmp_path_str.clone());
            env_vars.insert("TEMP".to_string(), tmp_path_str.clone());
            env_vars.insert("TMP".to_string(), tmp_path_str);
        }
        
        self.config.environment_vars = Some(env_vars.clone());
        env_vars
    }
    
    pub fn configure_cuda_paths(&mut self) {
         if self.config.install_path.as_os_str().is_empty() {
             error!("Installation path not set, cannot configure CUDA paths");
             return;
         }
         
         if let Some(ref mut gpu_config) = self.config.gpu_config {
             if gpu_config.cuda_version.is_some() {
                 let cuda_base_path = self.config.install_path
                     .join("ps_env")
                     .join("CUDA");
                 gpu_config.cuda_paths = Some(CudaPaths::new(cuda_base_path));
             } else {
                 warn!("No CUDA version configured, skipping CUDA paths setup");
             }
         }
     }
     
     pub fn get_cuda_download_link(&self, cuda_version: Option<&CudaVersion>) -> Option<String> {
         let version = cuda_version.or_else(|| {
             self.config.gpu_config.as_ref()?.cuda_version.as_ref()
         })?;
         
         Some(version.get_download_url().to_string())
     }
     
     pub fn msvc_bt_config(&self) -> (String, String) {
         let url = "https://aka.ms/vs/17/release/vs_buildtools.exe".to_string();
         let args = " --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.NativeDesktop --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.Llvm.Clang".to_string();
         (url, args)
     }
     
     pub fn get_config_summary(&self) -> String {
         let (gpu_name, gpu_generation, cuda_version, cuda_paths_configured, 
              compute_capability, memory_gb, backend, tensorrt_support) = 
             if let Some(ref gpu_config) = self.config.gpu_config {
                 (
                     gpu_config.name.clone(),
                     format!("{:?}", gpu_config.generation),
                     gpu_config.cuda_version.as_ref().map(|v| format!("{:?}", v)).unwrap_or_else(|| "None".to_string()),
                     if gpu_config.cuda_paths.is_some() { "Yes" } else { "No" },
                     gpu_config.compute_capability.clone(),
                     gpu_config.memory_gb,
                     gpu_config.recommended_backend.clone(),
                     gpu_config.supports_tensorrt
                 )
             } else {
                 (
                     "Not configured".to_string(),
                     "Unknown".to_string(),
                     "None".to_string(),
                     "No",
                     "Unknown".to_string(),
                     0,
                     "cpu".to_string(),
                     false
                 )
             };
         
         let env_vars_count = self.config.environment_vars.as_ref().map(|vars| vars.len()).unwrap_or(0);
         let setup_status = if self.config.environment_setup_completed {
             "[OK] Completed"
         } else {
             "[ERROR] Not completed"
         };
         
         format!(
             "PortableSource Configuration Summary\n\
              ====================================\n\n\
              Environment Setup: {}\n\n\
              GPU Configuration:\n\
                Name: {}\n\
                Generation: {}\n\
                CUDA Version: {}\n\
                CUDA Paths Configured: {}\n\
                Compute Capability: {}\n\
                Memory: {}GB\n\
                Backend: {}\n\
                TensorRT Support: {}\n\n\
              Install Path: {}\n\n\
              Environment Variables: {} configured",
             setup_status, gpu_name, gpu_generation, cuda_version, cuda_paths_configured,
             compute_capability, memory_gb, backend, tensorrt_support,
             self.config.install_path.display(), env_vars_count
         )
     }
    
    fn get_compute_capability(&self, generation: &GpuGeneration) -> String {
        match generation {
            GpuGeneration::Pascal => "6.1".to_string(),
            GpuGeneration::Turing => "7.5".to_string(),
            GpuGeneration::Ampere => "8.6".to_string(),
            GpuGeneration::AdaLovelace => "8.9".to_string(),
            GpuGeneration::Blackwell => "9.0".to_string(),
            GpuGeneration::Unknown => "5.0".to_string(),
        }
    }
    
    pub fn is_environment_setup_completed(&self) -> bool {
        self.config.environment_setup_completed
    }
    
    pub fn mark_environment_setup_completed(&mut self, completed: bool) -> Result<()> {
        self.config.environment_setup_completed = completed;
        self.save_config()
    }
    
    pub fn save_config(&self) -> Result<()> {
        // Ensure config directory exists
        if let Some(parent) = self.config_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        
        let json = serde_json::to_string_pretty(&self.config)?;
        std::fs::write(&self.config_path, json)?;
        
        info!("Configuration saved to: {:?}", self.config_path);
        Ok(())
    }
    
    pub fn load_config(&mut self) -> Result<()> {
        if !self.config_path.exists() {
            info!("No configuration file found, creating default configuration");
            return Ok(()); // Use default config
        }
        
        let content = std::fs::read_to_string(&self.config_path)?;
        self.config = serde_json::from_str(&content)?;
        
        info!("Configuration loaded from: {:?}", self.config_path);
        Ok(())
    }
    
}

fn detect_cuda_version_from_nvidia_smi() -> Option<CudaVersion> {
    // Try `nvidia-smi --query-gpu=cuda_version --format=csv,noheader`
    let output = Command::new("nvidia-smi")
        .arg("--query-gpu=cuda_version")
        .arg("--format=csv,noheader")
        .output()
        .ok()?;
    if !output.status.success() { return None; }
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    // stdout could be like "12.4" or "12.8"; map to enum
    match stdout {
        s if s.starts_with("12.8") => Some(CudaVersion::Cuda128),
        s if s.starts_with("12.4") => Some(CudaVersion::Cuda124),
        s if s.starts_with("11.8") => Some(CudaVersion::Cuda118),
        _ => None,
    }
}
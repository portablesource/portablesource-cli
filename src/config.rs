//! Configuration management for PortableSource

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{PathBuf};
use crate::{Result, PortableSourceError};
use crate::gpu::{GpuDetector, GpuInfo};
use log::{info, warn};

// Constants
pub const SERVER_DOMAIN: &str = "server.portables.dev";
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub enum CudaVersionLinux {
    #[serde(rename = "118")]
    Cuda118,
    #[serde(rename = "121")]
    Cuda121,
    #[serde(rename = "124")]
    Cuda124,
    #[serde(rename = "126")]
    Cuda126,
    #[serde(rename = "128")]
    Cuda128,
}

impl CudaVersion {
    pub fn get_download_url(&self) -> &'static str {
        match self {
            CudaVersion::Cuda118 => "https://files.portables.dev/CUDA/CUDA_118.tar.zst",
            CudaVersion::Cuda124 => "https://files.portables.dev/CUDA/CUDA_124.tar.zst",
            CudaVersion::Cuda128 => "https://files.portables.dev/CUDA/CUDA_128.tar.zst",
        }
    }
}

#[derive(Debug, Clone)]
pub enum ToolLinks {
    Git,
    Ffmpeg,
    Python311,
    MsvcBuildTools,
    // SevenZip удален, так как перешли на tar zstd
}

impl ToolLinks {
    pub fn url(&self) -> &'static str {
        match self {
            ToolLinks::Git => "https://files.portables.dev/git.tar.zst",
            ToolLinks::Ffmpeg => "https://files.portables.dev/ffmpeg.tar.zst",
            ToolLinks::Python311 => "https://files.portables.dev/python.tar.zst",
            ToolLinks::MsvcBuildTools => "https://aka.ms/vs/17/release/vs_buildtools.exe",
            // ToolLinks::SevenZip больше не используется, так как перешли на tar zstd
        }
    }
}



// GpuConfig removed - all GPU parameters are now computed dynamically

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortableSourceConfig {
    pub version: String,
    pub install_path: PathBuf,
    pub environment_vars: Option<HashMap<String, String>>,
    pub environment_setup_completed: bool,
}

impl Default for PortableSourceConfig {
    fn default() -> Self {
        Self {
            version: VERSION.to_string(),
            install_path: PathBuf::new(),
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
    /// Dynamically detect if CUDA should be installed based on GPU
    pub fn has_cuda(&self) -> bool {
        // Check if we have an NVIDIA GPU that supports CUDA
        if let Some(gpu_info) = self.detect_gpu() {
            let gpu_name_upper = gpu_info.name.to_uppercase();
            return gpu_name_upper.contains("NVIDIA") || gpu_name_upper.contains("GEFORCE") || gpu_name_upper.contains("RTX");
        }
        false
    }
    
    /// Dynamically get CUDA version based on GPU generation
    pub fn get_cuda_version(&self) -> Option<CudaVersion> {
        if !self.has_cuda() {
            return None;
        }
        
        // Get CUDA version based on GPU generation
        let generation = self.detect_current_gpu_generation();
        self.get_recommended_cuda_version(&generation)
    }
    
    /// Dynamically detect GPU generation
    pub fn detect_current_gpu_generation(&self) -> GpuGeneration {
        if let Some(gpu_info) = self.detect_gpu() {
            self.detect_gpu_generation(&gpu_info.name)
        } else {
            GpuGeneration::Unknown
        }
    }
    
    /// Get recommended backend based on available hardware
    pub fn get_recommended_backend(&self) -> String {
        if self.has_cuda() {
            "cuda".to_string()
        } else {
            "cpu".to_string()
        }
    }
    
    /// Check if TensorRT is supported
    pub fn supports_tensorrt(&self) -> bool {
        if !self.has_cuda() {
            return false;
        }
        
        let generation = self.detect_current_gpu_generation();
        matches!(generation, GpuGeneration::Ampere | GpuGeneration::AdaLovelace | GpuGeneration::Blackwell)
    }
    
    /// Get CUDA base path dynamically
    pub fn get_cuda_base_path(&self) -> Option<PathBuf> {
        if self.has_cuda() {
            Some(self.config.install_path.join("ps_env").join("CUDA"))
        } else {
            None
        }
    }
    
    /// Get CUDA bin path dynamically
    pub fn get_cuda_bin(&self) -> Option<PathBuf> {
        self.get_cuda_base_path().map(|base| base.join("bin"))
    }
    
    /// Get CUDA lib path dynamically
    pub fn get_cuda_lib(&self) -> Option<PathBuf> {
        self.get_cuda_base_path().map(|base| base.join("lib"))
    }
    
    /// Get CUDA lib64 path dynamically
    pub fn get_cuda_lib_64(&self) -> Option<PathBuf> {
        self.get_cuda_base_path().map(|base| base.join("lib").join("x64"))
    }
    
    /// Get CUDA include path dynamically
    pub fn get_cuda_include(&self) -> Option<PathBuf> {
        self.get_cuda_base_path().map(|base| base.join("include"))
    }

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
        // Configuration is no longer saved to disk - settings are session-only
        Ok(())
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
    
    pub fn get_gpu_name(&self) -> String {
        if let Some(gpu_info) = self.detect_gpu() {
            gpu_info.name
        } else {
            "Unknown GPU".to_string()
        }
    }
    
    pub fn detect_gpu(&self) -> Option<GpuInfo> {
        let detector = GpuDetector::new();
        if let Ok(gpu_info) = detector.get_best_gpu() {
            gpu_info
        } else {
            None
        }
    }
    


    /// Populate config based on existing ps_env content and nvidia-smi CUDA version
    pub fn hydrate_from_existing_env(&mut self) -> Result<()> {
        if self.config.install_path.as_os_str().is_empty() { return Ok(()); }
        let ps_env = self.config.install_path.join("ps_env");
        if !ps_env.exists() { return Ok(()); }

        // CUDA paths are now computed dynamically when needed

        // Mark environment as setup if core tools exist
        let python_exe = if cfg!(windows) { ps_env.join("python").join("python.exe") } else { ps_env.join("python").join("bin").join("python") };
        let git_exe = if cfg!(windows) { ps_env.join("git").join("cmd").join("git.exe") } else { ps_env.join("git").join("bin").join("git") };
        let ffmpeg_exe = if cfg!(windows) { ps_env.join("ffmpeg").join("ffmpeg.exe") } else { ps_env.join("ffmpeg").join("ffmpeg") };
        if python_exe.exists() && git_exe.exists() && ffmpeg_exe.exists() {
            self.config.environment_setup_completed = true;
        }

        // Unix: also consider micromamba base env as a completed base
        #[cfg(unix)]
        if !self.config.environment_setup_completed {
            let mamba_py = ps_env.join("mamba_env").join("bin").join("python");
            if mamba_py.exists() { self.config.environment_setup_completed = true; }
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
    

     pub fn get_cuda_download_link(&self, cuda_version: Option<&CudaVersion>) -> Option<String> {
         let version = if let Some(v) = cuda_version {
             v.clone()
         } else {
             self.get_cuda_version()?
         };
         
         Some(version.get_download_url().to_string())
     }
     
     pub fn msvc_bt_config(&self) -> (String, String) {
         // Не используется больше для финального списка; оставлено для совместимости
         ("https://aka.ms/vs/17/release/vs_buildtools.exe".to_string(), String::new())
     }
     
     pub fn get_config_summary(&self) -> String {
         // Get GPU info dynamically
         let gpu_detector = crate::gpu::GpuDetector::new();
         let (gpu_name, memory_gb) = if let Ok(Some(gpu_info)) = gpu_detector.get_best_gpu() {
             (gpu_info.name, gpu_info.memory_mb / 1024)
         } else {
             ("Unknown GPU".to_string(), 0)
         };
         
         let gpu_generation = self.detect_current_gpu_generation();
         let cuda_version = self.get_cuda_version();
         let backend = self.get_recommended_backend();
         let tensorrt_support = self.supports_tensorrt();
         let compute_capability = self.get_compute_capability(&gpu_generation);
         
         let (gpu_generation_str, cuda_version_str, cuda_paths_configured) = (
             format!("{:?}", gpu_generation),
             cuda_version.as_ref().map(|v| format!("{:?}", v)).unwrap_or_else(|| "None".to_string()),
             if cuda_version.is_some() { "Yes" } else { "No" }
         );
         
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
             setup_status, gpu_name, gpu_generation_str, cuda_version_str, cuda_paths_configured,
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
        // Configuration is no longer saved to disk - settings are session-only
        Ok(())
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

// Detect CUDA version by parsing `nvcc --version` output (Linux)
#[cfg(unix)]
fn detect_cuda_version_from_nvcc() -> Option<CudaVersion> {
    let output = std::process::Command::new("nvcc")
        .arg("--version")
        .output()
        .ok()?;
    if !output.status.success() { return None; }
    let stdout = String::from_utf8_lossy(&output.stdout);
    // Typical line: "Cuda compilation tools, release 12.4, V12.4.131"
    for line in stdout.lines() {
        let l = line.to_lowercase();
        if l.contains("release") && l.contains("cuda compilation tools") {
            // extract number after 'release '
            if let Some(pos) = l.find("release") {
                let rest = &l[pos + "release".len()..];
                let rest = rest.trim().trim_start_matches(':').trim_start_matches(',').trim();
                // rest starts like "12.4, v12.4.131"
                let ver = rest.split(|c| c == ',' || c == ' ').next().unwrap_or("");
                if ver.starts_with("12.8") { return Some(CudaVersion::Cuda128); }
                if ver.starts_with("12.4") { return Some(CudaVersion::Cuda124); }
                if ver.starts_with("11.8") { return Some(CudaVersion::Cuda118); }
            }
        }
    }
    None
}

// Fallback: detect CUDA Toolkit installed on filesystem
#[cfg(unix)]
fn detect_cuda_version_from_filesystem() -> Option<CudaVersion> {
    use std::fs;
    use std::path::Path;
    let vt = Path::new("/usr/local/cuda/version.txt");
    if let Ok(content) = fs::read_to_string(vt) {
        let lower = content.to_lowercase();
        // lines like: CUDA Version 12.4.0
        if lower.contains("12.8") { return Some(CudaVersion::Cuda128); }
        if lower.contains("12.4") { return Some(CudaVersion::Cuda124); }
        if lower.contains("11.8") { return Some(CudaVersion::Cuda118); }
    }
    None
}
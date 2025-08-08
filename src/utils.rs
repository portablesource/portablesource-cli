//! Utility functions for PortableSource

use crate::{Result, PortableSourceError};
use crate::config::ConfigManager;
use crate::envs_manager::PortableEnvironmentManager;
use crate::repository_installer::RepositoryInstaller;
use crate::gpu::{GpuDetector, GpuType};
use std::path::{Path, PathBuf};
use winreg::enums::*;
use winreg::RegKey;
use std::process::Command;
use std::fs;
use std::time::Duration;

const REGISTRY_KEY: &str = r"Software\PortableSource";
const INSTALL_PATH_VALUE: &str = "InstallPath";

/// Save installation path to Windows registry
pub fn save_install_path_to_registry(install_path: &Path) -> Result<()> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (key, _) = hkcu.create_subkey(REGISTRY_KEY)
        .map_err(|e| PortableSourceError::Registry(format!("Failed to create registry key: {}", e)))?;
    
    key.set_value(INSTALL_PATH_VALUE, &install_path.to_string_lossy().to_string())
        .map_err(|e| PortableSourceError::Registry(format!("Failed to set registry value: {}", e)))?;
    
    log::info!("Installation path saved to registry: {:?}", install_path);
    Ok(())
}

/// Delete installation path from Windows registry
pub fn delete_install_path_from_registry() -> Result<()> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    
    match hkcu.open_subkey_with_flags(REGISTRY_KEY, KEY_ALL_ACCESS) {
        Ok(key) => {
            key.delete_value(INSTALL_PATH_VALUE)
                .map_err(|e| PortableSourceError::Registry(format!("Failed to delete registry value: {}", e)))?;
            log::info!("Installation path deleted from registry");
            Ok(())
        }
        Err(_) => {
            log::warn!("Registry key not found");
            Ok(()) // Not an error if key doesn't exist
        }
    }
}

/// Load installation path from Windows registry
pub fn load_install_path_from_registry() -> Result<Option<PathBuf>> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    
    match hkcu.open_subkey(REGISTRY_KEY) {
        Ok(key) => {
            match key.get_value::<String, _>(INSTALL_PATH_VALUE) {
                Ok(path_str) => {
                    let path = PathBuf::from(path_str);
                    log::debug!("Loaded installation path from registry: {:?}", path);
                    Ok(Some(path))
                }
                Err(_) => Ok(None),
            }
        }
        Err(_) => Ok(None),
    }
}

/// Validate and create directory if it doesn't exist
pub fn validate_and_create_path(path: &Path) -> Result<PathBuf> {
    // Avoid canonicalize on Windows to prevent verbatim prefix (\\?\) in stored config/display
    let abs_path = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };

    if !abs_path.exists() {
        std::fs::create_dir_all(&abs_path)
            .map_err(|e| PortableSourceError::installation(
                format!("Failed to create directory {:?}: {}", abs_path, e)
            ))?;
    }

    if !abs_path.is_dir() {
        return Err(PortableSourceError::invalid_path(
            format!("Path is not a directory: {:?}", abs_path)
        ));
    }

    Ok(abs_path)
}

/// Validate and convert string path to PathBuf and ensure it exists
pub fn validate_and_get_path(path_str: &str) -> Result<PathBuf> {
    let path = PathBuf::from(path_str);
    validate_and_create_path(&path)
}

/// Create necessary directory structure for PortableSource
pub fn create_directory_structure(install_path: &Path) -> Result<()> {
    let directories = [
        install_path.join("ps_env"),
        install_path.join("repos"),
        install_path.join("envs"),
    ];
    
    for dir in &directories {
        std::fs::create_dir_all(dir)
            .map_err(|e| PortableSourceError::installation(
                format!("Failed to create directory {:?}: {}", dir, e)
            ))?;
        log::debug!("Created directory: {:?}", dir);
    }
    
    Ok(())
}

/// Check if MSVC Build Tools are installed
pub fn check_msvc_build_tools_installed() -> bool {
    // Check for cl.exe in common locations
    let common_paths = [
        r"C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Tools\MSVC",
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC",
        r"C:\Program Files\Microsoft Visual Studio\2019\Community\VC\Tools\MSVC",
        r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC",
    ];
    
    for base_path in &common_paths {
        let path = Path::new(base_path);
        if path.exists() {
            // Look for any version subdirectory
            if let Ok(entries) = std::fs::read_dir(path) {
                for entry in entries.flatten() {
                    let cl_path = entry.path().join("bin").join("Hostx64").join("x64").join("cl.exe");
                    if cl_path.exists() {
                        return true;
                    }
                }
            }
        }
    }
    
    // Also check if cl.exe is in PATH or registry Instances
    Command::new("cl")
        .arg("/?")
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false)
        || check_msvc_registry()
}

fn check_msvc_registry() -> bool {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    if let Ok(instances) = hklm.open_subkey(r"SOFTWARE\\Microsoft\\VisualStudio\\Setup\\Instances") {
        for subkey in instances.enum_keys().flatten() {
            if let Ok(instance) = hklm.open_subkey(format!(
                r"SOFTWARE\\Microsoft\\VisualStudio\\Setup\\Instances\\{}",
                subkey
            )) {
                if let Ok::<String, _>(product_id) = instance.get_value("ProductId") {
                    if product_id.contains("BuildTools") {
                        return true;
                    }
                }
            }
        }
    }
    false
}

/// Download and run MSVC Build Tools installer (blocking)
pub fn install_msvc_build_tools() -> Result<()> {
    use reqwest::blocking::Client;
    use std::io::copy;
    
    // Download URL and args (match python version's config)
    let (url, args) = crate::config::ConfigManager::new(None)
        .map(|cm| cm.msvc_bt_config())
        .unwrap_or_else(|_| (
            "https://aka.ms/vs/17/release/vs_buildtools.exe".to_string(),
            " --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.NativeDesktop --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.Llvm.Clang".to_string()
        ));

    log::info!("Starting MSVC Build Tools installation...");
    log::info!("Download URL: {}", url);

    // Prepare temp dir and file
    let temp_dir = std::env::temp_dir().join("portablesource");
    fs::create_dir_all(&temp_dir)?;
    let installer_path = temp_dir.join("vs_buildtools.exe");

    // Download file
    log::info!("Downloading installer to {:?}...", installer_path);
    let client = Client::builder()
        .timeout(Duration::from_secs(600))
        .build()?;
    let mut resp = client.get(&url).send()?;
    if !resp.status().is_success() {
        return Err(PortableSourceError::installation(format!(
            "Failed to download installer: HTTP {}",
            resp.status()
        )));
    }
    let mut file = std::fs::File::create(&installer_path)?;
    copy(&mut resp, &mut file)?;

    // Run installer
    log::info!("Running installer (this may take a while)...");
    let status = Command::new(&installer_path)
        .args(args.split_whitespace())
        .status()
        .map_err(|e| PortableSourceError::command(format!("Failed to start installer: {}", e)))?;

    // Cleanup best-effort
    let _ = std::fs::remove_file(&installer_path);

    if status.success() {
        log::info!("[OK] MSVC Build Tools installed successfully");
        Ok(())
    } else {
        Err(PortableSourceError::installation(format!(
            "Installer exited with code {:?}",
            status.code()
        )))
    }
}

/// Install MSVC Build Tools using a provided install path for temp storage
pub fn install_msvc_build_tools_with_path(install_path: &Path) -> Result<()> {
    use reqwest::blocking::Client;
    use std::io::copy;

    let (url, args) = ConfigManager::new(None)
        .map(|cm| cm.msvc_bt_config())
        .unwrap_or_else(|_| (
            "https://aka.ms/vs/17/release/vs_buildtools.exe".to_string(),
            " --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.NativeDesktop --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.Llvm.Clang".to_string()
        ));

    log::info!("Starting MSVC Build Tools installation...");
    log::info!("Download URL: {}", url);

    let temp_dir = install_path.join("tmp");
    fs::create_dir_all(&temp_dir)?;
    let installer_path = temp_dir.join("vs_buildtools.exe");

    log::info!("Downloading installer to {:?}...", installer_path);
    let client = Client::builder().timeout(Duration::from_secs(600)).build()?;
    let mut resp = client.get(&url).send()?;
    if !resp.status().is_success() {
        return Err(PortableSourceError::installation(format!(
            "Failed to download installer: HTTP {}",
            resp.status()
        )));
    }
    let mut file = std::fs::File::create(&installer_path)?;
    copy(&mut resp, &mut file)?;

    log::info!("Running installer (this may take a while)...");
    let status = Command::new(&installer_path)
        .args(args.split_whitespace())
        .status()
        .map_err(|e| PortableSourceError::command(format!("Failed to start installer: {}", e)))?;

    let _ = std::fs::remove_file(&installer_path);

    if status.success() {
        log::info!("[OK] MSVC Build Tools installed successfully");
        Ok(())
    } else {
        Err(PortableSourceError::installation(format!(
            "Installer exited with code {:?}",
            status.code()
        )))
    }
}

/// Show version information
pub fn show_version() {
    println!("PortableSource version: {}", crate::config::VERSION);
}

/// Get system information
pub fn get_system_info() -> Result<String> {
    let mut info = Vec::new();
    
    // OS Information
    info.push(format!("OS: {}", std::env::consts::OS));
    info.push(format!("Architecture: {}", std::env::consts::ARCH));
    
    // Available tools
    let tools = ["git", "python", "pip"];
    for tool in &tools {
        let available = which::which(tool).is_ok();
        info.push(format!("{}: {}", tool, if available { "Available" } else { "Not found" }));
    }
    
    Ok(info.join("\n"))
}

/// Check if a command is available in PATH
pub fn is_command_available(command: &str) -> bool {
    which::which(command).is_ok()
}

/// Execute a command and return the output
pub fn execute_command(command: &str, args: &[&str], working_dir: Option<&Path>) -> Result<String> {
    let mut cmd = Command::new(command);
    cmd.args(args);
    
    if let Some(dir) = working_dir {
        cmd.current_dir(dir);
    }
    
    let output = cmd.output()
        .map_err(|e| PortableSourceError::command(
            format!("Failed to execute command '{}': {}", command, e)
        ))?;
    
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(PortableSourceError::command(
            format!("Command '{}' failed: {}", command, stderr)
        ));
    }
    
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

/// Format file size in human-readable format
pub fn format_file_size(bytes: u64) -> String {
    const UNITS: &[&str] = &["B", "KB", "MB", "GB", "TB"];
    
    if bytes == 0 {
        return "0 B".to_string();
    }
    
    let mut size = bytes as f64;
    let mut unit_index = 0;
    
    while size >= 1024.0 && unit_index < UNITS.len() - 1 {
        size /= 1024.0;
        unit_index += 1;
    }
    
    if unit_index == 0 {
        format!("{} {}", bytes, UNITS[unit_index])
    } else {
        format!("{:.1} {}", size, UNITS[unit_index])
    }
}

/// Check if NVIDIA GPU is Pascal (GTX 10xx) or newer
pub fn check_nv_gpu() -> bool {
    let detector = GpuDetector::new();
    if let Ok(gpus) = detector.detect_gpu_wmi() {
        for gpu in gpus {
            if gpu.gpu_type == GpuType::Nvidia {
                let n = gpu.name.to_uppercase();
                let ok = n.contains("GTX 10")
                    || n.contains("GTX 16") || n.contains("GTX 17")
                    || n.contains("RTX 20") || n.contains("RTX 21") || n.contains("RTX 22") || n.contains("RTX 23") || n.contains("RTX 24")
                    || n.contains("RTX 30") || n.contains("RTX 31") || n.contains("RTX 32") || n.contains("RTX 33") || n.contains("RTX 34")
                    || n.contains("RTX 40") || n.contains("RTX 41") || n.contains("RTX 42") || n.contains("RTX 43") || n.contains("RTX 44")
                    || n.contains("RTX 50") || n.contains("RTX 51") || n.contains("RTX 52") || n.contains("RTX 53") || n.contains("RTX 54")
                    || n.contains("QUADRO") || n.contains("TESLA")
                    || n.contains("A100") || n.contains("A40") || n.contains("A30") || n.contains("A10")
                    || n.contains("A6000") || n.contains("A5000") || n.contains("A4000");
                if ok { return true; }
            }
        }
    }
    false
}

/// Show detailed system information similar to Python version
pub fn show_system_info_detailed(install_path: &Path, config_manager: &ConfigManager, env_manager: Option<&PortableEnvironmentManager>) -> Result<()> {
    let slash = if cfg!(windows) { "\\" } else { "/" };
    let os_name = if cfg!(windows) { "Windows" } else { "Linux/macOS" };

    log::info!("PortableSource - System Information:");
    log::info!("  - Installation path: {}", install_path.display());
    log::info!("  - Operating system: {}", os_name);

    // Directory structure
    log::info!("  - Directory structure:");
    log::info!("    * {}{}ps_env", install_path.display(), slash);
    log::info!("    * {}{}repos", install_path.display(), slash);
    log::info!("    * {}{}envs", install_path.display(), slash);

    // GPU information
    if let Some(gpu) = &config_manager.get_config().gpu_config {
        log::info!("  - GPU: {}", gpu.name);
        log::info!("  - GPU type: {:?}", gpu.generation);
        if let Some(cuda) = &gpu.cuda_version { log::info!("  - CUDA version: {:?}", cuda); }
    } else {
        log::info!("  - GPU: Not configured");
    }

    // Portable environment
    if let Some(mgr) = env_manager {
        let available = mgr.check_environment_status()?;
        log::info!("  - Portable Environment: {}", if available { "Available" } else { "Not available" });
        let base_created = mgr.get_python_executable().is_some();
        log::info!("  - Base environment (ps_env): {}", if base_created { "Created" } else { "Not created" });
    }

    let msvc_status = if check_msvc_build_tools_installed() { "Installed" } else { "Not installed" };
    log::info!("  - MSVC Build Tools: {}", msvc_status);
    Ok(())
}

/// Interactive change of installation path, saves to registry and config
pub fn change_installation_path_interactive(config_manager: &mut ConfigManager) -> Result<()> {
    use std::io::{self, Write};

    println!("\n============================================================");
    println!("CHANGE PORTABLESOURCE INSTALLATION PATH");
    println!("============================================================");

    if let Some(reg_path) = load_install_path_from_registry()? {
        println!("\nCurrent installation path: {}", reg_path.display());
    } else {
        println!("\nCurrent installation path not found in registry");
    }

    let default_path = PathBuf::from("C:/PortableSource");
    println!("\nDefault path will be used: {}", default_path.display());
    println!("\nYou can:");
    println!("1. Press Enter to use the default path");
    println!("2. Enter your own installation path");

    print!("\nEnter new installation path (or Enter for default): ");
    io::stdout().flush().ok();
    let mut input = String::new();
    io::stdin().read_line(&mut input).map_err(|e| PortableSourceError::installation(format!("Failed to read input: {}", e)))?;
    let input = input.trim();

    let new_path = if input.is_empty() { default_path } else { validate_and_get_path(input)? };

    println!("\nNew installation path: {}", new_path.display());
    if new_path.exists() && fs::read_dir(&new_path).map(|mut it| it.next().is_some()).unwrap_or(false) {
        loop {
            print!("Continue? (y/n): ");
            io::stdout().flush().ok();
            let mut confirm = String::new();
            io::stdin().read_line(&mut confirm).ok();
            let c = confirm.trim().to_lowercase();
            if c == "y" || c == "yes" { break; }
            if c == "n" || c == "no" { println!("Path change cancelled."); return Ok(()); }
            println!("Please enter 'y' or 'n'");
        }
    }

    save_install_path_to_registry(&new_path)?;
    config_manager.set_install_path(new_path.clone())?;
    log::info!("[OK] Installation path successfully changed");
    log::info!("New path: {:?}", new_path);
    log::info!("Restart PortableSource to apply changes");
    Ok(())
}

/// High-level application facade similar to Python's PortableSourceApp
pub struct PortableSourceApp {
    pub install_path: Option<PathBuf>,
    pub config_manager: Option<ConfigManager>,
    pub environment_manager: Option<PortableEnvironmentManager>,
    pub repository_installer: Option<RepositoryInstaller>,
}

impl PortableSourceApp {
    pub fn new() -> Self {
        Self { install_path: None, config_manager: None, environment_manager: None, repository_installer: None }
    }

    pub fn initialize(&mut self, install_path: Option<PathBuf>) -> Result<()> {
        if let Some(p) = install_path {
            let p = validate_and_create_path(&p)?;
            save_install_path_to_registry(&p)?;
            self.install_path = Some(p);
        } else {
            self.install_path = Some(self.get_installation_path_interactive()?);
        }

        let install_path = self.install_path.clone().unwrap();
        create_directory_structure(&install_path)?;

        let mut cfg = ConfigManager::new(None)?;
        if cfg.get_config().install_path.as_os_str().is_empty() {
            cfg.set_install_path(install_path.clone())?;
        }
        self.environment_manager = Some(PortableEnvironmentManager::new(install_path.clone()));
        self.repository_installer = Some(RepositoryInstaller::new(install_path.clone(), cfg.clone()));
        self.config_manager = Some(cfg);
        Ok(())
    }

    fn get_installation_path_interactive(&self) -> Result<PathBuf> {
        if let Some(path) = load_install_path_from_registry()? { return Ok(path); }

        use std::io::{self, Write};
        println!("\n============================================================");
        println!("PORTABLESOURCE INSTALLATION PATH SETUP");
        println!("============================================================");

        let default_path = PathBuf::from("C:/PortableSource");
        println!("\nDefault path will be used: {}", default_path.display());
        println!("\nYou can:");
        println!("1. Press Enter to use the default path");
        println!("2. Enter your own installation path");

        print!("\nEnter installation path (or Enter for default): ");
        io::stdout().flush().ok();
        let mut input = String::new();
        io::stdin().read_line(&mut input).ok();
        let input = input.trim();

        let chosen = if input.is_empty() { default_path } else { validate_and_get_path(input)? };
        println!("\nChosen installation path: {}", chosen.display());

        if chosen.exists() && fs::read_dir(&chosen).map(|mut it| it.next().is_some()).unwrap_or(false) {
            loop {
                print!("Continue? (y/n): ");
                io::stdout().flush().ok();
                let mut confirm = String::new();
                io::stdin().read_line(&mut confirm).ok();
                let c = confirm.trim().to_lowercase();
                if c == "y" || c == "yes" { break; }
                if c == "n" || c == "no" { return Err(PortableSourceError::installation("Installation cancelled")); }
                println!("Please enter 'y' or 'n'");
            }
        }

        save_install_path_to_registry(&chosen)?;
        Ok(chosen)
    }

    pub async fn setup_environment(&mut self) -> Result<()> {
        if self.environment_manager.is_none() { return Err(PortableSourceError::environment("Environment manager not initialized")); }
        let env_mgr = self.environment_manager.as_ref().unwrap();
        env_mgr.setup_environment().await?;

        if let Some(cfg) = self.config_manager.as_mut() {
            let gpu_detector = GpuDetector::new();
            if let Some(gpu) = gpu_detector.get_best_gpu()? {
                let gpu_cfg = gpu_detector.create_gpu_config(&gpu, cfg);
                cfg.get_config_mut().gpu_config = Some(gpu_cfg);
                cfg.save_config()?;
            }
            cfg.get_config_mut().environment_setup_completed = true;
            cfg.save_config()?;
        }
        Ok(())
    }

    pub async fn install_repository(&mut self, repo: &str) -> Result<()> {
        let installer = self.repository_installer.as_mut().ok_or_else(|| PortableSourceError::repository("Repository installer not initialized"))?;
        installer.install_repository(repo).await
    }

    pub async fn update_repository(&mut self, repo: &str) -> Result<()> {
        let installer = self.repository_installer.as_mut().ok_or_else(|| PortableSourceError::repository("Repository installer not initialized"))?;
        installer.update_repository(repo).await
    }

    pub fn delete_repository(&self, repo: &str) -> Result<()> {
        let installer = self.repository_installer.as_ref().ok_or_else(|| PortableSourceError::repository("Repository installer not initialized"))?;
        installer.delete_repository(repo)
    }

    pub fn list_installed_repositories(&self) -> Result<Vec<String>> {
        let installer = self.repository_installer.as_ref().ok_or_else(|| PortableSourceError::repository("Repository installer not initialized"))?;
        installer.list_repositories()
    }

    pub fn show_system_info_with_repos(&self) -> Result<()> {
        let install_path = self.install_path.as_ref().ok_or_else(|| PortableSourceError::installation("Installation path not initialized"))?;
        let cfg = self.config_manager.as_ref().ok_or_else(|| PortableSourceError::config("Config manager not initialized"))?;
        let env_ref = self.environment_manager.as_ref();
        show_system_info_detailed(install_path, cfg, env_ref)?;
        if let Ok(repos) = self.list_installed_repositories() {
            log::info!("  - Installed repositories: {}", repos.len());
            for repo in repos {
                log::info!("    * {}", repo);
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_format_file_size() {
        assert_eq!(format_file_size(0), "0 B");
        assert_eq!(format_file_size(512), "512 B");
        assert_eq!(format_file_size(1024), "1.0 KB");
        assert_eq!(format_file_size(1536), "1.5 KB");
        assert_eq!(format_file_size(1048576), "1.0 MB");
    }
    
    #[test]
    fn test_is_command_available() {
        // These should be available on most systems
        assert!(is_command_available("cargo"));
        // This should not be available
        assert!(!is_command_available("nonexistent_command_12345"));
    }
}
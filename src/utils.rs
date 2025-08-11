//! Utility functions for PortableSource

use crate::{Result, PortableSourceError};
use crate::config::ConfigManager;
use crate::envs_manager::PortableEnvironmentManager;
use crate::repository_installer::RepositoryInstaller;
use crate::gpu::{GpuDetector, GpuType};
use std::path::{Path, PathBuf};
#[cfg(windows)]
use winreg::enums::*;
#[cfg(windows)]
use winreg::RegKey;
#[cfg(unix)]
use libc;
use std::process::Command;
use std::fs;
use std::time::Duration;

const REGISTRY_KEY: &str = r"Software\PortableSource";
const INSTALL_PATH_VALUE: &str = "InstallPath";

/// Save installation path (Windows: registry; Linux: /etc file)
#[cfg(windows)]
pub fn save_install_path_to_registry(install_path: &Path) -> Result<()> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (key, _) = hkcu.create_subkey(REGISTRY_KEY)
        .map_err(|e| PortableSourceError::Registry(format!("Failed to create registry key: {}", e)))?;
    key.set_value(INSTALL_PATH_VALUE, &install_path.to_string_lossy().to_string())
        .map_err(|e| PortableSourceError::Registry(format!("Failed to set registry value: {}", e)))?;
    log::info!("Installation path saved to registry: {:?}", install_path);
    Ok(())
}

#[cfg(unix)]
pub fn save_install_path_to_registry(install_path: &Path) -> Result<()> {
    // Emulate registry with file
    if unsafe { libc::geteuid() } != 0 {
        return Err(PortableSourceError::Registry("Must be root to save install path on Linux".into()));
    }
    let path_file = std::path::Path::new("/etc/portablesource").join("install_path");
    if let Some(parent) = path_file.parent() { std::fs::create_dir_all(parent)?; }
    std::fs::write(&path_file, install_path.to_string_lossy().as_bytes())
        .map_err(|e| PortableSourceError::Registry(format!("Failed to write {}: {}", path_file.display(), e)))?;
    log::info!("Installation path saved to {}", path_file.display());
    Ok(())
}

/// Delete installation path from Windows registry
#[cfg(windows)]
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

#[cfg(unix)]
pub fn delete_install_path_from_registry() -> Result<()> {
    if unsafe { libc::geteuid() } != 0 {
        return Err(PortableSourceError::Registry("Must be root to delete install path on Linux".into()));
    }
    let path_file = std::path::Path::new("/etc/portablesource").join("install_path");
    if path_file.exists() { let _ = std::fs::remove_file(&path_file); }
    log::info!("Installation path deleted: {}", path_file.display());
    Ok(())
}

/// Load installation path from Windows registry
#[cfg(windows)]
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

#[cfg(unix)]
pub fn load_install_path_from_registry() -> Result<Option<PathBuf>> {
    let path_file = std::path::Path::new("/etc/portablesource").join("install_path");
    if !path_file.exists() { return Ok(None); }
    let content = std::fs::read_to_string(&path_file)
        .map_err(|e| PortableSourceError::Registry(format!("Failed to read {}: {}", path_file.display(), e)))?;
    let p = PathBuf::from(content.trim());
    Ok(Some(p))
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
    #[cfg(windows)]
    let directories = [
        install_path.join("ps_env"),
        install_path.join("repos"),
        install_path.join("envs"),
    ];
    #[cfg(unix)]
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

// ===== Linux helpers for install path and micromamba setup =====

#[cfg(unix)]
pub fn is_root() -> bool {
    let euid = unsafe { libc::geteuid() };
    euid == 0
}

#[cfg(unix)]
pub fn default_install_path_linux() -> PathBuf {
    if is_root() {
        PathBuf::from("/root/portablesource")
    } else {
        if let Some(home) = dirs::home_dir() {
            return home.join("portablesource");
        }
        PathBuf::from("./portablesource")
    }
}

#[cfg(unix)]
pub fn prompt_install_path_linux(default: &Path) -> Result<PathBuf> {
    use std::io::{self, Write};
    println!(
        "[{}] — it is base install path, do you like it, or customize?\nEnter new path or press Enter to accept:",
        default.display()
    );
    print!("> ");
    io::stdout().flush().ok();
    let mut input = String::new();
    io::stdin().read_line(&mut input).ok();
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return validate_and_create_path(default);
    }
    let chosen = PathBuf::from(trimmed);
    validate_and_create_path(&chosen)
}

/// Simple HTTP(S) download helper
pub fn download_file(url: &str, destination: &Path) -> Result<()> {
    use reqwest::blocking::Client;
    use std::io::copy;
    if let Some(parent) = destination.parent() { std::fs::create_dir_all(parent)?; }
    let client = Client::builder().timeout(Duration::from_secs(600)).build()?;
    let mut resp = client.get(url).send()
        .map_err(|e| PortableSourceError::environment(format!("Failed to GET {}: {}", url, e)))?;
    if !resp.status().is_success() {
        return Err(PortableSourceError::environment(format!("Download failed: HTTP {}", resp.status())));
    }
    let mut file = std::fs::File::create(destination)?;
    copy(&mut resp, &mut file)?;
    Ok(())
}

#[cfg(unix)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LinuxMode { Cloud, Desk }

#[cfg(unix)]
pub fn detect_linux_mode() -> LinuxMode {
    let has = |bin: &str| is_command_available(bin);
    if is_root() && (has("nvcc")) && (has("git")) && (has("python3") || has("python")) {
        LinuxMode::Cloud
    } else {
        LinuxMode::Desk
    }
}

#[cfg(unix)]
pub fn detect_cuda_version_from_system() -> Option<crate::config::CudaVersionLinux> {
    let out = std::process::Command::new("nvcc").arg("--version").output().ok()?;
    if !out.status.success() { return None; }
    let stdout = String::from_utf8_lossy(&out.stdout).to_lowercase();
    for line in stdout.lines() {
        if line.contains("cuda compilation tools") && line.contains("release") {
            let ver = line.split_whitespace().filter(|s| s.chars().next().map(|c| c.is_ascii_digit()).unwrap_or(false)).next().unwrap_or("");
            if ver.starts_with("12.8") { return Some(crate::config::CudaVersionLinux::Cuda128); }
            if ver.starts_with("12.6") { return Some(crate::config::CudaVersionLinux::Cuda126); }
            if ver.starts_with("12.4") { return Some(crate::config::CudaVersionLinux::Cuda124); }
            if ver.starts_with("12.1") { return Some(crate::config::CudaVersionLinux::Cuda121); }
            if ver.starts_with("11.8") { return Some(crate::config::CudaVersionLinux::Cuda118); }
        }
    }
    None
}

#[cfg(unix)]
fn cuda_version_to_runtime_spec(v: &crate::config::CudaVersionLinux) -> &'static str {
    match v {
        crate::config::CudaVersionLinux::Cuda118 => "11.8",
        crate::config::CudaVersionLinux::Cuda121 => "12.1",
        crate::config::CudaVersionLinux::Cuda124 => "12.4",
        crate::config::CudaVersionLinux::Cuda126 => "12.6",
        crate::config::CudaVersionLinux::Cuda128 => "12.8",
    }
}

#[cfg(unix)]
pub fn setup_micromamba_base_env(install_path: &Path, cuda_version: Option<crate::config::CudaVersionLinux>) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    // Ensure directory layout
    create_directory_structure(install_path)?;
    let mamba_bin = install_path.join("ps_env").join("micromamba-linux-64");
    let mamba_url = "https://github.com/mamba-org/micromamba-releases/releases/download/latest/micromamba-linux-64";
    if !mamba_bin.exists() {
        download_file(mamba_url, &mamba_bin)?;
        let mut perms = std::fs::metadata(&mamba_bin)?.permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&mamba_bin, perms)?;
    }

    let base_prefix = install_path.join("ps_env").join("mamba_env");
    let mut args: Vec<String> = vec![
        "create".into(), "-y".into(), "-p".into(), base_prefix.to_string_lossy().to_string(),
        "-c".into(), "nvidia/label/tensorrt-main".into(), "-c".into(), "nvidia".into(), "-c".into(), "conda-forge".into(),
        "python=3.11".into(), "git".into(), "ffmpeg".into(),
    ];
    let mut attempted_cuda = false;
    if let Some(v) = cuda_version.as_ref() {
        let spec = cuda_version_to_runtime_spec(v);
        args.push(format!("cuda-runtime={}", spec));
        args.push("cudnn".into());
        args.push("tensorrt".into());
        attempted_cuda = true;
    }
    // auto-accept ToS/licenses
    let status = std::process::Command::new(&mamba_bin)
        .env("MAMBA_ALWAYS_YES", "1")
        .args(args)
        .status()
        .map_err(|e| PortableSourceError::environment(format!("Failed to run micromamba: {}", e)))?;
    if !status.success() {
        return Err(PortableSourceError::environment("micromamba create failed".into()));
    }
    // Verify CUDA runtime presence on DESK: libcudart.so* must exist if we attempted CUDA
    if attempted_cuda {
        let lib_dir = base_prefix.join("lib");
        let lib64_dir = base_prefix.join("lib64");
        let mut found = false;
        for dir in [&lib_dir, &lib64_dir] {
            if dir.exists() {
                if let Ok(read) = std::fs::read_dir(dir) {
                    for e in read.flatten() {
                        if let Some(name) = e.file_name().to_str() {
                            if name.starts_with("libcudart.so") { found = true; break; }
                        }
                    }
                }
            }
            if found { break; }
        }
        if !found {
            // Fallback: try install TensorRT via pip from NVIDIA PyPI if conda TRT missing
            let py = base_prefix.join("bin").join("python");
            if py.exists() {
                let pip_status = std::process::Command::new(&py)
                    .args(["-m","pip","install","--extra-index-url","https://pypi.nvidia.com","nvidia-tensorrt"])
                    .status()
                    .map_err(|e| PortableSourceError::environment(format!("pip fallback failed: {}", e)))?;
                if !pip_status.success() {
                    return Err(PortableSourceError::environment("CUDA runtime verification failed: libcudart not found".into()));
                }
            }
            // Recheck libcudart after pip fallback (usually not provided by TRT, но оставим на случай будущих wheels)
            let mut ok = false;
            for dir in [&lib_dir, &lib64_dir] {
                if dir.exists() {
                    if let Ok(read) = std::fs::read_dir(dir) {
                        for e in read.flatten() {
                            if let Some(name) = e.file_name().to_str() {
                                if name.starts_with("libcudart.so") { ok = true; break; }
                            }
                        }
                    }
                }
                if ok { break; }
            }
            if !ok { return Err(PortableSourceError::environment("CUDA runtime verification failed: libcudart not found".into())); }
        }
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
    
    // Prefer winget if available (often faster and more reliable)
    if which::which("winget").is_ok() {
        let (_url, args) = crate::config::ConfigManager::new(None)
            .map(|cm| cm.msvc_bt_config())
            .unwrap_or_else(|_| (
                "https://aka.ms/vs/17/release/vs_buildtools.exe".to_string(),
                " --quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.NativeDesktop --add Microsoft.VisualStudio.Component.VC.CMake.Project --add Microsoft.VisualStudio.Component.VC.Llvm.Clang".to_string()
            ));

        log::info!("Trying to install MSVC Build Tools via winget...");
        let mut cmd = Command::new("winget");
        cmd.args([
            "install",
            "Microsoft.VisualStudio.2022.BuildTools",
            "--silent",
            "--accept-package-agreements",
            "--accept-source-agreements",
            "--disable-interactivity",
            "--override",
            &format!("\"{}\"", args.trim()),
        ]);
        let status = cmd.status();
        match status {
            Ok(st) if st.success() => {
                log::info!("winget installation completed successfully");
                return Ok(());
            }
            Ok(st) => {
                log::warn!("winget returned non-zero exit code: {:?}. Falling back to direct bootstrapper.", st.code());
            }
            Err(e) => {
                log::warn!("winget not usable: {}. Falling back to direct bootstrapper.", e);
            }
        }
    }

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
    // Best-effort cleanup of previous leftover/locked file
    if installer_path.exists() { let _ = std::fs::remove_file(&installer_path); }

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
    // Ensure data is fully flushed and file handle is closed before executing (Windows lock avoidance)
    let _ = file.sync_all();
    drop(file);

    // Run installer
    log::info!("Running installer (this may take a while)...");
    #[cfg(windows)]
    let status = Command::new(&installer_path)
        .args(args.split_whitespace())
        .creation_flags(0x08000000) // CREATE_NO_WINDOW
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

#[cfg(unix)]
pub fn prepare_linux_system() -> Result<()> {
    use std::process::Command;
    // Ожидается запуск от root для установки пакетов
    let is_root = unsafe { libc::geteuid() } == 0;
    if !is_root {
        log::warn!("Not running as root. Skipping package installation. Some steps may fail.");
        return Ok(());
    }

    // 1) Определяем пакетный менеджер
    let pm = linux_detect_package_manager();
    if matches!(pm, LinuxPackageManager::Unknown) {
        log::warn!("Unsupported package manager. Skipping package installation.");
        return Ok(());
    }

    // 2) Проверяем требования и формируем отчёт
    let missing = linux_check_requirements(pm.clone());
    println!("\n=== Linux requirements check ===");
    for (tool, status) in linux_collect_tool_status() {
        println!("- {}: {}", tool.0, status);
    }
    if missing.is_empty() {
        println!("All required packages are present.");
        return Ok(());
    }
    println!("\nMissing packages to install ({}):", missing.len());
    for (_tool, pkg) in &missing { println!("  - {}", pkg); }

    // 3) Устанавливаем только недостающие
    install_linux_packages(pm, &missing)?;
    Ok(())
}

#[cfg(windows)]
pub fn prepare_linux_system() -> Result<()> { Ok(()) }

#[cfg(unix)]
#[derive(Clone, Debug, PartialEq, Eq)]
enum LinuxPackageManager { Apt, Dnf, Yum, Pacman, Unknown }

#[cfg(unix)]
fn linux_detect_package_manager() -> LinuxPackageManager {
    if which::which("apt-get").is_ok() { return LinuxPackageManager::Apt; }
    if which::which("dnf").is_ok() { return LinuxPackageManager::Dnf; }
    if which::which("yum").is_ok() { return LinuxPackageManager::Yum; }
    if which::which("pacman").is_ok() { return LinuxPackageManager::Pacman; }
    LinuxPackageManager::Unknown
}

#[cfg(unix)]
fn linux_collect_tool_status() -> Vec<((&'static str), String)> {
    use std::process::Command;
    let mut out = Vec::new();
    let has = |bin: &str| which::which(bin).is_ok();
    out.push(("git", if has("git") { "OK".to_string() } else { "Missing".to_string() }));
    out.push(("python3", if has("python3") { "OK".to_string() } else { "Missing".to_string() }));
    // venv module
    let venv_ok = Command::new("python3").arg("-c").arg("import venv").status().map(|s| s.success()).unwrap_or(false);
    out.push(("python3-venv", if venv_ok { "OK".to_string() } else { "Missing".to_string() }));
    // pip3
    let pip_ok = has("pip3") || Command::new("python3").arg("-m").arg("pip").arg("--version").status().map(|s| s.success()).unwrap_or(false);
    out.push(("python3-pip", if pip_ok { "OK".to_string() } else { "Missing".to_string() }));
    // dev headers: python3-dev / python3-devel / (arch: part of python)
    let pyconf_ok = has("python3-config") || Command::new("python3").arg("-c").arg("import sysconfig;print(sysconfig.get_config_var('INCLUDEPY') or '')").status().map(|s| s.success()).unwrap_or(false);
    out.push(("python3-dev", if pyconf_ok { "OK".to_string() } else { "Missing".to_string() }));
    out.push(("ffmpeg", if has("ffmpeg") { "OK".to_string() } else { "Missing".to_string() }));
    // optional nvcc
    let nvcc_ok = has("nvcc");
    out.push(("nvcc (optional)", if nvcc_ok { "OK".to_string() } else { "Not found".to_string() }));
    out
}

#[cfg(unix)]
fn linux_check_requirements(pm: LinuxPackageManager) -> Vec<(String, String)> {
    // Возвращаем список (tool, pm_package_name) которых не хватает
    let statuses = linux_collect_tool_status();
    let mut missing: Vec<(String, String)> = Vec::new();
    let map_pkg = |tool: &str| -> Option<&'static str> {
        match pm {
            LinuxPackageManager::Apt => match tool {
                "git" => Some("git"),
                // prefer meta package on Debian/Ubuntu
                "python3" => Some("python3-full"),
                "python3-venv" => Some("python3-venv"),
                "python3-pip" => Some("python3-pip"),
                "python3-dev" => Some("python3-dev"),
                "ffmpeg" => Some("ffmpeg"),
                _ => None,
            },
            LinuxPackageManager::Dnf | LinuxPackageManager::Yum => match tool {
                "git" => Some("git"),
                "python3" => Some("python3"),
                "python3-venv" => None, // в dnf/yum venv обычно внутри python3
                "python3-pip" => Some("python3-pip"),
                "python3-dev" => Some("python3-devel"),
                "ffmpeg" => Some("ffmpeg"),
                _ => None,
            },
            LinuxPackageManager::Pacman => match tool {
                "git" => Some("git"),
                "python3" => Some("python"),
                "python3-venv" => None, // в Arch venv в составе python
                "python3-pip" => Some("python-pip"),
                "python3-dev" => None, // dev headers идут в составе python в Arch
                "ffmpeg" => Some("ffmpeg"),
                _ => None,
            },
            LinuxPackageManager::Unknown => None,
        }
    };
    for (tool, status) in statuses {
        if status == "Missing" {
            if let Some(pkg) = map_pkg(tool) {
                missing.push((tool.to_string(), pkg.to_string()));
            }
        }
    }
    missing
}

#[cfg(unix)]
fn install_linux_packages(pm: LinuxPackageManager, missing: &Vec<(String, String)>) -> Result<()> {
    use std::process::Command;
    if missing.is_empty() { return Ok(()); }
    let pkgs: Vec<String> = missing.iter().map(|(_, p)| p.clone()).collect();
    match pm {
        LinuxPackageManager::Apt => {
            let _ = Command::new("apt-get").arg("update").status();
            let mut cmd = Command::new("apt-get");
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("apt-get failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("apt-get install failed")); }
        }
        LinuxPackageManager::Dnf => {
            let mut cmd = Command::new("dnf");
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("dnf failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("dnf install failed")); }
        }
        LinuxPackageManager::Yum => {
            let mut cmd = Command::new("yum");
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("yum failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("yum install failed")); }
        }
        LinuxPackageManager::Pacman => {
            let mut cmd = Command::new("pacman");
            cmd.arg("-Sy").arg("--noconfirm");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("pacman failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("pacman install failed")); }
        }
        LinuxPackageManager::Unknown => {}
    }
    Ok(())
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
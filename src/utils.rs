//! Utility functions for PortableSource

use crate::{Result, PortableSourceError};
use crate::config::ConfigManager;
use crate::envs_manager::PortableEnvironmentManager;
use crate::repository_installer::RepositoryInstaller;
use crate::gpu::{GpuDetector, GpuType};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::fs;
use std::time::Duration;

#[cfg(unix)]
use indicatif::{ProgressBar, ProgressStyle};
#[cfg(unix)]
use libc;

#[cfg(windows)]
const REGISTRY_KEY: &str = r"Software\PortableSource";
#[cfg(windows)]
const INSTALL_PATH_VALUE: &str = "InstallPath";
#[cfg(windows)]
use winreg::enums::*;
#[cfg(windows)]
use winreg::RegKey;

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
    // Save install path to ~/.portablesource
    let config_file = if is_root() {
        PathBuf::from("/root/.portablesource")
    } else {
        if let Ok(username) = std::env::var("USER") {
            PathBuf::from(format!("/home/{}/.portablesource", username))
        } else if let Some(home) = dirs::home_dir() {
            home.join(".portablesource")
        } else {
            PathBuf::from("./.portablesource")
        }
    };
    
    std::fs::write(&config_file, install_path.to_string_lossy().as_bytes())
        .map_err(|e| PortableSourceError::Registry(format!("Failed to write {}: {}", config_file.display(), e)))?;
    log::info!("Installation path saved to {}", config_file.display());
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
    // Remove ~/.portablesource file
    let config_file = if is_root() {
        PathBuf::from("/root/.portablesource")
    } else {
        if let Ok(username) = std::env::var("USER") {
            PathBuf::from(format!("/home/{}/.portablesource", username))
        } else if let Some(home) = dirs::home_dir() {
            home.join(".portablesource")
        } else {
            PathBuf::from("./.portablesource")
        }
    };
    
    if config_file.exists() { 
        let _ = std::fs::remove_file(&config_file); 
    }

    // Best-effort: also remove legacy global file if running as root
    let etc_file = std::path::Path::new("/etc/portablesource").join("install_path");
    if is_root() && etc_file.exists() { 
        let _ = std::fs::remove_file(&etc_file); 
    }
    log::info!("Installation path deleted (user and legacy locations cleaned where possible)");
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
    // Load install path from ~/.portablesource
    let config_file = if is_root() {
        PathBuf::from("/root/.portablesource")
    } else {
        if let Ok(username) = std::env::var("USER") {
            PathBuf::from(format!("/home/{}/.portablesource", username))
        } else if let Some(home) = dirs::home_dir() {
            home.join(".portablesource")
        } else {
            PathBuf::from("./.portablesource")
        }
    };
    
    if config_file.exists() {
        let content = std::fs::read_to_string(&config_file)
            .map_err(|e| PortableSourceError::Registry(format!("Failed to read {}: {}", config_file.display(), e)))?;
        return Ok(Some(PathBuf::from(content.trim())));
    }
    
    // Back-compat: legacy global file
    let path_file = std::path::Path::new("/etc/portablesource").join("install_path");
    if path_file.exists() {
        let content = std::fs::read_to_string(&path_file)
            .map_err(|e| PortableSourceError::Registry(format!("Failed to read {}: {}", path_file.display(), e)))?;
        return Ok(Some(PathBuf::from(content.trim())));
    }
    
    Ok(None)
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

/// Определяет, является ли это первой установкой (отсутствуют директории среды)
pub fn is_first_installation(install_path: &Path) -> bool {
    let ps_env = install_path.join("ps_env");
    let repos = install_path.join("repos");
    let envs = install_path.join("envs");
    
    // Первая установка, если нет ни одной из ключевых директорий
    !ps_env.exists() && !repos.exists() && !envs.exists()
}

/// Копирует текущий exe файл в путь установки
pub fn copy_executable_to_install_path(install_path: &Path) -> Result<()> {
    let current_exe = std::env::current_exe()
        .map_err(|e| PortableSourceError::installation(
            format!("Failed to get current executable path: {}", e)
        ))?;
    
    let exe_name = current_exe.file_name()
        .ok_or_else(|| PortableSourceError::installation(
            "Failed to get executable name".to_string()
        ))?;
    
    let target_exe = install_path.join(exe_name);
    
    // Не копируем, если уже находимся в целевой директории
    if current_exe == target_exe {
        log::info!("Executable already in target location: {:?}", target_exe);
        return Ok(());
    }
    
    // Создаем директорию установки если её нет
    std::fs::create_dir_all(install_path)
        .map_err(|e| PortableSourceError::installation(
            format!("Failed to create install directory {:?}: {}", install_path, e)
        ))?;
    
    // Копируем exe файл
    std::fs::copy(&current_exe, &target_exe)
        .map_err(|e| PortableSourceError::installation(
            format!("Failed to copy executable from {:?} to {:?}: {}", current_exe, target_exe, e)
        ))?;
    
    log::info!("Executable copied to: {:?}", target_exe);
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
        // Get current username and construct path /home/{username}/portablesource
        if let Ok(username) = std::env::var("USER") {
            PathBuf::from(format!("/home/{}/portablesource", username))
        } else if let Some(home) = dirs::home_dir() {
            home.join("portablesource")
        } else {
            PathBuf::from("./portablesource")
        }
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
    // Env override: PORTABLESOURCE_MODE=CLOUD|DESK
    if let Ok(mode) = std::env::var("PORTABLESOURCE_MODE") {
        let m = mode.to_lowercase();
        if m == "cloud" { return LinuxMode::Cloud; }
        if m == "desk" { return LinuxMode::Desk; }
    }
    
    // Check if CUDA is available via nvcc command
    if let Ok(output) = std::process::Command::new("nvcc")
        .arg("--version")
        .output() {
        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            // Check if output contains "Cuda compilation tools"
            if stdout.contains("Cuda compilation tools") {
                return LinuxMode::Cloud;
            }
        }
    }
    
    // If no CUDA or nvcc command failed, use DESK mode
    LinuxMode::Desk
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
    // Correct latest asset URL + fallback pinned version
    let mamba_url_latest = "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64";
    let mamba_url_fallback = "https://github.com/mamba-org/micromamba-releases/releases/download/2.3.1-0/micromamba-linux-64";
    if !mamba_bin.exists() {
        if let Err(e) = download_file(mamba_url_latest, &mamba_bin) {
            log::warn!("micromamba latest download failed: {} — trying fallback", e);
            download_file(mamba_url_fallback, &mamba_bin)?;
        }
        let mut perms = std::fs::metadata(&mamba_bin)?.permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(&mamba_bin, perms)?;
    }

    let base_prefix = install_path.join("ps_env").join("mamba_env");
    let root_prefix = install_path.join("ps_env");
    let mut args: Vec<String> = vec![
        "create".into(),
        "-y".into(),
        "-r".into(), root_prefix.to_string_lossy().to_string(),
        "-p".into(), base_prefix.to_string_lossy().to_string(),
        "-c".into(), "nvidia".into(), "-c".into(), "conda-forge".into(),
        "python=3.11".into(), "git".into(), "ffmpeg".into(),
    ];
    let mut attempted_cuda = false;
    if let Some(v) = cuda_version.as_ref() {
        let spec = cuda_version_to_runtime_spec(v);
        args.push(format!("cuda-toolkit={}", spec));
        args.push("cudnn".into());
        attempted_cuda = true;
    }
    // auto-accept ToS/licenses
    let mut child = std::process::Command::new(&mamba_bin)
        .env("MAMBA_ALWAYS_YES", "true")
        .env("MAMBA_NO_RC", "true")
        .env("MAMBA_ROOT_PREFIX", &root_prefix)
        .current_dir(&root_prefix)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::inherit())
        .args(args)
        .spawn()
        .map_err(|e| PortableSourceError::environment(format!("Failed to run micromamba: {}", e)))?;

    let pb = ProgressBar::new_spinner();
    pb.set_style(ProgressStyle::with_template("{spinner} {msg}").unwrap());
    pb.enable_steady_tick(Duration::from_millis(120));
    if let Some(out) = child.stdout.take() {
        use std::io::{BufRead, BufReader};
        let reader = BufReader::new(out);
        for line in reader.lines().flatten() {
            let l = line.trim();
            if !l.is_empty() {
                pb.set_message(l.to_string());
            }
        }
    }
    let status = child.wait().map_err(|e| PortableSourceError::environment(format!("micromamba wait failed: {}", e)))?;
    if status.success() {
        pb.finish_with_message("micromamba: done");
        // Verify env created
        let py = base_prefix.join("bin").join("python");
        if !py.exists() {
            return Err(PortableSourceError::environment(format!(
                "micromamba create succeeded but python not found at {}",
                py.display()
            )));
        }
    } else {
        pb.finish_with_message("micromamba: failed");
        return Err(PortableSourceError::environment("micromamba create failed"));
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
                    return Err(PortableSourceError::environment("CUDA runtime verification failed: libcudart not found"));
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
            if !ok { return Err(PortableSourceError::environment("CUDA runtime verification failed: libcudart not found")); }
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

#[cfg(windows)]
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

#[cfg(not(windows))]
fn check_msvc_registry() -> bool {
    println!("Your os dont support!");
    false
}

/// Download and run MSVC Build Tools installer (blocking)
pub fn install_msvc_build_tools() -> Result<()> {
    use reqwest::blocking::Client;
    use std::io::copy;
    
    // Prefer winget if available (often faster and more reliable)
    // Helper: choose one SDK depending on OS build (Win11 vs Win10)
    #[cfg(windows)]
    fn detect_windows_sdk_component() -> &'static str {
        use winreg::enums::*;
        use winreg::RegKey;
        let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
        if let Ok(key) = hklm.open_subkey("SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion") {
            if let Ok(build_str) = key.get_value::<String, _>("CurrentBuildNumber") {
                if build_str.parse::<u32>().unwrap_or(0) >= 22000 {
                    return "Microsoft.VisualStudio.Component.Windows11SDK.26100";
                }
            }
        }
        "Microsoft.VisualStudio.Component.Windows10SDK.19041"
    }
    #[cfg(not(windows))]
    fn detect_windows_sdk_component() -> &'static str { "Microsoft.VisualStudio.Component.Windows10SDK.19041" }

    if which::which("winget").is_ok() {
        let args = concat!(
            " --quiet --wait --norestart --nocache",
            " --add Microsoft.VisualStudio.Workload.NativeDesktop",
            " --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            " --add Microsoft.VisualStudio.Component.VC.CMake.Project",
            " --add Microsoft.VisualStudio.Component.VC.Llvm.Clang",
            " --add Microsoft.VisualStudio.Component.VC.AddressSanitizer",
            " --add Microsoft.VisualStudio.Component.VC.Redist.14.Latest"
        ).to_string();

        log::info!("Trying to install MSVC Build Tools via winget...");
        let mut cmd = Command::new("winget");
        cmd.arg("install")
            .arg("Microsoft.VisualStudio.2022.BuildTools")
            .arg("--silent")
            .arg("--accept-package-agreements")
            .arg("--accept-source-agreements")
            .arg("--disable-interactivity")
            // Важно: без обрамляющих кавычек, Command сам корректно экранирует аргумент
            .arg("--override")
            .arg(format!("{} --add {}", args.trim(), detect_windows_sdk_component()))
            // На некоторых системах нужно явно указать источник
            .arg("--source").arg("winget");
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
    let url = "https://aka.ms/vs/17/release/vs_buildtools.exe".to_string();
    let args = format!(
        "{} --add {}",
        concat!(
            " --quiet --wait --norestart --nocache",
            " --add Microsoft.VisualStudio.Workload.NativeDesktop",
            " --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
            " --add Microsoft.VisualStudio.Component.VC.CMake.Project",
            " --add Microsoft.VisualStudio.Component.VC.Llvm.Clang",
            " --add Microsoft.VisualStudio.Component.VC.AddressSanitizer",
            " --add Microsoft.VisualStudio.Component.VC.Redist.14.Latest"
        ),
        detect_windows_sdk_component()
    );

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
    let status = {
        let mut cmd = Command::new(&installer_path);
        cmd.args(args.split_whitespace());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        cmd.status()
            .map_err(|e| PortableSourceError::command(format!("Failed to start installer: {}", e)))?
    };

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
    // Ожидается запуск от root для установки пакетов; если не root — пробуем sudo -n
    let is_root = unsafe { libc::geteuid() } == 0;
    let use_sudo = !is_root && which::which("sudo").is_ok();
    if !is_root && !use_sudo {
        log::warn!("Not running as root and sudo not available. Skipping package installation. Some steps may fail.");
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
        println!("- {}: {}", tool, status);
    }
    if missing.is_empty() {
        println!("All required packages are present.");
        return Ok(());
    }
    println!("\nMissing packages to install ({}):", missing.len());
    for (_tool, pkg) in &missing { println!("  - {}", pkg); }

    // 3) Устанавливаем только недостающие
    install_linux_packages(pm, &missing, use_sudo)?;
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
fn linux_collect_tool_status() -> Vec<(&'static str, String)> {
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
fn install_linux_packages(pm: LinuxPackageManager, missing: &Vec<(String, String)>, use_sudo: bool) -> Result<()> {
    use std::process::Command;
    if missing.is_empty() { return Ok(()); }
    let pkgs: Vec<String> = missing.iter().map(|(_, p)| p.clone()).collect();
    let sudo = |_cmd: &str| if use_sudo { Some("sudo") } else { None };
    let sudo_args: [&str; 1] = ["-n"]; // non-interactive
    match pm {
        LinuxPackageManager::Apt => {
            // apt-get update
            if let Some(s) = sudo("apt-get") {
                let _ = Command::new(s).args(&sudo_args).arg("apt-get").arg("update").status();
            } else {
                let _ = Command::new("apt-get").arg("update").status();
            }
            // apt-get install -y pkgs
            let mut cmd = if let Some(s) = sudo("apt-get") { let mut c = Command::new(s); c.args(&sudo_args).arg("apt-get"); c } else { Command::new("apt-get") };
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("apt-get failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("apt-get install failed")); }
        }
        LinuxPackageManager::Dnf => {
            let mut cmd = if let Some(s) = sudo("dnf") { let mut c = Command::new(s); c.args(&sudo_args).arg("dnf"); c } else { Command::new("dnf") };
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("dnf failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("dnf install failed")); }
        }
        LinuxPackageManager::Yum => {
            let mut cmd = if let Some(s) = sudo("yum") { let mut c = Command::new(s); c.args(&sudo_args).arg("yum"); c } else { Command::new("yum") };
            cmd.arg("install").arg("-y");
            for p in &pkgs { cmd.arg(p); }
            let st = cmd.status().map_err(|e| PortableSourceError::environment(format!("yum failed: {}", e)))?;
            if !st.success() { return Err(PortableSourceError::environment("yum install failed")); }
        }
        LinuxPackageManager::Pacman => {
            let mut cmd = if let Some(s) = sudo("pacman") { let mut c = Command::new(s); c.args(&sudo_args).arg("pacman"); c } else { Command::new("pacman") };
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
    
    // Available tools (cross-platform detection)
    let git_ok = which::which("git").is_ok();
    let py_ok = which::which("python3").is_ok() || which::which("python").is_ok();
    let pip_ok = which::which("pip3").is_ok() || {
        let py = if which::which("python3").is_ok() { "python3" } else { "python" };
        std::process::Command::new(py)
            .args(["-m", "pip", "--version"])
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    };
    let ffmpeg_ok = which::which("ffmpeg").is_ok();
    info.push(format!("git: {}", if git_ok { "Available" } else { "Not found" }));
    info.push(format!("python: {}", if py_ok { "Available" } else { "Not found" }));
    info.push(format!("pip: {}", if pip_ok { "Available" } else { "Not found" }));
    info.push(format!("ffmpeg: {}", if ffmpeg_ok { "Available" } else { "Not found" }));
    
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
    if config_manager.has_cuda() {
        log::info!("  - GPU: {}", config_manager.get_gpu_name());
        log::info!("  - GPU type: {:?}", config_manager.detect_current_gpu_generation());
        if let Some(cuda) = config_manager.get_cuda_version() {
            log::info!("  - CUDA version: {:?}", cuda);
        }
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
        #[cfg(windows)]
        use std::io::{self, Write};
        println!("\n============================================================");
        println!("PORTABLESOURCE INSTALLATION PATH SETUP");
        println!("============================================================");

        #[cfg(windows)]
        let default_path = PathBuf::from("C:/PortableSource");
        #[cfg(unix)]
        let default_path = default_install_path_linux();
        
        #[cfg(unix)]
        {
            let chosen = prompt_install_path_linux(&default_path)?;
            save_install_path_to_registry(&chosen)?;
            return Ok(chosen);
        }
        
        #[cfg(windows)]
        {
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
    }

    pub async fn setup_environment(&mut self) -> Result<()> {
        if self.environment_manager.is_none() { return Err(PortableSourceError::environment("Environment manager not initialized")); }
        let env_mgr = self.environment_manager.as_ref().unwrap();
        env_mgr.setup_environment().await?;

        if let Some(cfg) = self.config_manager.as_mut() {
            // GPU detection is now handled dynamically
            cfg.get_config_mut().environment_setup_completed = true;
            // Конфигурация больше не сохраняется на диск - только сессионные настройки
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

#[cfg(unix)]
pub async fn uninstall_portablesource(install_path: &PathBuf) -> Result<()> {
    use std::io::{self, Write};
    use std::fs;
    
    println!("[WARNING] This will completely remove PortableSource and all installed repositories!");
    println!("Installation path: {}", install_path.display());
    print!("Are you sure you want to continue? (yes/no): ");
    io::stdout().flush().unwrap();
    
    let mut input = String::new();
    io::stdin().read_line(&mut input).unwrap();
    
    if input.trim().to_lowercase() != "yes" {
        println!("Uninstall cancelled.");
        return Ok(());
    }
    
    println!("\n[INFO] Removing PortableSource environment...");
    
    // Remove the entire installation directory
    if install_path.exists() {
        match fs::remove_dir_all(install_path) {
            Ok(_) => println!("[SUCCESS] Environment directory removed: {}", install_path.display()),
            Err(e) => {
                log::error!("Failed to remove environment directory: {}", e);
                println!("[ERROR] Failed to remove environment directory: {}", e);
                return Err(e.into());
            }
        }
    } else {
        println!("[INFO] Environment directory not found: {}", install_path.display());
    }
    
    // Remove config directory if it exists
    if let Some(config_dir) = dirs::config_dir() {
        let portablesource_config = config_dir.join("portablesource");
        if portablesource_config.exists() {
            match fs::remove_dir_all(&portablesource_config) {
                Ok(_) => println!("[SUCCESS] Config directory removed: {}", portablesource_config.display()),
                Err(e) => println!("[WARNING] Failed to remove config directory: {}", e),
            }
        }
    }
    
    // Get the current executable path
    let current_exe = std::env::current_exe()?;
    println!("\n[INFO] Removing executable: {}", current_exe.display());
    
    // Create a self-deletion script
    let script_path = std::env::temp_dir().join("portablesource_uninstall.sh");
    let script_content = format!(
        "#!/bin/bash\nsleep 1\nrm -f '{}'\nrm -f '{}'\necho '[SUCCESS] PortableSource has been completely uninstalled.'\n",
        current_exe.display(),
        script_path.display()
    );
    
    fs::write(&script_path, script_content)?;
    
    // Make script executable
    use std::os::unix::fs::PermissionsExt;
    let mut perms = fs::metadata(&script_path)?.permissions();
    perms.set_mode(0o755);
    fs::set_permissions(&script_path, perms)?;
    
    println!("[SUCCESS] PortableSource environment has been removed.");
    println!("[INFO] Executing self-deletion...");
    
    // Execute the self-deletion script
    std::process::Command::new("bash")
        .arg(&script_path)
        .spawn()?;
    
    // Exit immediately to allow self-deletion
    std::process::exit(0);
}

pub async fn run_repository(repo: &str, install_path: &PathBuf, additional_args: &[String]) -> Result<()> {
    let repo_path = install_path.join("repos").join(repo);
    
    if !repo_path.exists() {
        println!("[ERROR] Repository '{}' not found at: {}", repo, repo_path.display());
        return Err(PortableSourceError::repository(format!("Repository '{}' not installed", repo)));
    }
    
    // Look for start script based on platform
    #[cfg(windows)]
    let start_script = repo_path.join(format!("start_{}.bat", repo));
    #[cfg(unix)]
    let start_script = repo_path.join(format!("start_{}.sh", repo));
    
    if !start_script.exists() {
        println!("[ERROR] Start script not found: {}", start_script.display());
        return Err(PortableSourceError::repository(format!("Start script for '{}' not found", repo)));
    }
    
    println!("[INFO] Running repository: {}", repo);
    println!("[INFO] Executing: {}", start_script.display());
    
    // Prepare arguments
    #[cfg(unix)]
    let args = additional_args.to_vec();
    #[cfg(windows)]
    let args = additional_args.to_vec();
    
    // Note: Docker detection and fallback logic is handled in try_run_with_fallback function
    
    if !args.is_empty() {
        println!("[INFO] Additional arguments: {}", args.join(" "));
    }
    
    // Execute the start script based on platform
    #[cfg(windows)]
    {
        let mut cmd = std::process::Command::new("cmd");
        cmd.args(["/C", start_script.to_str().unwrap()]);
        cmd.args(&args);
        
        let status = cmd.status()?;
        
        if status.success() {
            println!("[SUCCESS] Repository '{}' executed successfully", repo);
        } else {
            println!("[ERROR] Repository '{}' execution failed with exit code: {:?}", repo, status.code());
            return Err(PortableSourceError::command(format!("Repository '{}' execution failed", repo)));
        }
    }

    #[cfg(unix)]
    {
        // Try with fallback mechanism for Docker
        if is_running_in_docker() {
            if let Err(e) = try_run_with_fallback(&start_script, &additional_args, repo) {
                return Err(e);
            }
        } else {
            let mut cmd = std::process::Command::new("bash");
            cmd.arg(&start_script);
            cmd.args(&args);
            
            let status = cmd.status()?;
            
            if status.success() {
                println!("[SUCCESS] Repository '{}' executed successfully", repo);
            } else {
                println!("[ERROR] Repository '{}' execution failed with exit code: {:?}", repo, status.code());
                return Err(PortableSourceError::command(format!("Repository '{}' execution failed", repo)));
            }
        }
    }
    
    Ok(())
}

#[cfg(unix)]
fn try_run_with_fallback(start_script: &PathBuf, additional_args: &[String], repo: &str) -> Result<()> {
    // Try 1: --listen 0.0.0.0
    println!("[INFO] Trying with --listen 0.0.0.0");
    let mut args_with_listen = additional_args.to_vec();
    args_with_listen.push("--listen".to_string());
    args_with_listen.push("0.0.0.0".to_string());
    
    let mut cmd = std::process::Command::new("bash");
    cmd.arg(start_script);
    cmd.args(&args_with_listen);
    
    match cmd.status() {
        Ok(status) if status.success() => {
            println!("[SUCCESS] Repository '{}' executed successfully with --listen 0.0.0.0", repo);
            return Ok(());
        }
        Ok(status) => {
            println!("[WARNING] Failed with --listen 0.0.0.0, exit code: {:?}", status.code());
        }
        Err(e) => {
            println!("[WARNING] Failed to execute with --listen 0.0.0.0: {}", e);
        }
    }
    
    // Try 2: --listen only
    println!("[INFO] Trying with --listen only");
    let mut args_with_listen_only = additional_args.to_vec();
    args_with_listen_only.push("--listen".to_string());
    
    let mut cmd = std::process::Command::new("bash");
    cmd.arg(start_script);
    cmd.args(&args_with_listen_only);
    
    match cmd.status() {
        Ok(status) if status.success() => {
            println!("[SUCCESS] Repository '{}' executed successfully with --listen only", repo);
            return Ok(());
        }
        Ok(status) => {
            println!("[WARNING] Failed with --listen only, exit code: {:?}", status.code());
        }
        Err(e) => {
            println!("[WARNING] Failed to execute with --listen only: {}", e);
        }
    }
    
    // Try 3: No additional listen arguments
    println!("[INFO] Trying without additional listen arguments");
    let mut cmd = std::process::Command::new("bash");
    cmd.arg(start_script);
    cmd.args(additional_args);
    
    let status = cmd.status()?;
    
    if status.success() {
        println!("[SUCCESS] Repository '{}' executed successfully without listen arguments", repo);
        Ok(())
    } else {
        println!("[ERROR] All fallback attempts failed. Repository '{}' execution failed with exit code: {:?}", repo, status.code());
        Err(PortableSourceError::command(format!("Repository '{}' execution failed after all fallback attempts", repo)))
    }
}

#[cfg(unix)]
fn is_running_in_docker() -> bool {
    use std::fs;
    
    // Check if we're running in a Docker container by looking at /proc/self/cgroup
    if let Ok(cgroup_content) = fs::read_to_string("/proc/self/cgroup") {
        return cgroup_content.contains("docker");
    }
    
    // Alternative check: look for .dockerenv file
    if fs::metadata("/.dockerenv").is_ok() {
        return true;
    }
    
    false
}

#[cfg(windows)]
#[allow(dead_code)]
fn is_running_in_docker() -> bool {
    // Docker detection on Windows is more complex, for now return false
    // Could be implemented by checking environment variables or other methods
    false
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
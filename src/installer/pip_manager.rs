//! Pip manager for handling Python package installations with pip/uv support.

use crate::installer::command_runer::CommandRunner;
use crate::envs_manager::PortableEnvironmentManager;
use crate::config::ConfigManager;
use crate::PortableSourceError;
use crate::Result;
use log::{info, debug};
use std::path::{Path, PathBuf};
use std::fs;
use std::io::Write;
use serde_json::Value as JsonValue;
use toml::Value as TomlValue;

#[derive(Clone, Debug, PartialEq, Eq)]
enum PackageType {
    Regular,
    Torch,
    Onnxruntime,
    Insightface,
    Triton,
}

#[derive(Clone, Debug)]
struct PackageInfo {
    name: String,
    version: Option<String>,
    extras: Option<Vec<String>>,
    package_type: PackageType,
    original_line: String,
}

impl ToString for PackageInfo {
    fn to_string(&self) -> String {
        if let Some(v) = &self.version {
            format!("{}=={}", self.name, v)
        } else {
            self.name.clone()
        }
    }
}

#[derive(Clone, Debug, Default)]
struct InstallationPlan {
    torch_packages: Vec<PackageInfo>,
    onnx_packages: Vec<PackageInfo>,
    insightface_packages: Vec<PackageInfo>,
    triton_packages: Vec<PackageInfo>,
    regular_packages: Vec<PackageInfo>,
    torch_index_url: Option<String>,
    onnx_package_name: Option<String>,
}

struct RequirementsAnalyzer<'a> {
    config_manager: &'a ConfigManager,
}

impl<'a> RequirementsAnalyzer<'a> {
    fn new(config_manager: &'a ConfigManager) -> Self {
        Self { config_manager }
    }

    fn analyze_requirements(&self, requirements_path: &Path) -> Vec<PackageInfo> {
        let mut packages = Vec::new();
        if let Ok(content) = fs::read_to_string(requirements_path) {
            for line in content.lines() {
                if let Some(pkg) = self.parse_requirement_line(line) {
                    packages.push(pkg);
                }
            }
        }
        packages
    }

    fn parse_requirement_line(&self, line_in: &str) -> Option<PackageInfo> {
        let line = line_in.split('#').next().unwrap_or("").trim().to_string();
        if line.is_empty() || line.starts_with('-') || line.contains("--index-url") || line.contains("--extra-index-url") {
            return None;
        }
        
        // Basic parse: name[extras]==version
        let (name_part, version) = if let Some(idx) = line.find(|c: char| "=><!~".contains(c)) {
            let (n, v) = line.split_at(idx);
            (n.trim().to_string(), Some(v.trim_matches(|c| c == '=' || c == '>' || c == '<' || c == '!' || c == '~').to_string()))
        } else {
            (line.clone(), None)
        };
        
        let (name, extras_opt) = if let Some(start) = name_part.find('[') {
            let end = name_part.find(']').unwrap_or(name_part.len());
            (name_part[..start].to_string(), Some(name_part[start+1..end].split(',').map(|s| s.to_string()).collect()))
        } else {
            (name_part, None)
        };
        
        let lname = name.to_lowercase();
        let package_type = if ["torch", "torchvision", "torchaudio", "torchtext", "torchdata"].contains(&lname.as_str()) {
            PackageType::Torch
        } else if lname.starts_with("onnxruntime") {
            PackageType::Onnxruntime
        } else if lname.starts_with("insightface") {
            PackageType::Insightface
        } else if lname.starts_with("triton") {
            PackageType::Triton
        } else {
            PackageType::Regular
        };
        
        Some(PackageInfo {
            name: lname,
            version,
            extras: extras_opt,
            package_type,
            original_line: line_in.to_string(),
        })
    }

    fn create_installation_plan(&self, packages: &Vec<PackageInfo>) -> InstallationPlan {
        let mut plan = InstallationPlan::default();
        for p in packages {
            match p.package_type {
                PackageType::Torch => plan.torch_packages.push(p.clone()),
                PackageType::Onnxruntime => plan.onnx_packages.push(p.clone()),
                PackageType::Insightface => plan.insightface_packages.push(p.clone()),
                PackageType::Triton => plan.triton_packages.push(p.clone()),
                PackageType::Regular => plan.regular_packages.push(p.clone()),
            }
        }
        // torch index url
        plan.torch_index_url = Some(self.get_torch_index_url());
        // onnx package name by GPU vendor
        plan.onnx_package_name = Some(self.get_onnx_package_name());
        plan
    }

    fn get_torch_index_url(&self) -> String {
        if self.config_manager.has_cuda() {
            let gpu_name = self.config_manager.get_gpu_name();
            let gpu_generation = self.config_manager.detect_current_gpu_generation();
            let name_up = gpu_name.to_uppercase();
            let is_blackwell = name_up.contains("RTX 50") || format!("{:?}", gpu_generation).to_lowercase().contains("blackwell");
            if is_blackwell {
                return "https://download.pytorch.org/whl/nightly/cu128".into();
            }
        }
        
        #[cfg(unix)]
        {
            if let Some(cv) = crate::utils::detect_cuda_version_from_system() {
                return match cv {
                    crate::config::CudaVersionLinux::Cuda128 => "https://download.pytorch.org/whl/nightly/cu128".into(),
                    crate::config::CudaVersionLinux::Cuda126 => "https://download.pytorch.org/whl/cu126".into(),
                    crate::config::CudaVersionLinux::Cuda124 => "https://download.pytorch.org/whl/cu124".into(),
                    crate::config::CudaVersionLinux::Cuda121 => "https://download.pytorch.org/whl/cu121".into(),
                    crate::config::CudaVersionLinux::Cuda118 => "https://download.pytorch.org/whl/cu118".into(),
                };
            }
        }
        
        #[cfg(windows)]
        {
            if self.config_manager.has_cuda() {
                if let Some(cuda_version) = self.config_manager.get_cuda_version() {
                    return match cuda_version {
                        crate::config::CudaVersion::Cuda128 => "https://download.pytorch.org/whl/nightly/cu128".into(),
                        crate::config::CudaVersion::Cuda124 => "https://download.pytorch.org/whl/cu124".into(),
                        crate::config::CudaVersion::Cuda118 => "https://download.pytorch.org/whl/cu118".into(),
                    };
                }
            }
        }
        
        "https://download.pytorch.org/whl/cpu".into()
    }

    fn get_onnx_package_name(&self) -> String {
        if self.config_manager.has_cuda() {
            let gpu_name = self.config_manager.get_gpu_name();
            let up = gpu_name.to_uppercase();
            if up.contains("NVIDIA") {
                return "onnxruntime-gpu".into();
            }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) {
                return "onnxruntime-directml".into();
            }
        }
        "onnxruntime".into()
    }
}

pub struct PipManager<'a> {
    command_runner: &'a CommandRunner<'a>,
    env_manager: &'a PortableEnvironmentManager,
    config_manager: &'a ConfigManager,
}

impl<'a> PipManager<'a> {
    pub fn new(
        command_runner: &'a CommandRunner,
        env_manager: &'a PortableEnvironmentManager,
        config_manager: &'a ConfigManager,
    ) -> Self {
        Self {
            command_runner,
            env_manager,
            config_manager,
        }
    }

    /// Get python executable path in virtual environment
    pub fn get_python_in_env(&self, repo_name: &str) -> PathBuf {
        let cfg = self.config_manager.get_config();
        let venv_path = cfg.install_path.join("envs").join(repo_name);
        if cfg!(windows) {
            venv_path.join("python.exe")
        } else {
            venv_path.join("bin").join("python")
        }
    }

    /// Get pip executable command for virtual environment
    pub fn get_pip_executable(&self, repo_name: &str) -> Vec<String> {
        let py = self.get_python_in_env(repo_name);
        if py.exists() {
            vec![py.to_string_lossy().to_string(), "-m".into(), "pip".into()]
        } else {
            vec!["python".into(), "-m".into(), "pip".into()]
        }
    }

    /// Get uv executable command for virtual environment
    pub fn get_uv_executable(&self, repo_name: &str) -> Vec<String> {
        let mut py_path = self.get_python_in_env(repo_name);
        if !py_path.exists() {
            py_path = if cfg!(windows) { 
                PathBuf::from("python.exe") 
            } else { 
                PathBuf::from("python3") 
            };
        }
        vec![py_path.to_string_lossy().to_string(), "-m".into(), "uv".into()]
    }

    /// Install uv in virtual environment and check if it's available
    pub fn install_uv_in_venv(&self, repo_name: &str) -> Result<bool> {
        let uv_cmd = self.get_uv_executable(repo_name);
        // Try uv --version
        if self.command_runner.run_silent(
            &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], 
            None, 
            None
        ).is_ok() {
            return Ok(true);
        }
        
        // Install uv via pip
        let mut pip_cmd = self.get_pip_executable(repo_name);
        pip_cmd.extend(["install".into(), "uv".into()]);
        let _ = self.command_runner.run_silent(&pip_cmd, Some("Installing uv"), None);
        
        // Verify installation
        Ok(self.command_runner.run_silent(
            &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], 
            None, 
            None
        ).is_ok())
    }

    /// Find requirements files in repository, checking specific files first, then using glob patterns
    pub fn find_requirements_files(&self, repo_path: &Path) -> Option<PathBuf> {
        // First, check specific known files
        let specific_candidates = [
            repo_path.join("requirements.txt"),
            repo_path.join("requirements_pyp.txt"),
            repo_path.join("requirements").join("requirements_nvidia.txt"),
            repo_path.join("requirements").join("requirements.txt"),
            repo_path.join("install").join("requirements.txt"),
        ];
        
        // Check specific files first
        for candidate in &specific_candidates {
            if candidate.exists() {
                return Some(candidate.clone());
            }
        }
        
        // Then search for requirements_* patterns in root directory
        if let Ok(entries) = fs::read_dir(repo_path) {
            for entry in entries.flatten() {
                if let Ok(file_type) = entry.file_type() {
                    if file_type.is_file() {
                        let file_name = entry.file_name();
                        let name_str = file_name.to_string_lossy();
                        if name_str.starts_with("requirements_") && name_str.ends_with(".txt") {
                            return Some(entry.path());
                        }
                    }
                }
            }
        }
        
        // Then search for requirements* patterns in root directory
        if let Ok(entries) = fs::read_dir(repo_path) {
            for entry in entries.flatten() {
                if let Ok(file_type) = entry.file_type() {
                    if file_type.is_file() {
                        let file_name = entry.file_name();
                        let name_str = file_name.to_string_lossy();
                        if name_str.starts_with("requirements") && name_str.ends_with(".txt") && name_str != "requirements.txt" {
                            return Some(entry.path());
                        }
                    }
                }
            }
        }
        
        // Finally, search in requirements/ subdirectory for requirements\* patterns
        let requirements_dir = repo_path.join("requirements");
        if requirements_dir.exists() {
            if let Ok(entries) = fs::read_dir(&requirements_dir) {
                for entry in entries.flatten() {
                    if let Ok(file_type) = entry.file_type() {
                        if file_type.is_file() {
                            let file_name = entry.file_name();
                            let name_str = file_name.to_string_lossy();
                            if name_str.starts_with("requirements") && name_str.ends_with(".txt") {
                                return Some(entry.path());
                            }
                        }
                    }
                }
            }
        }
        
        None
    }

    /// Extract dependencies from pyproject.toml and create requirements_pyp.txt
    pub fn extract_dependencies_from_pyproject(&self, pyproject_path: &Path, repo_path: &Path) -> Result<PathBuf> {
        info!("Parsing pyproject.toml: {:?}", pyproject_path);
        
        // Read and parse TOML file
        let content = fs::read_to_string(pyproject_path)
            .map_err(|e| PortableSourceError::repository(format!("Failed to read pyproject.toml: {}", e)))?;
        
        let toml: TomlValue = content.parse()
            .map_err(|e| PortableSourceError::repository(format!("Failed to parse pyproject.toml: {}", e)))?;
        
        // Extract dependencies from [project.dependencies]
        let mut dependencies = Vec::new();
        
        if let Some(project) = toml.get("project") {
            if let Some(deps) = project.get("dependencies") {
                if let Some(deps_array) = deps.as_array() {
                    for dep in deps_array {
                        if let Some(dep_str) = dep.as_str() {
                            dependencies.push(dep_str.to_string());
                        }
                    }
                }
            }
        }
        
        if dependencies.is_empty() {
            return Err(PortableSourceError::repository("No dependencies found in pyproject.toml [project.dependencies]".to_string()));
        }
        
        // Create requirements_pyp.txt file
        let requirements_path = repo_path.join("requirements_pyp.txt");
        let mut file = fs::File::create(&requirements_path)
            .map_err(|e| PortableSourceError::repository(format!("Failed to create requirements_pyp.txt: {}", e)))?;
        
        for dep in &dependencies {
            writeln!(file, "{}", dep)
                .map_err(|e| PortableSourceError::repository(format!("Failed to write to requirements_pyp.txt: {}", e)))?;
        }
        
        info!("Extracted {} dependencies from pyproject.toml to requirements_pyp.txt", dependencies.len());
        Ok(requirements_path)
    }

    /// Check for pyproject.toml scripts
    pub fn check_scripts_in_pyproject(&self, repo_path: &Path) -> Result<(bool, Option<String>)> {
        let pyproject_path = repo_path.join("pyproject.toml");
        
        if !pyproject_path.exists() {
            return Ok((false, None));
        }
        
        // Read and parse TOML file
        let content = fs::read_to_string(&pyproject_path)
            .map_err(|e| PortableSourceError::repository(format!("Failed to read pyproject.toml: {}", e)))?;
        
        let toml: TomlValue = content.parse()
            .map_err(|e| PortableSourceError::repository(format!("Failed to parse pyproject.toml: {}", e)))?;
        
        // Check for [project.scripts] section
        if let Some(project) = toml.get("project") {
            if let Some(scripts) = project.get("scripts") {
                if let Some(scripts_table) = scripts.as_table() {
                    // Priority order: gradio+infer scripts first, then any other script
                    let mut fallback_script: Option<(String, String)> = None;
                    
                    for (script_name, script_value) in scripts_table {
                        if let Some(script_str) = script_value.as_str() {
                            let name_lower = script_name.to_lowercase();
                            let value_lower = script_str.to_lowercase();
                            
                            // Check if script name or value contains both 'gradio' and 'infer'
                            if (name_lower.contains("gradio") && name_lower.contains("infer")) ||
                               (value_lower.contains("gradio") && value_lower.contains("infer")) {
                                info!("Found gradio+infer script: {} = {}", script_name, script_str);
                                // Extract module path (before ':') for python -m usage
                                let module_path = if let Some(colon_pos) = script_str.find(':') {
                                    script_str[..colon_pos].to_string()
                                } else {
                                    script_str.to_string()
                                };
                                return Ok((true, Some(module_path)));
                            }
                            
                            // Store first script as fallback
                            if fallback_script.is_none() {
                                // Extract module path (before ':') for python -m usage
                                let module_path = if let Some(colon_pos) = script_str.find(':') {
                                    script_str[..colon_pos].to_string()
                                } else {
                                    script_str.to_string()
                                };
                                fallback_script = Some((script_name.clone(), module_path));
                            }
                        }
                    }
                    
                    // If no gradio+infer script found, use any available script
                    if let Some((script_name, script_str)) = fallback_script {
                        info!("Found pyproject.toml script: {} = {}", script_name, script_str);
                        return Ok((true, Some(script_str)));
                    }
                }
            }
        }
        
        Ok((false, None))
    }

    /// Install requirements from requirements.txt file using uv or pip
    pub fn install_requirements_with_uv_or_pip(&self, repo_name: &str, requirements: &Path, repo_path: Option<&Path>) -> Result<()> {
        if !requirements.exists() {
            return Err(PortableSourceError::repository(format!("Requirements file not found: {:?}", requirements)));
        }

        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
        
        // Handle case when requirements is in different directory than repo_path
        let tmp = if let Some(repo) = repo_path {
            if requirements.starts_with(repo) {
                requirements.to_path_buf()
            } else {
                // Copy requirements to repo directory for proper resolution
                let tmp_req = repo.join("requirements_tmp.txt");
                std::fs::copy(requirements, &tmp_req)?;
                tmp_req
            }
        } else {
            requirements.to_path_buf()
        };

        // Filter out packages that we install separately from requirements
        let filtered_req = if repo_path.is_some() {
            let filtered_path = tmp.parent().unwrap().join("requirements_filtered.txt");
            let content = std::fs::read_to_string(&tmp)?;
            let filtered_content = content
                .lines()
                .filter(|line| {
                    let line_lower = line.trim().to_lowercase();
                    // Skip empty lines and comments
                    if line_lower.is_empty() || line_lower.starts_with('#') {
                        return true;
                    }
                    // Filter out packages we install separately
                    !line_lower.starts_with("insightface") && 
                    !line_lower.contains("insightface") &&
                    !line_lower.starts_with("onnxruntime") && 
                    !line_lower.contains("onnxruntime") &&
                    !line_lower.starts_with("torch") && 
                    !line_lower.contains("torch") &&
                    !line_lower.starts_with("triton") && 
                    !line_lower.contains("triton")
                })
                .collect::<Vec<_>>()
                .join("\n");
            std::fs::write(&filtered_path, filtered_content)?;
            filtered_path
        } else {
            tmp.clone()
        };

        if uv_available {
            let mut uv_cmd = self.get_uv_executable(repo_name);
            uv_cmd.extend(["pip".into(), "install".into(), "-r".into(), filtered_req.to_string_lossy().to_string()]);
            self.command_runner.run(&uv_cmd, Some("Installing requirements (uv)"), repo_path)?;
        } else {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), "-r".into(), filtered_req.to_string_lossy().to_string()]);
            self.command_runner.run(&pip_cmd, Some("Installing requirements (pip)"), repo_path)?;
        }

        // Clean up temporary files if created
        if repo_path.is_some() {
            if tmp.file_name() == Some(std::ffi::OsStr::new("requirements_tmp.txt")) {
                let _ = std::fs::remove_file(&tmp);
            }
            if filtered_req.file_name() == Some(std::ffi::OsStr::new("requirements_filtered.txt")) {
                let _ = std::fs::remove_file(&filtered_req);
            }
        }

        // Install ONNX with GPU detection after base requirements
        let onnx_spec = self.get_onnx_package_spec();
        let mut onnx_cmd = if uv_available {
            let mut cmd = self.get_uv_executable(repo_name);
            cmd.extend(["pip".into(), "install".into()]);
            cmd
        } else {
            let mut cmd = self.get_pip_executable(repo_name);
            cmd.push("install".into());
            cmd
        };
        
        // Check if we need --pre flag for nightly builds (Blackwell GPUs)
        if self.needs_onnx_nightly() {
            onnx_cmd.push("--pre".into());
        }
        
        onnx_cmd.extend(["--index-strategy".into(), "unsafe-best-match".into()]);
        onnx_cmd.push(onnx_spec);
        
        if let Err(_) = self.command_runner.run(&onnx_cmd, Some("Installing ONNX with GPU support"), repo_path) {
            // Fallback without --pre if it fails
            if self.needs_onnx_nightly() {
                let mut fallback_cmd = if uv_available {
                    let mut cmd = self.get_uv_executable(repo_name);
                    cmd.extend(["pip".into(), "install".into()]);
                    cmd
                } else {
                    let mut cmd = self.get_pip_executable(repo_name);
                    cmd.push("install".into());
                    cmd
                };
                fallback_cmd.extend(["--index-strategy".into(), "unsafe-best-match".into()]);
                fallback_cmd.push(self.get_onnx_package_spec());
                let _ = self.command_runner.run(&fallback_cmd, Some("Installing ONNX (fallback)"), repo_path);
            }
        }

        // Check if torch is installed and reinstall with CUDA index if needed
        let mut check_cmd = self.get_pip_executable(repo_name);
        check_cmd.extend(["show".into(), "torch".into()]);
        
        let cfg = self.config_manager.get_config();
        let venv_path = cfg.install_path.join("envs").join(repo_name);
        
        if let Ok(output) = std::process::Command::new(&check_cmd[0])
            .args(&check_cmd[1..])
            .env("VIRTUAL_ENV", venv_path)
            .output() {
            if output.status.success() {
                let mut reinstall_cmd = if uv_available {
                    let mut cmd = self.get_uv_executable(repo_name);
                    cmd.extend(["pip".into(), "install".into()]);
                    cmd
                } else {
                    let mut cmd = self.get_pip_executable(repo_name);
                    cmd.push("install".into());
                    cmd
                };
                
                reinstall_cmd.extend([
                    "--force-reinstall".into(), 
                    "--index-url".into(), 
                    self.get_default_torch_index_url(),
                    "torch".into(), 
                    "torchvision".into(), 
                    "torchaudio".into()
                ]);
                
                if let Err(_) = self.command_runner.run_silent(&reinstall_cmd, Some("Reinstalling torch with CUDA"), repo_path) {
                    // Fallback to pip if uv fails
                    if uv_available {
                        let mut pip_cmd = self.get_pip_executable(repo_name);
                        pip_cmd.extend([
                            "install".into(), 
                            "--force-reinstall".into(), 
                            "--index-url".into(), 
                            self.get_default_torch_index_url(),
                            "torch".into(), 
                            "torchvision".into(), 
                            "torchaudio".into()
                        ]);
                        let _ = self.command_runner.run_silent(&pip_cmd, Some("Reinstalling torch with CUDA (pip)"), repo_path);
                    }
                }
            }
        }

        // Install Triton with platform-specific package names
        let mut triton_cmd = if uv_available {
            let mut cmd = self.get_uv_executable(repo_name);
            cmd.extend(["pip".into(), "install".into()]);
            cmd
        } else {
            let mut cmd = self.get_pip_executable(repo_name);
            cmd.push("install".into());
            cmd
        };
        
        // Use platform-specific triton package names
        #[cfg(windows)]
        triton_cmd.push("triton-windows".into());
        #[cfg(not(windows))]
        triton_cmd.push("triton".into());
        
        let _ = self.command_runner.run(&triton_cmd, Some("Installing Triton"), repo_path);

        // Check if InsightFace was in the original requirements
        let needs_insightface = std::fs::read_to_string(&tmp)?
            .lines()
            .any(|line| {
                let line_lower = line.trim().to_lowercase();
                line_lower.starts_with("insightface") || 
                line_lower.contains("insightface")
            });

        // Install InsightFace only if it was requested in requirements
        if needs_insightface {
            self.handle_insightface_package(repo_name, repo_path)?;
        }

        Ok(())
    }

    /// Install repository as package using uv or pip
    pub fn install_repo_as_package(&self, repo_name: &str, repo_path: &Path) -> Result<()> {
        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
        
        if uv_available {
            let mut uv_cmd = self.get_uv_executable(repo_name);
            uv_cmd.extend(["pip".into(), "install".into(), ".".into()]);
            self.command_runner.run_silent(&uv_cmd, Some("Installing repository as package (uv)"), Some(repo_path))
        } else {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), ".".into()]);
            self.command_runner.run_silent(&pip_cmd, Some("Installing repository as package (pip)"), Some(repo_path))
        }
    }

    /// Apply ONNX GPU detection to package name
    pub fn apply_onnx_gpu_detection(&self, base: &str) -> String {
        let up = self.config_manager.get_gpu_name().to_uppercase();
        if base.starts_with("onnxruntime") && !base.contains("-gpu") && !base.contains("-directml") {
            if up.contains("NVIDIA") {
                return base.replace("onnxruntime", "onnxruntime-gpu");
            }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) {
                return base.replace("onnxruntime", "onnxruntime-directml");
            }
        }
        base.into()
    }

    /// Check if ONNX nightly build is needed for GPU compatibility
    pub fn needs_onnx_nightly(&self) -> bool {
        // Blackwell GPUs need nightly builds
        if self.config_manager.has_cuda() {
            let gpu_generation = self.config_manager.detect_current_gpu_generation();
            let gpu_name = self.config_manager.get_gpu_name();
            let gpu_gen = format!("{:?}", gpu_generation).to_lowercase();
            let name_up = gpu_name.to_uppercase();
            let is_nvidia = name_up.contains("NVIDIA") || name_up.contains("RTX") || name_up.contains("GEFORCE");
            if is_nvidia && gpu_gen.contains("blackwell") {
                return true;
            }
        }
        
        // Linux: system CUDA 12.8
        #[cfg(unix)]
        {
            if let Some(cv) = crate::utils::detect_cuda_version_from_system() {
                if matches!(cv, crate::config::CudaVersionLinux::Cuda128) {
                    return true;
                }
            }
        }
        
        false
    }

    /// Get ONNX package specification with GPU generation consideration
    pub fn get_onnx_package_spec(&self) -> String {
        if self.config_manager.has_cuda() {
            let gpu_generation = self.config_manager.detect_current_gpu_generation();
            let gpu_name = self.config_manager.get_gpu_name();
            let gpu_gen = format!("{:?}", gpu_generation).to_lowercase();
            let name_up = gpu_name.to_uppercase();
            let is_nvidia = name_up.contains("NVIDIA") || name_up.contains("RTX") || name_up.contains("GEFORCE");
            let is_blackwell = gpu_gen.contains("blackwell");
            
            if is_nvidia && is_blackwell {
                return "onnxruntime-gpu>=1.20".into();
            }
            if is_nvidia {
                return "onnxruntime-gpu".into();
            }
            if (name_up.contains("AMD") || name_up.contains("INTEL")) && cfg!(windows) {
                return "onnxruntime-directml".into();
            }
        }
        "onnxruntime".into()
    }

    /// Get default torch index URL based on GPU and CUDA configuration
    pub fn get_default_torch_index_url(&self) -> String {
        if self.config_manager.has_cuda() {
            let gpu_name = self.config_manager.get_gpu_name();
            let gpu_generation = self.config_manager.detect_current_gpu_generation();
            let _name_up = gpu_name.to_uppercase();
            let gen_str = format!("{:?}", gpu_generation).to_lowercase();
            let is_blackwell = gen_str.contains("blackwell");
            
            if is_blackwell {
                return "https://download.pytorch.org/whl/nightly/cu128".to_string();
            }
        }
        
        #[cfg(unix)]
        {
            if let Some(cv) = crate::utils::detect_cuda_version_from_system() {
                return match cv {
                    crate::config::CudaVersionLinux::Cuda128 => "https://download.pytorch.org/whl/nightly/cu128".to_string(),
                    crate::config::CudaVersionLinux::Cuda126 => "https://download.pytorch.org/whl/cu126".to_string(),
                    crate::config::CudaVersionLinux::Cuda124 => "https://download.pytorch.org/whl/cu124".to_string(),
                    crate::config::CudaVersionLinux::Cuda121 => "https://download.pytorch.org/whl/cu121".to_string(),
                    crate::config::CudaVersionLinux::Cuda118 => "https://download.pytorch.org/whl/cu118".to_string(),
                };
            }
        }
        
        #[cfg(windows)]
        {
            if self.config_manager.has_cuda() {
                if let Some(cuda_version) = self.config_manager.get_cuda_version() {
                    return match cuda_version {
                        crate::config::CudaVersion::Cuda128 => "https://download.pytorch.org/whl/nightly/cu128".to_string(),
                        crate::config::CudaVersion::Cuda124 => "https://download.pytorch.org/whl/cu124".to_string(),
                        crate::config::CudaVersion::Cuda118 => "https://download.pytorch.org/whl/cu118".to_string(),
                    };
                }
            }
        }
        
        "https://download.pytorch.org/whl/cpu".to_string()
    }
    
    /// Get optional torch index URL
    pub fn get_default_torch_index_url_opt(&self) -> Option<String> {
        Some(self.get_default_torch_index_url())
    }

    /// Execute server installation plan steps
    pub fn execute_server_installation_plan(&self, repo_name: &str, plan: &JsonValue, repo_path: Option<&Path>) -> Result<bool> {
        let steps = plan.get("steps").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        
        // Process all installation steps
        for step in &steps {
            self.process_server_step(repo_name, step, repo_path)?;
        }
        
        Ok(true)
    }

    /// Process individual server installation step
    pub fn process_server_step(&self, repo_name: &str, step: &JsonValue, repo_path: Option<&Path>) -> Result<()> {
        let step_type = step.get("type").and_then(|s| s.as_str()).unwrap_or("");
        match step_type {
            "requirements" => {
                if let Some(path) = step.get("path").and_then(|s| s.as_str()) {
                    let req_path = if let Some(rp) = repo_path { 
                        rp.join(path) 
                    } else { 
                        PathBuf::from(path) 
                    };
                    self.install_requirements_with_uv_or_pip(repo_name, &req_path, repo_path)?;
                }
            }
            "pip_install" | "regular" | "regular_only" => {
                // For server plans, we let install_requirements_with_uv_or_pip handle everything
                // Just log that this step type is handled by requirements installation
                debug!("Step type {} will be handled by requirements installation", step_type);
            }
            _ => {
                debug!("Unknown step type in server plan: {}", step_type);
            }
        }
        Ok(())
    }

    /// Handle pip_install step with comprehensive package analysis and separation
    fn handle_pip_install_step(&self, repo_name: &str, step: &JsonValue, repo_path: Option<&Path>) -> Result<()> {
        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
        
        // Create analyzer for intelligent package processing
        let analyzer = RequirementsAnalyzer::new(self.config_manager);
        
        // Parse packages into PackageInfo structs with proper version handling
        let mut packages = Vec::new();
        if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
            for p in pkgs {
                if let Some(s) = p.as_str() {
                    if let Some(pkg_info) = analyzer.parse_requirement_line(s) {
                        packages.push(pkg_info);
                    }
                }
            }
        }
        
        // Create installation plan with intelligent package separation
        let plan = analyzer.create_installation_plan(&packages);
        
        // Install regular packages first (no special index needed)
        if !plan.regular_packages.is_empty() {
            let mut cmd = if uv_available {
                let mut c = self.get_uv_executable(repo_name);
                c.extend(["pip".into(), "install".into()]);
                c
            } else {
                let mut c = self.get_pip_executable(repo_name);
                c.push("install".into());
                c
            };
            
            // Check if we need --pre flag for any onnx packages that got classified as regular
            let needs_pre = self.needs_onnx_nightly() && 
                plan.regular_packages.iter().any(|pkg| {
                    pkg.name.starts_with("onnxruntime") && pkg.name.contains("gpu")
                });
            if needs_pre {
                cmd.push("--pre".into());
            }
            
            // Add dependency resolution strategy flags for better conflict handling
            cmd.extend(["--resolution".into(), "highest".into()]);
            cmd.extend(["--index-strategy".into(), "unsafe-best-match".into()]);
            
            // Add package specs with proper version handling
            for pkg in &plan.regular_packages {
                let pkg_spec = if pkg.name == "tensorflow" && pkg.version.is_none() {
                    // Handle unversioned tensorflow with platform-specific logic
                    #[cfg(windows)]
                    {
                        // On Windows, use regular tensorflow (CUDA libraries come separately)
                        // Use compatible version that works with typing-extensions>=4.8.0
                        "tensorflow==2.15.0".to_string()
                    }
                    #[cfg(not(windows))]
                    {
                        // On Linux, can use tensorflow with CUDA extensions
                        if self.config_manager.has_cuda() {
                            "tensorflow==2.15.0".to_string()
                        } else {
                            "tensorflow-cpu==2.15.0".to_string()
                        }
                    }
                } else if pkg.name == "typing-extensions" && pkg.version.is_some() {
                    // Use a compatible typing-extensions version that works with both tensorflow and onnx
                    // onnx>=1.18.0 requires typing-extensions>=4.7.1
                    // tensorflow 2.15.0 can work with typing-extensions>=4.7.1
                    "typing-extensions>=4.7.1".to_string()
                } else {
                    pkg.to_string()
                };
                cmd.push(pkg_spec);
            }
            
            self.command_runner.run(&cmd, Some("Installing regular packages"), repo_path)?;
        }
        
        // Install torch packages with appropriate index URL
        if !plan.torch_packages.is_empty() {
            let mut cmd = if uv_available {
                let mut c = self.get_uv_executable(repo_name);
                c.extend(["pip".into(), "install".into()]);
                c
            } else {
                let mut c = self.get_pip_executable(repo_name);
                c.push("install".into());
                c
            };
            
            // Use torch index URL from plan or step or default
            let torch_index = plan.torch_index_url.as_ref()
                .map(|s| s.as_str())
                .or_else(|| step.get("torch_index_url").and_then(|s| s.as_str()))
                .map(|s| s.to_string())
                .unwrap_or_else(|| self.get_default_torch_index_url());
            
            cmd.extend(["--index-url".into(), torch_index]);
            cmd.extend(["--index-strategy".into(), "unsafe-best-match".into()]);
            
            // Add torch package specs with versions
            for pkg in &plan.torch_packages {
                cmd.push(pkg.to_string());
            }
            
            self.command_runner.run(&cmd, Some("Installing torch packages"), repo_path)?;
        }
        
        // Install onnx packages with GPU detection and version handling
        if !plan.onnx_packages.is_empty() {
            let mut cmd = if uv_available {
                let mut c = self.get_uv_executable(repo_name);
                c.extend(["pip".into(), "install".into()]);
                c
            } else {
                let mut c = self.get_pip_executable(repo_name);
                c.push("install".into());
                c
            };
            
            // Check if we need --pre flag for nightly builds
            if self.needs_onnx_nightly() {
                cmd.push("--pre".into());
            }
            
            cmd.extend(["--index-strategy".into(), "unsafe-best-match".into()]);
            
            // Apply GPU detection to onnx packages and add to command
            for pkg in &plan.onnx_packages {
                let onnx_spec = self.apply_onnx_gpu_detection(&pkg.to_string());
                cmd.push(onnx_spec);
            }
            
            self.command_runner.run(&cmd, Some("Installing ONNX packages"), repo_path)?;
        }
        
        // Handle special packages with custom installation logic
        if !plan.insightface_packages.is_empty() {
            self.handle_insightface_package(repo_name, repo_path)?;
        }
        
        // Handle triton packages with platform-specific logic
        if !plan.triton_packages.is_empty() {
            let mut cmd = if uv_available {
                let mut c = self.get_uv_executable(repo_name);
                c.extend(["pip".into(), "install".into()]);
                c
            } else {
                let mut c = self.get_pip_executable(repo_name);
                c.push("install".into());
                c
            };
            
            // Use platform-specific triton package names
            #[cfg(windows)]
            cmd.push("triton-windows".into());
            #[cfg(not(windows))]
            cmd.push("triton".into());
            
            self.command_runner.run(&cmd, Some("Installing Triton packages"), repo_path)?;
        }
        
        Ok(())
    }

    /// Handle insightface package installation with Windows wheel support
    pub fn handle_insightface_package(&self, repo_name: &str, repo_path: Option<&Path>) -> Result<()> {
        #[cfg(windows)]
        {
            let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
            
            // Use precompiled wheel for Windows
            let wheel = "https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl";
            if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend([
                    "pip".into(), 
                    "install".into(), 
                    "--force-reinstall".into(),
                    "-U".into(),
                    wheel.into(),
                    "numpy==1.26.4".into()
                ]);
                self.command_runner.run(&uv_cmd, Some("Installing insightface + numpy (uv)"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend([
                    "install".into(), 
                    "--force-reinstall".into(),
                    "-U".into(),
                    wheel.into(),
                    "numpy==1.26.4".into()
                ]);
                self.command_runner.run(&pip_cmd, Some("Installing insightface + numpy (pip)"), repo_path)
            }
        }
        
        #[cfg(not(windows))]
        {
            let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
            
            if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend([
                    "pip".into(), 
                    "install".into(), 
                    "--force-reinstall".into(),
                    "-U".into(), 
                    "insightface".into(), 
                    "numpy==1.26.4".into()
                ]);
                self.command_runner.run(&uv_cmd, Some("Installing insightface + numpy (uv)"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend([
                    "install".into(), 
                    "--force-reinstall".into(),
                    "-U".into(), 
                    "insightface".into(), 
                    "numpy==1.26.4".into()
                ]);
                self.command_runner.run(&pip_cmd, Some("Installing insightface + numpy (pip)"), repo_path)
            }
        }
    }

}

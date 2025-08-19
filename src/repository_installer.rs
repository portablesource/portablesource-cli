//! Repository installer for PortableSource
//! 
//! This module handles installation, updating, and management of repositories
//! with automatic dependency resolution and GPU-specific package handling.

use crate::{Result, PortableSourceError};
use crate::config::{ConfigManager, SERVER_DOMAIN};
use crate::envs_manager::PortableEnvironmentManager;
use log::{info, warn, debug};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use url::Url;
use toml::Value as TomlValue;
#[cfg(unix)]
use crate::utils::{detect_linux_mode, LinuxMode};

pub struct RepositoryInstaller {
    install_path: PathBuf,
    _config_manager: ConfigManager,
    _env_manager: PortableEnvironmentManager,
    server_client: ServerApiClient,
    main_file_finder: MainFileFinder,
    fallback_repositories: HashMap<String, FallbackRepo>,
}

impl RepositoryInstaller {
    pub fn new(install_path: PathBuf, mut config_manager: ConfigManager) -> Self {
        let env_manager = PortableEnvironmentManager::with_config(install_path.clone(), config_manager.clone());
        let server_client = ServerApiClient::new(format!("https://{}", SERVER_DOMAIN));
        let main_file_finder = MainFileFinder::new(server_client.clone());
        let fallback_repositories = default_fallback_repositories();
        // Anchor config to install dir (without extra disk writes here)
        config_manager.get_config_mut().install_path = install_path.clone();
        config_manager.set_config_path_to_install_dir();
        
        Self {
            install_path,
            _config_manager: config_manager,
            _env_manager: env_manager,
            server_client,
            main_file_finder,
            fallback_repositories,
        }
    }
    
    /// Install a repository from URL or name
    pub async fn install_repository(&mut self, repo_url_or_name: &str) -> Result<()> {
        log::info!("Installing repository: {}", repo_url_or_name);
        println!("[PortableSource] Installing repository: {}", repo_url_or_name);
        
        if self.is_repository_url(repo_url_or_name) {
            self.install_from_url(repo_url_or_name).await
        } else {
            self.install_from_name(repo_url_or_name).await
        }
    }
    
    /// Update an existing repository
    pub async fn update_repository(&mut self, repo_name: &str) -> Result<()> {
        log::info!("Updating repository: {}", repo_name);

        let repo_path = self.install_path.join("repos").join(repo_name);

        if !repo_path.exists() {
            return Err(PortableSourceError::repository(
                format!("Repository '{}' not found", repo_name)
            ));
        }

        // 1) Fetch + reset --hard to remote main/master, then pull
        let git_exe = self.get_git_executable();
        {
            let args = vec![git_exe.clone(), "fetch".to_string(), "--all".to_string()];
            if let Err(e) = run_tool_with_env(&self._env_manager, &args, Some("Fetching from remote"), Some(&repo_path)) {
                warn!("Failed to fetch from remote: {}", e);
            }
        }
        {
            let args = vec![git_exe.clone(), "reset".to_string(), "--hard".to_string(), "origin/main".to_string()];
            if run_tool_with_env(&self._env_manager, &args, Some("Reset to origin/main"), Some(&repo_path)).is_err() {
                let args = vec![git_exe.clone(), "reset".to_string(), "--hard".to_string(), "origin/master".to_string()];
                let _ = run_tool_with_env(&self._env_manager, &args, Some("Reset to origin/master"), Some(&repo_path));
            }
        }
        {
            let args = vec![git_exe.clone(), "pull".to_string()];
            if let Err(e) = run_tool_with_env(&self._env_manager, &args, Some("Pulling latest changes"), Some(&repo_path)) {
                warn!("Failed to pull latest changes: {}", e);
            }
        }

        // 2) Reinstall deps (install_dependencies сам пересоздаст venv)
        let _ = self.install_dependencies(&repo_path).await;

        Ok(())
    }
    
    /// Delete a repository
    pub fn delete_repository(&self, repo_name: &str) -> Result<()> {
        log::info!("Deleting repository: {}", repo_name);
        
        let repo_path = self.install_path.join("repos").join(repo_name);
        let env_path = self.install_path.join("envs").join(repo_name);
        
        if !repo_path.exists() && !env_path.exists() {
            return Err(PortableSourceError::repository(
                format!("Repository '{}' not found", repo_name)
            ));
        }
        
        // Delete repo folder if present
        if repo_path.exists() {
            std::fs::remove_dir_all(&repo_path)
                .map_err(|e| PortableSourceError::repository(
                    format!("Failed to delete repository '{}': {}", repo_name, e)
                ))?;
        }

        // Delete corresponding env folder if present
        if env_path.exists() {
            std::fs::remove_dir_all(&env_path)
                .map_err(|e| PortableSourceError::repository(
                    format!("Failed to delete environment for '{}': {}", repo_name, e)
                ))?;
        }
        
        log::info!("Repository '{}' deleted successfully", repo_name);
        Ok(())
    }
    
    /// List installed repositories with source suffixes
    pub fn list_repositories(&self) -> Result<Vec<String>> {
        let repos_path = self.install_path.join("repos");
        
        if !repos_path.exists() {
            return Ok(Vec::new());
        }
        
        let mut repositories = Vec::new();
        
        for entry in std::fs::read_dir(&repos_path)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                if let Some(name) = entry.file_name().to_str() {
                    let repo_dir = entry.path();
                    let link_file = repo_dir.join("link.txt");
                    let suffix = if link_file.exists() {
                        let link = fs::read_to_string(&link_file).unwrap_or_default();
                        let link_lower = link.to_lowercase();
                        if link_lower.contains("github.com") { " [From github]" } else { " [From git]" }
                    } else {
                        " [From server]"
                    };
                    repositories.push(format!("{}{}", name, suffix));
                }
            }
        }
        
        repositories.sort();
        Ok(repositories)
    }

    /// List raw repository folder names (no suffixes)
    pub fn list_repository_names_raw(&self) -> Result<Vec<String>> {
        let repos_path = self.install_path.join("repos");
        if !repos_path.exists() { return Ok(Vec::new()); }
        let mut repositories = Vec::new();
        for entry in std::fs::read_dir(&repos_path)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                if let Some(name) = entry.file_name().to_str() {
                    repositories.push(name.to_string());
                }
            }
        }
        repositories.sort();
        Ok(repositories)
    }

    /// List repositories with labels, preserving mapping to raw names, sorted by name
    pub fn list_repositories_labeled(&self) -> Result<Vec<(String, String)>> {
        let repos_path = self.install_path.join("repos");
        if !repos_path.exists() { return Ok(Vec::new()); }
        let mut items: Vec<(String, String)> = Vec::new();
        for entry in std::fs::read_dir(&repos_path)? {
            let entry = entry?;
            if entry.file_type()?.is_dir() {
                if let Some(name) = entry.file_name().to_str() {
                    let repo_dir = entry.path();
                    let link_file = repo_dir.join("link.txt");
                    let suffix = if link_file.exists() {
                        let link = fs::read_to_string(&link_file).unwrap_or_default();
                        let link_lower = link.to_lowercase();
                        if link_lower.contains("github.com") { " [From github]" } else { " [From git]" }
                    } else {
                        " [From server]"
                    };
                    items.push((name.to_string(), format!("{}{}", name, suffix)));
                }
            }
        }
        items.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(items)
    }
    
    async fn install_from_url(&mut self, repo_url: &str) -> Result<()> {
        log::info!("Installing from URL: {}", repo_url);
        // Parse URL to get repository name
        let url = Url::parse(repo_url)
            .map_err(|e| PortableSourceError::repository(format!("Invalid repository URL: {}", e)))?;
        let repo_name = self.extract_repo_name_from_url(&url)?;
        let repo_path = self.install_path.join("repos").join(&repo_name);

        // Clone or update
        let repo_info = RepositoryInfo { url: Some(repo_url.to_string()), main_file: None, program_args: None };
        self.clone_or_update_repository(&repo_info, &repo_path).await?;

        // Create URL marker and link.txt (source)
        let _ = create_url_marker(&repo_path, &repo_name, repo_url);
        let _ = write_link_file(&repo_path, repo_url);

        // Install dependencies
        self.install_dependencies(&repo_path).await?;

        // Generate startup script (Windows only for now)
        #[cfg(windows)]
        self.generate_startup_script(&repo_path, &repo_info)?;
                #[cfg(unix)]
        {
            self.generate_startup_script_unix(&repo_path, &repo_info)?;
            println!("[PortableSource] Start script generated: {:?}", repo_path.join(format!("start_{}.sh", repo_name.to_lowercase())));
        }

        // Send stats (non-fatal)
        let _ = self.server_client.send_download_stats(&repo_name);

        // Special setup hooks
        self.apply_special_setup(&repo_name, &repo_path)?;

        info!("Repository '{}' installed successfully", repo_name);
        Ok(())
    }
    
    async fn install_from_name(&mut self, repo_name: &str) -> Result<()> {
        info!("Installing from name: {}", repo_name);
        println!("[PortableSource] Resolving repository '{}'", repo_name);
        let repo_info = self.get_repository_info(repo_name)?
            .ok_or_else(|| PortableSourceError::repository(format!("Repository '{}' not found", repo_name)))?;

        let name = self.normalize_repo_name(repo_name, &repo_info)?;
        let repo_path = self.install_path.join("repos").join(&name);

        println!("[PortableSource] Target path: {:?}", repo_path);
        println!("[PortableSource] Cloning/Updating repository...");
        self.clone_or_update_repository(&repo_info, &repo_path).await?;

        println!("[PortableSource] Installing dependencies...");
        // Persist config once at start of dependency installation
        // Конфигурация больше не сохраняется на диск - только сессионные настройки
        self.install_dependencies(&repo_path).await?;

        #[cfg(windows)]
        {
            // println!("[PortableSource] Generating start script...");
            // Конфигурация больше не сохраняется на диск - только сессионные настройки
            self.generate_startup_script(&repo_path, &repo_info)?;
        }
        #[cfg(unix)]
        {
            // println!("[PortableSource] Generating start script (.sh)...");
            // Конфигурация больше не сохраняется на диск - только сессионные настройки
            self.generate_startup_script_unix(&repo_path, &repo_info)?;
            println!("[PortableSource] Start script generated: {:?}", repo_path.join(format!("start_{}.sh", name.to_lowercase())));
        }

        let _ = self.server_client.send_download_stats(&name);


        // Special setup hooks
        self.apply_special_setup(&name, &repo_path)?;
        Ok(())
    }
    
    // Note: kept for potential future use
    // async fn clone_repository(&self, repo_url: &str, repo_path: &Path) -> Result<()> {
    //     info!("Cloning repository to: {:?}", repo_path);
    //     println!("[PortableSource] git clone {} -> {:?}", repo_url, repo_path);
    //     let git_exe = self.get_git_executable();
    //     let parent = repo_path.parent().ok_or_else(|| PortableSourceError::repository("Invalid repo path"))?;
    //     fs::create_dir_all(parent)?;
    //     let mut cmd = Command::new(git_exe);
    //     cmd.current_dir(parent).arg("clone").arg(repo_url).arg(repo_path.file_name().unwrap());
    //     run_with_progress(cmd, Some("Cloning repository"))?;
    //     Ok(())
    // }
    
    async fn install_dependencies(&self, repo_path: &Path) -> Result<()> {
        info!("Installing dependencies for: {:?}", repo_path);
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();

        // Ensure project environment exists (Windows: copy portable python; Linux: create venv)
        self.create_venv_environment(&repo_name)?;

        // Try server installation plan
        if let Some(plan) = self.server_client.get_installation_plan(&repo_name)? {
            info!("Using server installation plan");
            if self.execute_server_installation_plan(&repo_name, &plan, Some(repo_path))? {
                return Ok(());
            } else {
                warn!("Server installation failed, falling back to local requirements.txt");
            }
        } else {
            info!("No server installation plan, using local files");
        }

        // Check for pyproject.toml first
        let pyproject_path = repo_path.join("pyproject.toml");
        if pyproject_path.exists() {
            info!("Found pyproject.toml, extracting dependencies");
            if let Ok(requirements_path) = self.extract_dependencies_from_pyproject(&pyproject_path, repo_path) {
                info!("Installing from extracted pyproject.toml dependencies: {:?}", requirements_path);
                self.install_requirements_with_uv_or_pip(&repo_name, &requirements_path, Some(repo_path))?;
                
                // Install the repository itself as a package
                info!("Installing repository as package with uv pip install .");
                self.install_repo_as_package(&repo_name, repo_path)?;
                
                return Ok(());
            } else {
                warn!("Failed to extract dependencies from pyproject.toml, falling back to requirements.txt");
            }
        }

        // Fallback to requirements.txt variants using smart search
        if let Some(requirements_file) = self.find_requirements_files(repo_path) {
            info!("Installing from {:?}", requirements_file);
            self.install_requirements_with_uv_or_pip(&repo_name, &requirements_file, Some(repo_path))?;
        } else {
            info!("No requirements.txt or pyproject.toml found");
        }
        Ok(())
    }
    
    fn is_repository_url(&self, input: &str) -> bool {
        input.starts_with("http://") || input.starts_with("https://") || input.starts_with("git@")
    }
    
    fn extract_repo_name_from_url(&self, url: &Url) -> Result<String> {
        let path = url.path();
        let name = path.split('/').last().unwrap_or("unknown");
        
        // Remove .git suffix if present
        let name = if name.ends_with(".git") {
            &name[..name.len() - 4]
        } else {
            name
        };
        
        if name.is_empty() {
            return Err(PortableSourceError::repository(
                "Could not extract repository name from URL"
            ));
        }
        
        Ok(name.to_string())
    }
    
    /// Find requirements files in repository, checking specific files first, then using glob patterns
    fn find_requirements_files(&self, repo_path: &Path) -> Option<PathBuf> {
        use std::fs;
        
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
}

// ===== Additional types and impls =====

#[derive(Clone, Debug, Default)]
struct ServerApiClient {
    server_url: String,
    timeout_secs: u64,
}

impl ServerApiClient {
    fn new(server_url: String) -> Self {
        Self { server_url: server_url.trim_end_matches('/').to_string(), timeout_secs: 10 }
    }

    #[allow(dead_code)]
    fn is_server_available(&self) -> bool {
        let url = format!("{}/api/repositories", self.server_url);
        let timeout = self.timeout_secs;
        std::thread::spawn(move || {
            match reqwest::blocking::Client::new()
                .get(&url)
                .timeout(std::time::Duration::from_secs(timeout))
                .send() {
                Ok(resp) => resp.status().is_success(),
                Err(_) => false,
            }
        }).join().unwrap_or(false)
    }

    fn get_repository_info(&self, name: &str) -> Result<Option<RepositoryInfo>> {
        let url = format!("{}/api/repositories/{}", self.server_url, name.to_lowercase());
        let timeout = self.timeout_secs;
        let res = std::thread::spawn(move || {
            let resp = reqwest::blocking::Client::new()
                .get(&url)
                .timeout(std::time::Duration::from_secs(timeout))
                .send();
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        let v: serde_json::Value = r.json().unwrap_or(serde_json::json!({}));
                        if v.get("success").and_then(|b| b.as_bool()).unwrap_or(false) {
                            if let Some(repo) = v.get("repository") {
                                let url = repo.get("repositoryUrl").and_then(|s| s.as_str()).map(|s| s.trim().to_string());
                                let main_file = repo.get("filePath").and_then(|s| s.as_str()).map(|s| s.to_string());
                                let program_args = repo.get("programArgs").and_then(|s| s.as_str()).map(|s| s.to_string());
                                return Ok(Some(RepositoryInfo { url, main_file, program_args }));
                            }
                        } else {
                            // legacy format
                            let url = v.get("url").and_then(|s| s.as_str()).map(|s| s.to_string());
                            let main_file = v.get("main_file").and_then(|s| s.as_str()).map(|s| s.to_string());
                            let program_args = v.get("program_args").and_then(|s| s.as_str()).map(|s| s.to_string());
                            if url.is_some() || main_file.is_some() {
                                return Ok(Some(RepositoryInfo { url, main_file, program_args }));
                            }
                        }
                        Ok(None)
                    } else if r.status().as_u16() == 404 { Ok(None) } else { Ok(None) }
                }
                Err(_) => Ok(None)
            }
        }).join().unwrap_or(Ok(None));
        res
    }

    #[allow(dead_code)]
    fn search_repositories(&self, _name: &str) -> Vec<serde_json::Value> {
        // Optional enhancement: implement when server supports search endpoint
        Vec::new()
    }

    fn get_installation_plan(&self, name: &str) -> Result<Option<serde_json::Value>> {
        let url = format!("{}/api/repositories/{}/install-plan", self.server_url, name.to_lowercase());
        let timeout = self.timeout_secs;
        std::thread::spawn(move || {
            let resp = reqwest::blocking::Client::new()
                .get(&url)
                .timeout(std::time::Duration::from_secs(timeout))
                .send();
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        let v: serde_json::Value = r.json().unwrap_or(serde_json::json!({}));
                        if v.get("success").and_then(|b| b.as_bool()).unwrap_or(false) {
                            if let Some(plan) = v.get("installation_plan") { return Ok(Some(plan.clone())); }
                        }
                        Ok(None)
                    } else { Ok(None) }
                }
                Err(e) => { warn!("Server error get_installation_plan: {}", e); Ok(None) }
            }
        }).join().unwrap_or(Ok(None))
    }

    fn send_download_stats(&self, repo_name: &str) -> Result<()> {
        let url = format!("{}/api/repositories/{}/download", self.server_url, repo_name.to_lowercase());
        let body = serde_json::json!({
            "repository_name": repo_name.to_lowercase(),
            "success": true,
            "timestamp": serde_json::Value::Null,
        });
        let timeout = self.timeout_secs;
        let _ = std::thread::spawn(move || {
            let _ = reqwest::blocking::Client::new()
                .post(&url)
                .json(&body)
                .timeout(std::time::Duration::from_secs(timeout))
                .send();
        }).join();
        Ok(())
    }
}

#[derive(Clone, Debug, Default)]
struct MainFileFinder { server_client: ServerApiClient }

impl MainFileFinder {
    fn new(server_client: ServerApiClient) -> Self { Self { server_client } }

    fn find_main_file(&self, repo_name: &str, repo_path: &Path, repo_url: Option<&str>) -> Option<String> {
        // 1) Try server
        if let Ok(Some(info)) = self.server_client.get_repository_info(repo_name) {
            if let Some(main_file) = info.main_file {
                if validate_main_file(repo_path, &main_file) { return Some(main_file); }
            }
        }
        // 2) Try common names
        let common = ["run.py","app.py","webui.py","main.py","start.py","launch.py","gui.py","interface.py","server.py"];
        for f in common {
            if validate_main_file(repo_path, f) { return Some(f.to_string()); }
        }
        // 3) Heuristic: any single non-test python file
        let mut candidates: Vec<String> = Vec::new();
        if let Ok(entries) = fs::read_dir(repo_path) {
            for e in entries.flatten() {
                if let Ok(ft) = e.file_type() { if ft.is_file() {
                    let name = e.file_name().to_string_lossy().to_string();
                    if name.to_lowercase().ends_with(".py") && !name.contains("test_") && name != "setup.py" && !name.contains("__") && !name.contains("install") {
                        candidates.push(name);
                    }
                }}
            }
        }
        if candidates.len() == 1 { return candidates.into_iter().next(); }
        for c in &candidates { if c.contains("main") || c.contains("run") || c.contains("start") || c.contains("app") { return Some(c.clone()); } }
        // 4) last resort: use repo_url name
            if let Some(url) = repo_url { if let Ok(urlp) = Url::parse(url) {
            if let Some(name) = urlp.path_segments().and_then(|s| s.last()).map(|s| s.trim_end_matches(".git")) {
                let candidate = format!("{}.py", name);
                if validate_main_file(repo_path, &candidate) { return Some(candidate); }
            }
        }}
        None
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct RepositoryInfo { url: Option<String>, main_file: Option<String>, program_args: Option<String> }

#[derive(Clone, Debug)]
#[allow(dead_code)]
struct FallbackRepo { url: String, branch: Option<String>, main_file: Option<String>, program_args: Option<String> }

fn default_fallback_repositories() -> HashMap<String, FallbackRepo> {
    let mut m = HashMap::new();
    m.insert("facefusion".into(), FallbackRepo{ url: "https://github.com/facefusion/facefusion".into(), branch: Some("master".into()), main_file: Some("facefusion.py".into()), program_args: Some("run".into())});
    m.insert("comfyui".into(), FallbackRepo{ url: "https://github.com/comfyanonymous/ComfyUI".into(), branch: None, main_file: Some("main.py".into()), program_args: None});
    m.insert("stable-diffusion-webui-forge".into(), FallbackRepo{ url: "https://github.com/lllyasviel/stable-diffusion-webui-forge".into(), branch: None, main_file: Some("webui.py".into()), program_args: None});
    m.insert("liveportrait".into(), FallbackRepo{ url: "https://github.com/KwaiVGI/LivePortrait".into(), branch: None, main_file: Some("app.py".into()), program_args: None});
    m.insert("deep-live-cam".into(), FallbackRepo{ url: "https://github.com/hacksider/Deep-Live-Cam".into(), branch: None, main_file: Some("run.py".into()), program_args: None});
    m
}

fn validate_main_file(repo_path: &Path, main_file: &str) -> bool {
    repo_path.join(main_file).exists()
}

fn create_url_marker(repo_path: &Path, repo_name: &str, repo_url: &str) -> Result<()> {
    let marker = repo_path.join(format!("ps_repo_{}_url.txt", repo_name.to_lowercase()));
    let mut f = fs::File::create(marker)?;
    f.write_all(repo_url.as_bytes())?;
    Ok(())
}

fn write_link_file(repo_path: &Path, link: &str) -> Result<()> {
    let marker = repo_path.join("link.txt");
    let mut f = fs::File::create(marker)?;
    f.write_all(link.as_bytes())?;
    Ok(())
}

impl RepositoryInstaller {
    fn apply_special_setup(&self, _repo_name: &str, _repo_path: &Path) -> Result<()> {
        Ok(())
    }

    fn get_git_executable(&self) -> String {
        if let Some(p) = self._env_manager.get_git_executable() { return p.to_string_lossy().to_string(); }
        "git".into()
    }

    async fn clone_or_update_repository(&self, repo_info: &RepositoryInfo, repo_path: &Path) -> Result<()> {
        let git_exe = self.get_git_executable();
        if repo_path.exists() {
            if repo_path.join(".git").exists() {
                match self.update_repository_with_fixes(&git_exe, repo_path) {
                    Ok(_) => return Ok(()),
                    Err(e) => {
                        // If repository was removed due to corruption (exit code 128), proceed to clone
                        if e.to_string().contains("Repository corrupted (exit code 128)") {
                            warn!("Repository was corrupted and removed, proceeding to clone fresh copy");
                            // Continue to cloning logic below
                        } else {
                            return Err(e);
                        }
                    }
                }
            } else {
                return Err(PortableSourceError::repository(format!("Directory exists but is not a git repository: {:?}", repo_path)));
            }
        }
        
        // Clone repository (either first time or after corruption removal)
        let url = repo_info.url.clone().ok_or_else(|| PortableSourceError::repository("Missing repository URL"))?;
        info!("Cloning repository from URL: {}", url);
        
        let parent = repo_path.parent().ok_or_else(|| PortableSourceError::repository("Invalid repo path"))?;
        fs::create_dir_all(parent)?;
        let mut args = vec![git_exe.clone(), "clone".to_string()];
        if let Some(branch) = None::<String> { 
            args.push("-b".to_string());
            args.push(branch);
        }
        args.push(url.clone());
        args.push(repo_path.file_name().unwrap().to_string_lossy().to_string());
        
        match run_tool_with_env(&self._env_manager, &args, Some("Cloning repository"), Some(parent)) {
            Ok(_) => {
                info!("Repository cloned successfully to: {:?}", repo_path);
                println!("[PortableSource] Repository cloned successfully");
                Ok(())
            }
            Err(e) => {
                eprintln!("Failed to clone repository from {}: {}", url, e);
                println!("[PortableSource] Failed to clone repository: {}", e);
                Err(e)
            }
        }
    }

    fn update_repository_with_fixes(&self, git_exe: &str, repo_path: &Path) -> Result<()> {
        let max_attempts = 3;
        for attempt in 0..max_attempts {
            let args = vec![git_exe.to_string(), "pull".to_string()];
            match run_tool_with_env(&self._env_manager, &args, Some("Updating repository"), Some(repo_path)) {
                Ok(_) => return Ok(()),
                Err(e) => {
                    warn!("git pull failed (attempt {}/{}): {}", attempt + 1, max_attempts, e);
                    
                    // Check for exit code 128 (not a git repository)
                    if e.to_string().contains("exit code: 128") {
                        warn!("Exit code 128 detected - repository is corrupted. Removing and will re-clone.");
                        if let Err(remove_err) = std::fs::remove_dir_all(repo_path) {
                            warn!("Failed to remove corrupted repository: {}", remove_err);
                        }
                        return Err(PortableSourceError::repository("Repository corrupted (exit code 128) - removed for re-cloning"));
                    }
                    
                    if attempt < max_attempts - 1 {
                        if self.fix_git_issues(git_exe, repo_path).is_ok() { continue; }
                    }
                    if attempt == max_attempts - 1 { return Err(PortableSourceError::repository("Failed to update repository")); }
                }
            }
        }
        Err(PortableSourceError::repository("Failed to update repository"))
    }

    fn fix_git_issues(&self, git_exe: &str, repo_path: &Path) -> Result<()> {
        // Try a sequence of common fixes
        let fixes: Vec<Vec<&str>> = vec![
            vec!["fetch", "origin"],
            vec!["reset", "--hard", "origin/main"],
        ];
        for fix_args in fixes {
            let mut args = vec![git_exe.to_string()];
            args.extend(fix_args.into_iter().map(|s| s.to_string()));
            let _ = run_tool_with_env(&self._env_manager, &args, None, Some(repo_path));
        }
        Ok(())
    }

    fn create_venv_environment(&self, repo_name: &str) -> Result<()> {
        let install_path = self.install_path.clone();
        let envs_path = install_path.join("envs");
        let venv_path = envs_path.join(repo_name);
        if venv_path.exists() { fs::remove_dir_all(&venv_path)?; }

        if cfg!(windows) {
            // Windows: копируем портативный Python в envs/{repo}
            let ps_env_python = install_path.join("ps_env").join("python");
            if !ps_env_python.exists() { return Err(PortableSourceError::installation(format!("Portable Python not found at: {:?}", ps_env_python))); }
            info!("Creating environment by copying portable Python: {:?} -> {:?}", ps_env_python, venv_path);
            copy_dir_recursive(&ps_env_python, &venv_path)?;
            let python_exe = venv_path.join("python.exe");
            if !python_exe.exists() { return Err(PortableSourceError::installation(format!("Python executable not found in {:?}", venv_path))); }
        } else {
            // Linux: в DESK режиме используем python из micromamba-базы, в CLOUD — системный python3
            fs::create_dir_all(&envs_path)?;
            let mamba_py = install_path.join("ps_env").join("mamba_env").join("bin").join("python");
            #[cfg(unix)]
            let py_bin = if matches!(detect_linux_mode(), LinuxMode::Desk) && mamba_py.exists() { mamba_py } else { PathBuf::from("python3") };
            #[cfg(not(unix))]
            let py_bin = mamba_py; // unreachable, just to satisfy type
            let status = std::process::Command::new(&py_bin)
                .args(["-m", "venv", venv_path.to_string_lossy().as_ref()])
                .status()
                .map_err(|e| PortableSourceError::environment(format!("Failed to create venv: {}", e)))?;
            if !status.success() {
                return Err(PortableSourceError::environment("python -m venv failed"));
            }
            // Ensure pip is present in the new venv
            let venv_py = venv_path.join("bin").join("python");
            let pip_ok = std::process::Command::new(&venv_py)
                .args(["-m", "pip", "--version"]) 
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            if !pip_ok {
                let _ = std::process::Command::new(&venv_py)
                    .args(["-m", "ensurepip", "-U"]) 
                    .status();
            }
        }
        Ok(())
    }

    fn install_requirements_with_uv_or_pip(&self, repo_name: &str, requirements: &Path, repo_path: Option<&Path>) -> Result<()> {
        // Parse requirements and build plan
        let analyzer = RequirementsAnalyzer::new(&self._config_manager);
        let packages = analyzer.analyze_requirements(requirements);
        let plan = analyzer.create_installation_plan(&packages);

        // Ensure uv if available
        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
        info!("UV Available: {}", uv_available);
        info!("Plan: regular={} torch={} onnx={}", plan.regular_packages.len(), plan.torch_packages.len(), plan.onnx_packages.len());

        // 1) Regular packages via uv (prefer) или pip -r — сначала базовые зависимости
        if !plan.regular_packages.is_empty() {
            let tmp = requirements.parent().unwrap_or_else(|| Path::new(".")).join("requirements_regular_temp.txt");
            {
                let mut file = fs::File::create(&tmp)?;
                for p in &plan.regular_packages { writeln!(file, "{}", p.original_line)?; }
            }
            let res = if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--extra-index-url".into(), "https://pypi.ngc.nvidia.com".into(), "-r".into(), tmp.to_string_lossy().to_string()]);
                info!("UV Command: {:?}", uv_cmd);
                run_tool_with_env(&self._env_manager, &uv_cmd, Some("Installing regular packages with uv"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "-r".into(), tmp.to_string_lossy().to_string()]);
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing regular packages with pip"), repo_path)
            };
            let _ = fs::remove_file(&tmp);
            res?;
        }

        // 2) ONNX пакеты
        if !plan.onnx_packages.is_empty() {
            // Форсим onnxruntime-gpu>=1.20 для Blackwell/NVIDIA на любых ОС
            let forced_spec = self.get_onnx_package_spec();
            let spec = if forced_spec.contains(">=") { forced_spec } else {
                if let Some(pkg) = plan.onnx_packages.iter().find(|p| p.name == "onnxruntime" && p.version.is_some()) {
                    format!("{}=={}", forced_spec, pkg.version.clone().unwrap())
                } else { forced_spec }
            };
            let use_nightly = self.needs_onnx_nightly();
            if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--extra-index-url".into(), "https://pypi.ngc.nvidia.com".into()]);
                if use_nightly { uv_cmd.push("--pre".into()); }
                uv_cmd.push(spec);
                run_tool_with_env_silent(&self._env_manager, &uv_cmd, Some("Installing ONNX package (uv)"), repo_path)?;
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.push("install".into());
                if use_nightly { pip_cmd.push("--pre".into()); }
                pip_cmd.push(spec);
                run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing ONNX package"), repo_path)?;
            }
        }

        // 3) Torch пакеты (через pip) с нужным индексом
        if !plan.torch_packages.is_empty() {
            let torch_install_result = if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--force-reinstall".into()]);
                if let Some(index) = plan.torch_index_url.as_ref() {
                    uv_cmd.extend(["--index-url".into(), index.clone()]);
                }
                for p in &plan.torch_packages { uv_cmd.push(p.to_string()); }
                run_tool_with_env(&self._env_manager, &uv_cmd, Some("Installing PyTorch packages (uv)"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.push("install".into());
                pip_cmd.push("--force-reinstall".into());
                if let Some(index) = plan.torch_index_url.as_ref() {
                    pip_cmd.push("--index-url".into());
                    pip_cmd.push(index.clone());
                }
                for p in &plan.torch_packages { pip_cmd.push(p.to_string()); }
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing PyTorch packages"), repo_path)
            };
            
            // Fallback: если установка с версией не удалась, попробуем без версии
            if torch_install_result.is_err() {
                warn!("PyTorch installation with specific versions failed, trying without versions...");
                if uv_available {
                    let mut uv_cmd = self.get_uv_executable(repo_name);
                    uv_cmd.extend(["pip".into(), "install".into(), "--force-reinstall".into()]);
                    if let Some(index) = plan.torch_index_url.as_ref() {
                        uv_cmd.extend(["--index-url".into(), index.clone()]);
                    }
                    for p in &plan.torch_packages { 
                        uv_cmd.push(p.name.clone()); // Только имя без версии
                    }
                    run_tool_with_env(&self._env_manager, &uv_cmd, Some("Installing PyTorch packages without versions (uv)"), repo_path)?;
                } else {
                    let mut pip_cmd = self.get_pip_executable(repo_name);
                    pip_cmd.push("install".into());
                    pip_cmd.push("--force-reinstall".into());
                    if let Some(index) = plan.torch_index_url.as_ref() {
                        pip_cmd.push("--index-url".into());
                        pip_cmd.push(index.clone());
                    }
                    for p in &plan.torch_packages { 
                        pip_cmd.push(p.name.clone()); // Только имя без версии
                    }
                    run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing PyTorch packages without versions"), repo_path)?;
                }
            }
        }

        // 4) Triton (если присутствует) — пропускаем на Linux, т.к. ставится безболезненно
        #[cfg(windows)]
        if !plan.triton_packages.is_empty() {
            for pkg in &plan.triton_packages {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                let spec = if let Some(v) = &pkg.version { format!("{}=={}", pkg.name, v) } else { pkg.name.clone() };
                pip_cmd.extend(["install".into(), spec]);
                run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing Triton package"), repo_path)?;
            }
        }

        // 5) InsightFace — строго в самом конце, с -U для согласования numpy — пропускаем на Linux
        #[cfg(windows)]
        if !plan.insightface_packages.is_empty() {
            for _p in &plan.insightface_packages {
                self.handle_insightface_package(repo_name, repo_path)?;
            }
        }

        // ONNX packages are only installed if explicitly listed in dependencies
        // No automatic fallback installation
        
        // 6) Force reinstall torch with CUDA index if torch is installed (as dependency)
        // Check if torch is installed in the environment
        let python_exe = self.get_python_in_env(repo_name);
        let check_torch_cmd = vec![python_exe.to_string_lossy().to_string(), "-c".to_string(), "import torch; print('torch_installed')".to_string()];
        
        if run_tool_with_env_silent(&self._env_manager, &check_torch_cmd, None, repo_path).is_ok() {
            info!("Torch detected as dependency, reinstalling with CUDA index...");
            let torch_index_url = self.get_default_torch_index_url();
            
            let torch_reinstall_result = if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--force-reinstall".into(), "--index-url".into(), torch_index_url, "torch".into(), "torchvision".into(), "torchaudio".into()]);
                run_tool_with_env(&self._env_manager, &uv_cmd, Some("Reinstalling torch with CUDA index"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "--force-reinstall".into(), "--index-url".into(), torch_index_url, "torch".into(), "torchvision".into(), "torchaudio".into()]);
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Reinstalling torch with CUDA index"), repo_path)
            };
            
            if torch_reinstall_result.is_err() {
                warn!("Failed to reinstall torch with CUDA index, keeping existing installation");
            }
        }

        Ok(())
    }

    fn get_python_in_env(&self, repo_name: &str) -> PathBuf {
        let cfg = self._config_manager.get_config();
        let venv_path = cfg.install_path.join("envs").join(repo_name);
        if cfg!(windows) {
            venv_path.join("python.exe")
        } else {
            venv_path.join("bin").join("python")
        }
    }

    fn get_pip_executable(&self, repo_name: &str) -> Vec<String> {
        let py = self.get_python_in_env(repo_name);
        if py.exists() { vec![py.to_string_lossy().to_string(), "-m".into(), "pip".into()] } else { vec!["python".into(), "-m".into(), "pip".into()] }
    }

    fn get_uv_executable(&self, repo_name: &str) -> Vec<String> {
        // Всегда вызываем модуль uv как "<env_python> -m uv";
        // надёжнее, чем полагаться на PATH и бинарь uv
        let mut py_path = self.get_python_in_env(repo_name);
        if !py_path.exists() {
            // Фолбэк на системный python (windows/python.exe, unix/python3)
            py_path = if cfg!(windows) { PathBuf::from("python.exe") } else { PathBuf::from("python3") };
        }
        vec![py_path.to_string_lossy().to_string(), "-m".into(), "uv".into()]
    }

    fn install_uv_in_venv(&self, repo_name: &str) -> Result<bool> {
        let uv_cmd = self.get_uv_executable(repo_name);
        // Try uv --version
        if run_tool_with_env_silent(&self._env_manager, &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], None, None).is_ok() { return Ok(true); }
        // Install uv via pip
        let mut pip_cmd = self.get_pip_executable(repo_name);
        pip_cmd.extend(["install".into(), "uv".into()]);
        let _ = run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing uv"), None);
        // Verify
        Ok(run_tool_with_env_silent(&self._env_manager, &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], None, None).is_ok())
    }

    fn execute_server_installation_plan(&self, repo_name: &str, plan: &serde_json::Value, repo_path: Option<&Path>) -> Result<bool> {
        let steps = plan.get("steps").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        for step in steps {
            self.process_server_step(repo_name, &step, repo_path)?;
        }
        
        // Check if torch is installed and reinstall with CUDA index if needed
        let mut check_cmd = self.get_pip_executable(repo_name);
        check_cmd.extend(["show".into(), "torch".into()]);
        
        let cfg = self._config_manager.get_config();
        let venv_path = cfg.install_path.join("envs").join(repo_name);
        
        if let Ok(output) = std::process::Command::new(&check_cmd[0])
            .args(&check_cmd[1..])
            .env("VIRTUAL_ENV", venv_path)
            .output() {
            if output.status.success() {
                info!("Torch detected as dependency, reinstalling with CUDA index");
                
                // Try with uv first
                let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
                let mut reinstall_cmd = if uv_available {
                    let mut cmd = self.get_uv_executable(repo_name);
                    cmd.extend(["pip".into(), "install".into()]);
                    cmd
                } else {
                    let mut cmd = self.get_pip_executable(repo_name);
                    cmd.push("install".into());
                    cmd
                };
                
                reinstall_cmd.extend(["--force-reinstall".into(), "--index-url".into(), self.get_default_torch_index_url()]);
                reinstall_cmd.extend(["torch".into(), "torchvision".into(), "torchaudio".into()]);
                
                if let Err(_) = run_tool_with_env_silent(&self._env_manager, &reinstall_cmd, Some("Reinstalling torch with CUDA"), repo_path) {
                    // Fallback to pip if uv fails
                    if uv_available {
                        info!("UV failed, falling back to pip for torch reinstall");
                        let mut pip_cmd = self.get_pip_executable(repo_name);
                        pip_cmd.extend(["install".into(), "--force-reinstall".into(), "--index-url".into(), self.get_default_torch_index_url()]);
                        pip_cmd.extend(["torch".into(), "torchvision".into(), "torchaudio".into()]);
                        run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Reinstalling torch with CUDA (pip)"), repo_path)?;
                    }
                }
            }
        }
        
        Ok(true)
    }

    fn process_server_step(&self, repo_name: &str, step: &serde_json::Value, repo_path: Option<&Path>) -> Result<()> {
        let step_type = step.get("type").and_then(|s| s.as_str()).unwrap_or("");
        match step_type {
            "requirements" => {
                if let Some(path) = step.get("path").and_then(|s| s.as_str()) {
                    let req_path = if let Some(rp) = repo_path { rp.join(path) } else { PathBuf::from(path) };
                    self.install_requirements_with_uv_or_pip(repo_name, &req_path, repo_path)?;

                }
            }
            "pip_install" | "regular" | "regular_only" => {
                // Prefer uv if available
                let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
                
                // Separate torch and regular packages
                let mut torch_packages: Vec<String> = Vec::new();
                let mut regular_packages: Vec<String> = Vec::new();
                
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs {
                        if let Some(s) = p.as_str() {
                            let mapped = self.apply_onnx_gpu_detection(s);
                            // Check if this is a torch-related package
                            let package_name = mapped.split(|c| "=><!".contains(c)).next().unwrap_or("").to_lowercase();
                            let is_torch_package = ["torch", "torchvision", "torchaudio", "torchtext", "torchdata"].contains(&package_name.as_str());
                            
                            // info!("Package: {} -> mapped: {} -> name: {} -> is_torch: {}", s, mapped, package_name, is_torch_package);
                            
                            if is_torch_package {
                                torch_packages.push(mapped);
                            } else {
                                regular_packages.push(mapped);
                            }
                        }
                    }
                }
                
                // info!("Torch packages: {:?}", torch_packages);
                // info!("Regular packages: {:?}", regular_packages);
                
                // Install regular packages first (without torch index)
                if !regular_packages.is_empty() {
                    let mut cmd = if uv_available { self.get_uv_executable(repo_name) } else { self.get_pip_executable(repo_name) };
                    if uv_available { cmd.extend(["pip".into(), "install".into()]); } else { cmd.push("install".into()); }
                    
                    let needs_pre = self.needs_onnx_nightly() && regular_packages.iter().any(|m| m.starts_with("onnxruntime-gpu"));
                    if needs_pre { cmd.push("--pre".into()); }
                    
                    // No extra index URL needed for regular packages
                    
                    for p in regular_packages { cmd.push(p); }
                    run_tool_with_env(&self._env_manager, &cmd, Some("Installing regular packages"), repo_path)?;
                }
                
                // Install torch packages separately with torch index
                if !torch_packages.is_empty() {
                    let mut cmd = if uv_available { self.get_uv_executable(repo_name) } else { self.get_pip_executable(repo_name) };
                    if uv_available { cmd.extend(["pip".into(), "install".into()]); } else { cmd.push("install".into()); }
                    
                    // Add torch index URL
                    if let Some(idx) = step.get("torch_index_url").and_then(|s| s.as_str()) {
                        cmd.extend(["--index-url".into(), idx.into()]);
                    } else if let Some(idx) = self.get_default_torch_index_url_opt() {
                        cmd.extend(["--index-url".into(), idx]);
                    }
                    
                    // No extra index URL needed for torch packages
                    
                    for p in torch_packages { cmd.push(p); }
                    run_tool_with_env(&self._env_manager, &cmd, None, repo_path)?;
                }
            }
            "torch" => {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "--force-reinstall".into()]);
                // Torch index URL
                if let Some(idx) = step.get("torch_index_url").and_then(|s| s.as_str()) {
                    pip_cmd.extend(["--index-url".into(), idx.into()]);
                } else if let Some(idx) = self.get_default_torch_index_url_opt() { pip_cmd.extend(["--index-url".into(), idx]); }
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() { pip_cmd.push(s.to_string()); } }
                }
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing PyTorch packages"), repo_path)?;
            }
            "onnxruntime" => {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.push("install".into());
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() { pip_cmd.push(self.apply_onnx_gpu_detection(s)); } }
                } else {
                    pip_cmd.push(self.apply_onnx_gpu_detection("onnxruntime"));
                }
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing ONNX packages"), repo_path)?;
            }
            "insightface" => {
                self.handle_insightface_package(repo_name, repo_path)?;

            }
            "triton" => {
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() {
                        let mut pip_cmd = self.get_pip_executable(repo_name);
                        pip_cmd.extend(["install".into(), s.to_string()]);
                        run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing Triton package"), repo_path)?;
                    }}
                }
            }
            _ => { debug!("Unknown step type in server plan: {}", step_type); }
        }
        Ok(())
    }

    fn get_default_torch_index_url_opt(&self) -> Option<String> {
        Some(self.get_default_torch_index_url())
    }

    fn get_default_torch_index_url(&self) -> String {
        if self._config_manager.has_cuda() {
            let gpu_name = self._config_manager.get_gpu_name();
            let gpu_generation = self._config_manager.detect_current_gpu_generation();
            let name_up = gpu_name.to_uppercase();
            let is_blackwell = name_up.contains("RTX 50") || format!("{:?}", gpu_generation).to_lowercase().contains("blackwell");
            if is_blackwell { return "https://download.pytorch.org/whl/nightly/cu128".into(); }
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
            if self._config_manager.has_cuda() {
                if let Some(cuda_version) = self._config_manager.get_cuda_version() {
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

    fn apply_onnx_gpu_detection(&self, base: &str) -> String {
        let up = self._config_manager.get_gpu_name().to_uppercase();
        if base.starts_with("onnxruntime") {
            if up.contains("NVIDIA") { return base.replace("onnxruntime", "onnxruntime-gpu"); }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) { return base.replace("onnxruntime", "onnxruntime-directml"); }
        }
        base.into()
    }

    fn needs_onnx_nightly(&self) -> bool {
        // Blackwell GPUs (RTX 50xx)
        if self._config_manager.has_cuda() {
            let gpu_generation = self._config_manager.detect_current_gpu_generation();
            let gpu_name = self._config_manager.get_gpu_name();
            let gen = format!("{:?}", gpu_generation).to_lowercase();
            let name_up = gpu_name.to_uppercase();
            let is_nvidia = name_up.contains("NVIDIA") || name_up.contains("RTX") || name_up.contains("GEFORCE");
            if is_nvidia && (gen.contains("blackwell") || name_up.contains("RTX 50")) {
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

    // Выбор конкретного пакета ORT для установки с учётом поколения GPU
    fn get_onnx_package_spec(&self) -> String {
        if self._config_manager.has_cuda() {
            let gpu_generation = self._config_manager.detect_current_gpu_generation();
            let gpu_name = self._config_manager.get_gpu_name();
            let gen = format!("{:?}", gpu_generation).to_lowercase();
            let name_up = gpu_name.to_uppercase();
            let is_nvidia = name_up.contains("NVIDIA") || name_up.contains("RTX") || name_up.contains("GEFORCE");
            let is_blackwell = gen.contains("blackwell") || name_up.contains("RTX 50");
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


    fn check_scripts_in_pyproject(&self, repo_path: &Path) -> Result<(bool, Option<String>)> {
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


    fn generate_startup_script(&self, repo_path: &Path, repo_info: &RepositoryInfo) -> Result<bool> {
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
        // info!("Starting generate_startup_script for repo: {}", repo_name);
        // println!("[PortableSource] Creating startup script for: {}", repo_name);
        
        let mut main_file = repo_info.main_file.clone();
        if main_file.is_none() { main_file = self.main_file_finder.find_main_file(&repo_name, repo_path, repo_info.url.as_deref()); }
        
        // Check for pyproject.toml scripts if main_file is not found
        let pyproject_path = repo_path.join("pyproject.toml");
        let (has_pyproject_scripts, script_module) = if main_file.is_none() && pyproject_path.exists() {
            info!("Main file not found, checking pyproject.toml for scripts");
            self.check_scripts_in_pyproject(repo_path)?
        } else {
            (false, None)
        };

        let install_path = &self.install_path;

        let bat_file = repo_path.join(format!("start_{}.bat", repo_name));
        let program_args = repo_info.program_args.clone().unwrap_or_default();

        // CUDA PATH section if configured
        let cuda_section = if self._config_manager.has_cuda() {
            format!(
                "set cuda_bin=%env_path%\\CUDA\\bin\nset cuda_lib=%env_path%\\CUDA\\lib\nset cuda_lib_64=%env_path%\\CUDA\\lib\\x64\nset cuda_nvml_bin=%env_path%\\CUDA\\nvml\\bin\nset cuda_nvml_lib=%env_path%\\CUDA\\nvml\\lib\nset cuda_nvvm_bin=%env_path%\\CUDA\\nvvm\\bin\nset cuda_nvvm_lib=%env_path%\\CUDA\\nvvm\\lib\n\nset PATH=%cuda_bin%;%PATH%\nset PATH=%cuda_lib%;%PATH%\nset PATH=%cuda_lib_64%;%PATH%\nset PATH=%cuda_nvml_bin%;%PATH%\nset PATH=%cuda_nvml_lib%;%PATH%\nset PATH=%cuda_nvvm_bin%;%PATH%\nset PATH=%cuda_nvvm_lib%;%PATH%\n"
            )
        } else { "REM No CUDA paths configured".into() };
        
        // Generate base script content without execution command
        let base_content = format!("@echo off\n").to_string() + &format!(
            "echo Launch {}...\n\nsubst X: {}\nX:\n\nset env_path=X:\\ps_env\nset envs_path=X:\\envs\nset repos_path=X:\\repos\nset ffmpeg_path=%env_path%\\ffmpeg\nset python_path=%envs_path%\\{}\nset python_exe=%python_path%\\python.exe\nset repo_path=%repos_path%\\{}\n\nset tmp_path=X:\\tmp\nset USERPROFILE=%tmp_path%\nset TEMP=%tmp_path%\\Temp\nset TMP=%tmp_path%\\Temp\nset APPDATA=%tmp_path%\\AppData\\Roaming\nset LOCALAPPDATA=%tmp_path%\\AppData\\Local\nset HF_HOME=%repo_path%\\huggingface_home\nset XDG_CACHE_HOME=%tmp_path%\nset HF_DATASETS_CACHE=%HF_HOME%\\datasets\n\nset PYTHONIOENCODING=utf-8\nset PYTHONUNBUFFERED=1\nset PYTHONDONTWRITEBYTECODE=1\n\nREM === CUDA PATHS ===\n{}\nset PATH=%python_path%;%PATH%\nset PATH=%python_path%\\Scripts;%PATH%\nset PATH=%ffmpeg_path%;%PATH%\n\ncd /d \"%repo_path%\"\n",
            repo_name,
            install_path.display(),
            repo_name,
            repo_name,
            cuda_section,
        );
        
        // Determine execution command based on available options
        let content = if let Some(main_file_path) = main_file {
            // Case 1: main_file found - use it
            // info!("Using main file: {}", main_file_path);
            base_content + &format!(
                "\"%python_exe%\" {} {}\nset EXIT_CODE=%ERRORLEVEL%\n\necho Cleaning up...\nsubst X: /D\n\nif %EXIT_CODE% neq 0 (\n    echo.\n    echo Program finished with error (code: %EXIT_CODE%)\n) else (\n    echo.\n    echo Program finished successfully\n)\n\npause\n",
                main_file_path,
                program_args,
            )
        } else if has_pyproject_scripts {
            // Case 2: no main_file but pyproject.toml has scripts
            if let Some(module_path) = script_module {
                info!("No main file found, using pyproject.toml script: {}", module_path);
                base_content + &format!(
                    "\"%python_exe%\" -m {} {}\nset EXIT_CODE=%ERRORLEVEL%\n\necho Cleaning up...\nsubst X: /D\n\nif %EXIT_CODE% neq 0 (\n    echo.\n    echo Program finished with error (code: %EXIT_CODE%)\n) else (\n    echo.\n    echo Program finished successfully\n)\n\npause\n",
                    module_path,
                    program_args,
                )
            } else {
                // Fallback case - should not happen but handle gracefully
                warn!("No main file or valid pyproject script found, generating interactive shell");
                base_content + &format!(
                    "\"%python_exe%\"\nset EXIT_CODE=%ERRORLEVEL%\n\necho Cleaning up...\nsubst X: /D\n\nif %EXIT_CODE% neq 0 (\n    echo.\n    echo Program finished with error (code: %EXIT_CODE%)\n) else (\n    echo.\n    echo Program finished successfully\n)\n\npause\n"
                )
            }
        } else {
            // Case 3: no main_file and no pyproject.toml - just python shell
            warn!("No main file or pyproject.toml scripts found, generating interactive Python shell");
            base_content + &format!(
                "\"%python_exe%\"\nset EXIT_CODE=%ERRORLEVEL%\n\necho Cleaning up...\nsubst X: /D\n\nif %EXIT_CODE% neq 0 (\n    echo.\n    echo Program finished with error (code: %EXIT_CODE%)\n) else (\n    echo.\n    echo Program finished successfully\n)\n\npause\n"
            )
        };
        let mut f = fs::File::create(&bat_file)?;
        f.write_all(content.as_bytes())?;
        // info!("Successfully created startup script: {:?}", bat_file);

        Ok(true)
    }

    #[cfg(unix)]
    fn generate_startup_script_unix(&self, repo_path: &Path, repo_info: &RepositoryInfo) -> Result<bool> {
        use std::os::unix::fs::PermissionsExt;
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
        let mut main_file = repo_info.main_file.clone();
        if main_file.is_none() { main_file = self.main_file_finder.find_main_file(&repo_name, repo_path, repo_info.url.as_deref()); }
        
        // Check for pyproject.toml scripts if main_file is not found
        let pyproject_path = repo_path.join("pyproject.toml");
        let (has_pyproject_scripts, script_module) = if main_file.is_none() && pyproject_path.exists() {
            info!("Main file not found, checking pyproject.toml for scripts");
            self.check_scripts_in_pyproject(repo_path)?
        } else {
            (false, None)
        };

        let cfg = self._config_manager.get_config();
        let install_path = &self.install_path;

        let sh_file = repo_path.join(format!("start_{}.sh", repo_name));
        let program_args = repo_info.program_args.clone().unwrap_or_default();
        
        // Remove duplicate check since we already have it above

        // CUDA PATH exports if configured (optional)
        let mut cuda_exports = String::new();
        if self._config_manager.has_cuda() {
            let base = self._config_manager.get_cuda_base_path().to_string_lossy();
            let bin = self._config_manager.get_cuda_bin().to_string_lossy();
            let lib = self._config_manager.get_cuda_lib().to_string_lossy();
            let lib64 = self._config_manager.get_cuda_lib_64().to_string_lossy();
            cuda_exports.push_str(&format!("export CUDA_PATH=\"{}\"\n", base));
            cuda_exports.push_str(&format!("export CUDA_HOME=\"{}\"\n", base));
            cuda_exports.push_str(&format!("export CUDA_ROOT=\"{}\"\n", base));
            cuda_exports.push_str(&format!("export PATH=\"{}:$PATH\"\n", bin));
            // Use default expansion for unset variable due to 'set -u'
            cuda_exports.push_str(&format!("export LD_LIBRARY_PATH=\"{}:{}:${{LD_LIBRARY_PATH:-}}\"\n", lib, lib64));
        }

        // Generate base script content without execution command
        let base_content = format!("#!/usr/bin/env bash\nset -Eeuo pipefail\n\nINSTALL=\"{}\"
ENV_PATH=\"$INSTALL/ps_env\"
BASE_PREFIX=\"$ENV_PATH/mamba_env\"
REPO_PATH=\"{}\"
VENV=\"$INSTALL/envs/{}\"
PYEXE=\"$VENV/bin/python\"\n\n# Detect mode: allow override via PORTABLESOURCE_MODE\nMODE=\"${{PORTABLESOURCE_MODE:-}}\"\nif [[ -z \"$MODE\" ]]; then\n  if command -v git >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1 && command -v ffmpeg >/dev/null 2>&1; then\n    MODE=cloud
  else
    MODE=desk
  fi
fi

# prepend micromamba base bin to PATH (no activation) in DESK mode
if [[ \"$MODE\" == \"desk\" ]]; then
  export PATH=\"$BASE_PREFIX/bin:$PATH\"
fi

# activate project venv if present (be tolerant to unset vars)
if [[ -f \"$VENV/bin/activate\" ]]; then
  set +u
  source \"$VENV/bin/activate\" || true
  set -u
fi

{}\ncd \"$REPO_PATH\"\n",

            install_path.to_string_lossy(),
            repo_path.to_string_lossy(),
            repo_name,
            cuda_exports,
        );
        
        // Determine execution command based on available options
        let content = if let Some(main_file) = main_file {
            // Use main_file if available
            base_content + &format!(
                "if [[ -x \"$PYEXE\" ]]; then\n  exec \"$PYEXE\" \"{}\" {}\nelse\n  exec python3 \"{}\" {}\nfi\n",
                main_file,
                program_args,
                main_file,
                program_args,
            )
        } else if has_pyproject_scripts {
            if let Some(module_path) = script_module {
                info!("Using pyproject.toml script module: {}", module_path);
                base_content + &format!(
                    "if [[ -x \"$PYEXE\" ]]; then\n  exec \"$PYEXE\" -m {} {}\nelse\n  exec python3 -m {} {}\nfi\n",
                    module_path,
                    program_args,
                    module_path,
                    program_args,
                )
            } else {
                warn!("pyproject.toml found but no suitable scripts detected");
                base_content + "if [[ -x \"$PYEXE\" ]]; then\n  exec \"$PYEXE\"\nelse\n  exec python3\nfi\n"
            }
        } else {
            // No main_file and no pyproject.toml - just run python
            warn!("No main file or pyproject.toml scripts found, generating basic python launcher");
            base_content + "if [[ -x \"$PYEXE\" ]]; then\n  exec \"$PYEXE\"\nelse\n  exec python3\nfi\n"
        };

        let mut f = fs::File::create(&sh_file)?;
        use std::io::Write as _;
        f.write_all(content.as_bytes())?;
        let mut perms = fs::metadata(&sh_file)?.permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&sh_file, perms)?;
        Ok(true)
    }

    fn normalize_repo_name(&self, input: &str, repo_info: &RepositoryInfo) -> Result<String> {
        if !input.trim().is_empty() { return Ok(input.to_lowercase()); }
        if let Some(url) = &repo_info.url {
            let url = Url::parse(url).map_err(|e| PortableSourceError::repository(e.to_string()))?;
            return self.extract_repo_name_from_url(&url);
        }
        Err(PortableSourceError::repository("Cannot determine repository name"))
    }

    fn get_repository_info(&self, repo_url_or_name: &str) -> Result<Option<RepositoryInfo>> {
        // URL input
        if self.is_repository_url(repo_url_or_name) {
            let url = repo_url_or_name.to_string();
            let name = self.extract_repo_name_from_url(&Url::parse(&url).map_err(|e| PortableSourceError::repository(e.to_string()))?)?;
            // try server by name
            if let Ok(Some(mut info)) = self.server_client.get_repository_info(&name) {
                if info.url.is_none() { info.url = Some(url); }
                return Ok(Some(info));
            }
            return Ok(Some(RepositoryInfo { url: Some(url), main_file: None, program_args: None }));
        }
        // owner/name form
        if repo_url_or_name.contains('/') && !repo_url_or_name.starts_with("http") {
            let url = format!("https://github.com/{}", repo_url_or_name);
            return Ok(Some(RepositoryInfo { url: Some(url), main_file: None, program_args: None }));
        }
        // plain name
        if let Ok(Some(info)) = self.server_client.get_repository_info(repo_url_or_name) { return Ok(Some(info)); }
        if let Some(fb) = self.fallback_repositories.get(&repo_url_or_name.to_lowercase()) {
            return Ok(Some(RepositoryInfo { url: Some(fb.url.clone()), main_file: fb.main_file.clone(), program_args: fb.program_args.clone() }));
        }
        Ok(None)
    }
}

// ===== Requirements analysis (Rust port of Python logic) =====

#[derive(Clone, Debug, PartialEq, Eq)]
enum PackageType { Regular, Torch, Onnxruntime, Insightface, Triton }

#[derive(Clone, Debug)]
#[allow(dead_code)]
struct PackageInfo {
    name: String,
    version: Option<String>,
    extras: Option<Vec<String>>,
    package_type: PackageType,
    original_line: String,
}

impl ToString for PackageInfo {
    fn to_string(&self) -> String {
        if let Some(v) = &self.version { format!("{}=={}", self.name, v) } else { self.name.clone() }
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

struct RequirementsAnalyzer<'a> { config_manager: &'a ConfigManager }

impl<'a> RequirementsAnalyzer<'a> {
    fn new(config_manager: &'a ConfigManager) -> Self { Self { config_manager } }

    fn analyze_requirements(&self, requirements_path: &Path) -> Vec<PackageInfo> {
        let mut packages = Vec::new();
        if let Ok(content) = fs::read_to_string(requirements_path) {
            for line in content.lines() {
                if let Some(pkg) = self.parse_requirement_line(line) { packages.push(pkg); }
            }
        }
        packages
    }

    fn parse_requirement_line(&self, line_in: &str) -> Option<PackageInfo> {
        let line = line_in.split('#').next().unwrap_or("").trim().to_string();
        if line.is_empty() || line.starts_with('-') || line.contains("--index-url") || line.contains("--extra-index-url") { return None; }
        // basic parse: name[extras]==version
        let (name_part, version) = if let Some(idx) = line.find(|c: char| "=><!~".contains(c)) {
            let (n, v) = line.split_at(idx);
            (n.trim().to_string(), Some(v.trim_matches(|c| c == '=' || c == '>' || c == '<' || c == '!' || c == '~').to_string()))
        } else { (line.clone(), None) };
        let (name, extras_opt) = if let Some(start) = name_part.find('[') { let end = name_part.find(']').unwrap_or(name_part.len()); (name_part[..start].to_string(), Some(name_part[start+1..end].split(',').map(|s| s.to_string()).collect())) } else { (name_part, None) };
        let lname = name.to_lowercase();
        let package_type = if ["torch","torchvision","torchaudio","torchtext","torchdata"].contains(&lname.as_str()) { PackageType::Torch }
            else if lname.starts_with("onnxruntime") { PackageType::Onnxruntime }
            else if lname.starts_with("insightface") { PackageType::Insightface }
            else if lname.starts_with("triton") { PackageType::Triton }
            else { PackageType::Regular };
        Some(PackageInfo { name: lname, version, extras: extras_opt, package_type, original_line: line_in.to_string() })
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
        plan.torch_index_url = Some(self.get_default_torch_index_url());
        // onnx package name by GPU vendor
        plan.onnx_package_name = Some(self.get_onnx_package_name());
        plan
    }

    fn get_default_torch_index_url(&self) -> String {
        if self.config_manager.has_cuda() {
            let gpu_name = self.config_manager.get_gpu_name();
            let gpu_generation = self.config_manager.detect_current_gpu_generation();
            let name_up = gpu_name.to_uppercase();
            let is_blackwell = name_up.contains("RTX 50") || format!("{:?}", gpu_generation).to_lowercase().contains("blackwell");
            if is_blackwell { return "https://download.pytorch.org/whl/nightly/cu128".into(); }
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
            if up.contains("NVIDIA") { return "onnxruntime-gpu".into(); }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) { return "onnxruntime-directml".into(); }
        }
        "onnxruntime".into()
    }
}

impl RepositoryInstaller {
    fn handle_insightface_package(&self, repo_name: &str, repo_path: Option<&Path>) -> Result<()> {
        // Windows prebuilt wheel; fallback to pip package
        if cfg!(windows) {
            let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
            // Устанавливаем одновременно insightface и совместимую версию numpy
            let wheel = "https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl";
            if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--extra-index-url".into(), "https://pypi.ngc.nvidia.com".into(), "-U".into(), wheel.into(), "numpy==1.26.4".into()]);
                run_tool_with_env_silent(&self._env_manager, &uv_cmd, Some("Installing insightface + numpy (uv)"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "-U".into(), wheel.into(), "numpy==1.26.4".into()]);
                run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing insightface + numpy (pip)"), repo_path)
            }
        } else {
            let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
            // Ставим одновременно insightface и совместимый numpy
            if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "--extra-index-url".into(), "https://pypi.ngc.nvidia.com".into(), "-U".into(), "insightface".into(), "numpy==1.26.4".into()]);
                run_tool_with_env_silent(&self._env_manager, &uv_cmd, Some("Installing insightface + numpy (uv)"), repo_path)
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "-U".into(), "insightface".into(), "numpy==1.26.4".into()]);
                run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing insightface + numpy (pip)"), repo_path)
            }
        }
    }

    /// Install repository as a package using uv pip install .
    fn install_repo_as_package(&self, repo_name: &str, repo_path: &Path) -> Result<()> {
        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
        
        if uv_available {
            let mut uv_cmd = self.get_uv_executable(repo_name);
            uv_cmd.extend(["pip".into(), "install".into(), "--extra-index-url".into(), "https://pypi.ngc.nvidia.com".into(), ".".into()]);
            run_tool_with_env_silent(&self._env_manager, &uv_cmd, Some("Installing repository as package (uv)"), Some(repo_path))

        } else {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), ".".into()]);
            run_tool_with_env_silent(&self._env_manager, &pip_cmd, Some("Installing repository as package (pip)"), Some(repo_path))
        }
    }

    /// Extract dependencies from pyproject.toml and create requirements_pyp.txt
    fn extract_dependencies_from_pyproject(&self, pyproject_path: &Path, repo_path: &Path) -> Result<PathBuf> {
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
}

fn copy_dir_recursive(from: &Path, to: &Path) -> Result<()> {
    fs::create_dir_all(to)?;
    for entry in fs::read_dir(from)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let src = entry.path();
        let dst = to.join(entry.file_name());
        if ty.is_dir() { copy_dir_recursive(&src, &dst)?; } else { fs::copy(&src, &dst)?; }
    }
    Ok(())
}

#[derive(Clone, Copy)]
#[allow(dead_code)]
enum CommandType {
    Git,
    Pip,
    Uv,
    Python,
    Other,
}

fn run_with_progress_typed(mut cmd: Command, label: Option<&str>, command_type: CommandType) -> Result<()> {
    if let Some(l) = label { info!("{}...", l); }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| PortableSourceError::command(e.to_string()))?;
    
    let mut stderr_lines = Vec::new();
    
    let (_stdout_prefix, _stderr_prefix, error_prefix) = match command_type {
        CommandType::Git => ("[Git]", "[Git Error]", "Git command failed"),
        CommandType::Pip => ("[Pip]", "[Pip Error]", "Pip command failed"),
        CommandType::Uv => ("[UV]", "[UV Error]", "UV command failed"),
        CommandType::Python => ("[Python]", "[Python Error]", "Python command failed"),
        CommandType::Other => ("[Command]", "[Command Error]", "Command failed"),
    };
    
    if let Some(out) = child.stdout.take() {
        let reader = BufReader::new(out);
        for line in reader.lines().flatten() { 
            debug!("{}", line);
        }
    }
    
    if let Some(err) = child.stderr.take() {
        let reader = BufReader::new(err);
        for line in reader.lines().flatten() {
            debug!("stderr: {}", line);
            stderr_lines.push(line);
        }
    }
    
    let status = child.wait().map_err(|e| PortableSourceError::command(e.to_string()))?;
    if !status.success() {
        let error_msg = if !stderr_lines.is_empty() {
            format!("Command failed with status: {}\nError output:\n{}", status, stderr_lines.join("\n"))
        } else {
            format!("Command failed with status: {}", status)
        };
        debug!("{}: {}", error_prefix, error_msg);
        return Err(PortableSourceError::command(error_msg));
    }
    Ok(())
}

fn run_tool_with_env_silent(env_manager: &PortableEnvironmentManager, args: &Vec<String>, label: Option<&str>, cwd: Option<&Path>) -> Result<()> {
    if args.is_empty() { return Ok(()); }
    if let Some(l) = label { info!("{}...", l); }
    let mut cmd = Command::new(&args[0]);
    for a in &args[1..] { cmd.arg(a); }
    if let Some(dir) = cwd { cmd.current_dir(dir); }
    let envs = env_manager.setup_environment_for_subprocess();
    cmd.envs(envs).stdout(Stdio::null()).stderr(Stdio::null());
    
    let status = cmd.status().map_err(|e| PortableSourceError::command(e.to_string()))?;
    if !status.success() {
        return Err(PortableSourceError::command(format!("Command failed with status: {}", status)));
    }
    Ok(())
}

fn run_tool_with_env(env_manager: &PortableEnvironmentManager, args: &Vec<String>, label: Option<&str>, cwd: Option<&Path>) -> Result<()> {
    if args.is_empty() { return Ok(()); }
    let mut cmd = Command::new(&args[0]);
    for a in &args[1..] { cmd.arg(a); }
    if let Some(dir) = cwd { cmd.current_dir(dir); }
    let envs = env_manager.setup_environment_for_subprocess();
    cmd.envs(envs).stdout(Stdio::piped()).stderr(Stdio::piped());
    
    // Determine command type based on the executable and arguments
    let command_type = if args.len() >= 2 {
        let exe_name = std::path::Path::new(&args[0]).file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or(&args[0])
            .to_lowercase();
        
        if exe_name == "python" || exe_name == "python3" {
            if args.len() >= 3 && args[1] == "-m" {
                match args[2].as_str() {
                    "pip" => CommandType::Pip,
                    "uv" => CommandType::Uv,
                    _ => CommandType::Python,
                }
            } else {
                CommandType::Python
            }
        } else if exe_name == "pip" || exe_name == "pip3" {
            CommandType::Pip
        } else if exe_name == "uv" {
            CommandType::Uv
        } else if exe_name == "git" {
            CommandType::Git
        } else {
            CommandType::Other
        }
    } else {
        CommandType::Other
    };
    
    run_with_progress_typed(cmd, label, command_type)
}

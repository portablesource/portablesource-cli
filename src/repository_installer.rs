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
            let mut cmd = std::process::Command::new(&git_exe);
            cmd.current_dir(&repo_path).arg("fetch").arg("--all");
            let _ = run_with_progress(cmd, Some("Fetching from remote"));
        }
        if reset_hard_to(&git_exe, &repo_path, "origin/main").is_err() {
            let _ = reset_hard_to(&git_exe, &repo_path, "origin/master");
        }
        {
            let mut cmd = std::process::Command::new(&git_exe);
            cmd.current_dir(&repo_path).arg("pull");
            let _ = run_with_progress(cmd, Some("Pulling latest changes"));
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

        // Generate startup script
        self.generate_startup_script(&repo_path, &repo_info)?;

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
        let _ = self._config_manager.save_config();
        self.install_dependencies(&repo_path).await?;

        println!("[PortableSource] Generating start script...");
        // Final save after script generation
        let _ = self._config_manager.save_config();
        self.generate_startup_script(&repo_path, &repo_info)?;

        let _ = self.server_client.send_download_stats(&name);

        println!("[PortableSource] Done: '{}'", name);
        // Special setup hooks
        self.apply_special_setup(&name, &repo_path)?;
        Ok(())
    }
    
    async fn clone_repository(&self, repo_url: &str, repo_path: &Path) -> Result<()> {
        info!("Cloning repository to: {:?}", repo_path);
        println!("[PortableSource] git clone {} -> {:?}", repo_url, repo_path);
        let git_exe = self.get_git_executable();
        let parent = repo_path.parent().ok_or_else(|| PortableSourceError::repository("Invalid repo path"))?;
        fs::create_dir_all(parent)?;
        // Clone directly into target directory to avoid nested folder names
        let mut cmd = Command::new(git_exe);
        cmd.current_dir(parent).arg("clone").arg(repo_url).arg(repo_path.file_name().unwrap());
        run_with_progress(cmd, Some("Cloning repository"))?;
        Ok(())
    }
    
    async fn install_dependencies(&self, repo_path: &Path) -> Result<()> {
        info!("Installing dependencies for: {:?}", repo_path);
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();

        // Ensure copied python environment exists
        self.create_venv_environment(&repo_name)?;

        // Try server installation plan
        if let Some(plan) = self.server_client.get_installation_plan(&repo_name)? {
            if self.execute_server_installation_plan(&repo_name, &plan, Some(repo_path))? {
                return Ok(());
            } else {
                warn!("Server installation failed, falling back to local requirements.txt");
            }
        }

        // Fallback to requirements.txt variants
        let candidates = [
            repo_path.join("requirements.txt"),
            repo_path.join("requirements").join("requirements.txt"),
            repo_path.join("install").join("requirements.txt"),
        ];
        let requirements = candidates.iter().find(|p| p.exists());
        if let Some(req) = requirements {
            info!("Installing from {:?}", req);
            self.install_requirements_with_uv_or_pip(&repo_name, req)?;
        } else {
            info!("No requirements.txt found");
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
    fn apply_special_setup(&self, repo_name: &str, _repo_path: &Path) -> Result<()> {
        match repo_name.to_lowercase().as_str() {
            "wangp" => {
                // Install mmgp==3.5.6 into this repo env
                if self.install_uv_in_venv(repo_name).unwrap_or(false) {
            let mut uv_cmd = self.get_uv_executable(repo_name);
                    uv_cmd.extend(["pip".into(), "install".into(), "mmgp==3.5.6".into()]);
                    let _ = run_tool_with_env(&self._env_manager, &uv_cmd, Some("Installing mmgp for wangp"));
                } else {
                    let mut pip_cmd = self.get_pip_executable(repo_name);
                    pip_cmd.extend(["install".into(), "mmgp==3.5.6".into()]);
                    let _ = run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing mmgp for wangp"));
                }
                Ok(())
            }
            _ => Ok(())
        }
    }

    fn get_git_executable(&self) -> String {
        if let Some(p) = self._env_manager.get_git_executable() { return p.to_string_lossy().to_string(); }
        "git".into()
    }

    async fn clone_or_update_repository(&self, repo_info: &RepositoryInfo, repo_path: &Path) -> Result<()> {
        let git_exe = self.get_git_executable();
        if repo_path.exists() {
            if repo_path.join(".git").exists() { self.update_repository_with_fixes(&git_exe, repo_path)?; return Ok(()); }
            return Err(PortableSourceError::repository(format!("Directory exists but is not a git repository: {:?}", repo_path)));
        }
        let url = repo_info.url.clone().ok_or_else(|| PortableSourceError::repository("Missing repository URL"))?;
        let parent = repo_path.parent().ok_or_else(|| PortableSourceError::repository("Invalid repo path"))?;
        fs::create_dir_all(parent)?;
        let mut cmd = Command::new(&git_exe);
        cmd.current_dir(parent).arg("clone");
        if let Some(branch) = None::<String> { cmd.arg("-b").arg(branch); }
        cmd.arg(&url).arg(repo_path.file_name().unwrap());
        run_with_progress(cmd, Some("Cloning repository"))?;
        Ok(())
    }

    fn update_repository_with_fixes(&self, git_exe: &str, repo_path: &Path) -> Result<()> {
        let max_attempts = 3;
        for attempt in 0..max_attempts {
            let mut cmd = Command::new(git_exe);
            cmd.current_dir(repo_path).arg("pull");
            match run_with_progress(cmd, Some("Updating repository")) {
                Ok(_) => return Ok(()),
                Err(e) => {
                    warn!("git pull failed (attempt {}/{}): {}", attempt + 1, max_attempts, e);
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
        for args in fixes {
            let mut cmd = Command::new(git_exe);
            cmd.current_dir(repo_path);
            for a in args { cmd.arg(a); }
            let _ = run_with_progress(cmd, None);
        }
        Ok(())
    }

    fn create_venv_environment(&self, repo_name: &str) -> Result<()> {
        let cfg = self._config_manager.get_config();
        if cfg.install_path.as_os_str().is_empty() { return Err(PortableSourceError::installation("Install path not configured")); }
        let install_path = cfg.install_path.clone();
        let envs_path = install_path.join("envs");
        let venv_path = envs_path.join(repo_name);
        let ps_env_python = install_path.join("ps_env").join("python");
        if !ps_env_python.exists() { return Err(PortableSourceError::installation(format!("Portable Python not found at: {:?}", ps_env_python))); }
        if venv_path.exists() { fs::remove_dir_all(&venv_path)?; }
        info!("Creating environment by copying portable Python: {:?} -> {:?}", ps_env_python, venv_path);
        copy_dir_recursive(&ps_env_python, &venv_path)?;
        let python_exe = if cfg!(windows) { venv_path.join("python.exe") } else { venv_path.join("bin").join("python") };
        if !python_exe.exists() { return Err(PortableSourceError::installation(format!("Python executable not found in {:?}", venv_path))); }
        Ok(())
    }

    fn install_requirements_with_uv_or_pip(&self, repo_name: &str, requirements: &Path) -> Result<()> {
        // Parse requirements and build plan
        let analyzer = RequirementsAnalyzer::new(&self._config_manager);
        let packages = analyzer.analyze_requirements(requirements);
        let plan = analyzer.create_installation_plan(&packages);

        // Ensure uv if available
        let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);

        // 1) Regular packages via uv (prefer) или pip -r — сначала базовые зависимости
        if !plan.regular_packages.is_empty() {
            let tmp = requirements.parent().unwrap_or_else(|| Path::new(".")).join("requirements_regular_temp.txt");
            {
                let mut file = fs::File::create(&tmp)?;
                for p in &plan.regular_packages { writeln!(file, "{}", p.original_line)?; }
            }
            let res = if uv_available {
                let mut uv_cmd = self.get_uv_executable(repo_name);
                uv_cmd.extend(["pip".into(), "install".into(), "-r".into(), tmp.to_string_lossy().to_string()]);
                run_tool_with_env(&self._env_manager, &uv_cmd, Some("Installing regular packages with uv"))
            } else {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.extend(["install".into(), "-r".into(), tmp.to_string_lossy().to_string()]);
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing regular packages with pip"))
            };
            let _ = fs::remove_file(&tmp);
            res?;
        }

        // 2) ONNX пакеты
        if !plan.onnx_packages.is_empty() {
            let package_name = plan.onnx_package_name.clone().unwrap_or_else(|| "onnxruntime".into());
            let mut version_suffix = String::new();
            if let Some(pkg) = plan.onnx_packages.iter().find(|p| p.name == "onnxruntime" && p.version.is_some()) {
                version_suffix = format!("=={}", pkg.version.clone().unwrap());
            }
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), format!("{}{}", package_name, version_suffix)]);
            run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing ONNX package"))?;
        }

        // 3) Torch пакеты (через pip) с нужным индексом
        if !plan.torch_packages.is_empty() {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.push("install".into());
            pip_cmd.push("--force-reinstall".into());
            if let Some(index) = plan.torch_index_url.as_ref() {
                pip_cmd.push("--index-url".into());
                pip_cmd.push(index.clone());
            }
            for p in &plan.torch_packages { pip_cmd.push(p.to_string()); }
            run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing PyTorch packages"))?;
        }

        // 4) Triton (если присутствует)
        if !plan.triton_packages.is_empty() {
            for pkg in &plan.triton_packages {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                let spec = if let Some(v) = &pkg.version { format!("{}=={}", pkg.name, v) } else { pkg.name.clone() };
                pip_cmd.extend(["install".into(), spec]);
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing Triton package"))?;
            }
        }

        // 5) InsightFace — строго в самом конце, с -U для согласования numpy
        if !plan.insightface_packages.is_empty() {
            for _p in &plan.insightface_packages {
                self.handle_insightface_package(repo_name)?;
            }
        }

        Ok(())
    }

    fn get_python_in_env(&self, repo_name: &str) -> PathBuf {
        let cfg = self._config_manager.get_config();
        let venv_path = cfg.install_path.join("envs").join(repo_name);
        if cfg!(windows) { venv_path.join("python.exe") } else { venv_path.join("bin").join("python") }
    }

    fn get_pip_executable(&self, repo_name: &str) -> Vec<String> {
        let py = self.get_python_in_env(repo_name);
        if py.exists() { vec![py.to_string_lossy().to_string(), "-m".into(), "pip".into()] } else { vec!["python".into(), "-m".into(), "pip".into()] }
    }

    fn get_uv_executable(&self, repo_name: &str) -> Vec<String> {
        let py = self.get_python_in_env(repo_name);
        if py.exists() { vec![py.to_string_lossy().to_string(), "-m".into(), "uv".into()] } else { vec!["python".into(), "-m".into(), "uv".into()] }
    }

    fn install_uv_in_venv(&self, repo_name: &str) -> Result<bool> {
        let uv_cmd = self.get_uv_executable(repo_name);
        // Try uv --version
        if run_tool_with_env(&self._env_manager, &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], None).is_ok() { return Ok(true); }
        // Install uv via pip
        let mut pip_cmd = self.get_pip_executable(repo_name);
        pip_cmd.extend(["install".into(), "uv".into()]);
        let _ = run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing uv"));
        // Verify
        Ok(run_tool_with_env(&self._env_manager, &vec![uv_cmd[0].clone(), uv_cmd[1].clone(), uv_cmd[2].clone(), "--version".into()], None).is_ok())
    }

    fn execute_server_installation_plan(&self, repo_name: &str, plan: &serde_json::Value, repo_path: Option<&Path>) -> Result<bool> {
        let steps = plan.get("steps").and_then(|s| s.as_array()).cloned().unwrap_or_default();
        for step in steps {
            self.process_server_step(repo_name, &step, repo_path)?;
        }
        Ok(true)
    }

    fn process_server_step(&self, repo_name: &str, step: &serde_json::Value, repo_path: Option<&Path>) -> Result<()> {
        let step_type = step.get("type").and_then(|s| s.as_str()).unwrap_or("");
        match step_type {
            "requirements" => {
                if let Some(path) = step.get("path").and_then(|s| s.as_str()) {
                    let req_path = if let Some(rp) = repo_path { rp.join(path) } else { PathBuf::from(path) };
                    self.install_requirements_with_uv_or_pip(repo_name, &req_path)?;
                }
            }
            "pip_install" | "regular" | "regular_only" => {
                // Prefer uv if available
                let uv_available = self.install_uv_in_venv(repo_name).unwrap_or(false);
                let mut cmd = if uv_available { self.get_uv_executable(repo_name) } else { self.get_pip_executable(repo_name) };
                if uv_available { cmd.extend(["pip".into(), "install".into()]); } else { cmd.push("install".into()); }
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() { cmd.push(s.to_string()); } }
                }
                self.add_install_flags_and_urls(repo_name, &mut cmd, step)?;
                run_tool_with_env(&self._env_manager, &cmd, Some("Installing packages"))?;
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
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing PyTorch packages"))?;
            }
            "onnxruntime" => {
                let mut pip_cmd = self.get_pip_executable(repo_name);
                pip_cmd.push("install".into());
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() { pip_cmd.push(self.apply_onnx_gpu_detection(s)); } }
                } else {
                    pip_cmd.push(self.apply_onnx_gpu_detection("onnxruntime"));
                }
                run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing ONNX packages"))?;
            }
            "insightface" => {
                self.handle_insightface_package(repo_name)?;
            }
            "triton" => {
                if let Some(pkgs) = step.get("packages").and_then(|p| p.as_array()) {
                    for p in pkgs { if let Some(s) = p.as_str() {
                        let mut pip_cmd = self.get_pip_executable(repo_name);
                        pip_cmd.extend(["install".into(), s.to_string()]);
                        run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing Triton package"))?;
                    }}
                }
            }
            _ => { debug!("Unknown step type in server plan: {}", step_type); }
        }
        Ok(())
    }

    fn add_install_flags_and_urls(&self, _repo_name: &str, cmd: &mut Vec<String>, step: &serde_json::Value) -> Result<()> {
        if let Some(flags) = step.get("install_flags").and_then(|s| s.as_array()) {
            for f in flags { if let Some(s) = f.as_str() { cmd.push(s.to_string()); } }
        }
        // Torch index if any torch packages appear
        let has_torch = cmd.iter().any(|a| a.starts_with("torch"));
        if has_torch && !cmd.iter().any(|a| a == "--index-url") {
            if let Some(idx) = step.get("torch_index_url").and_then(|s| s.as_str()) {
                cmd.extend(["--index-url".into(), idx.into()]);
            } else if let Some(idx) = self.get_default_torch_index_url_opt() {
                cmd.extend(["--index-url".into(), idx]);
            }
        }
        Ok(())
    }

    fn get_default_torch_index_url_opt(&self) -> Option<String> {
        Some(self.get_default_torch_index_url())
    }

    fn get_default_torch_index_url(&self) -> String {
        let cfg = self._config_manager.get_config();
        if let Some(gpu) = &cfg.gpu_config { if let Some(cuda) = &gpu.cuda_version {
            return match cuda { crate::config::CudaVersion::Cuda128 => "https://download.pytorch.org/whl/cu128".into(), crate::config::CudaVersion::Cuda124 => "https://download.pytorch.org/whl/cu124".into(), crate::config::CudaVersion::Cuda118 => "https://download.pytorch.org/whl/cu118".into() };
        }}
        "https://download.pytorch.org/whl/cpu".into()
    }

    fn apply_onnx_gpu_detection(&self, base: &str) -> String {
        let cfg = self._config_manager.get_config();
        let up = cfg.gpu_config.as_ref().map(|g| g.name.to_uppercase()).unwrap_or_default();
        if base.starts_with("onnxruntime") {
            if up.contains("NVIDIA") { return base.replace("onnxruntime", "onnxruntime-gpu"); }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) { return base.replace("onnxruntime", "onnxruntime-directml"); }
        }
        base.into()
    }

    fn generate_startup_script(&self, repo_path: &Path, repo_info: &RepositoryInfo) -> Result<bool> {
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();
        let mut main_file = repo_info.main_file.clone();
        if main_file.is_none() { main_file = self.main_file_finder.find_main_file(&repo_name, repo_path, repo_info.url.as_deref()); }
        let main_file = match main_file { Some(m) => m, None => { warn!("Could not determine main file for repository"); return Ok(false); } };

        let cfg = self._config_manager.get_config();
        if cfg.install_path.as_os_str().is_empty() { return Err(PortableSourceError::installation("Install path not configured")); }
        let install_path = &cfg.install_path;

        let bat_file = repo_path.join(format!("start_{}.bat", repo_name));
        let program_args = repo_info.program_args.clone().unwrap_or_default();

        // CUDA PATH section if configured
        let cuda_section = if let Some(gpu) = &cfg.gpu_config { if let Some(_paths) = &gpu.cuda_paths {
            format!(
                "set cuda_bin=%env_path%\\CUDA\\bin\nset cuda_lib=%env_path%\\CUDA\\lib\nset cuda_lib_64=%env_path%\\CUDA\\lib\\x64\nset cuda_nvml_bin=%env_path%\\CUDA\\nvml\\bin\nset cuda_nvml_lib=%env_path%\\CUDA\\nvml\\lib\nset cuda_nvvm_bin=%env_path%\\CUDA\\nvvm\\bin\nset cuda_nvvm_lib=%env_path%\\CUDA\\nvvm\\lib\n\nset PATH=%cuda_bin%;%PATH%\nset PATH=%cuda_lib%;%PATH%\nset PATH=%cuda_lib_64%;%PATH%\nset PATH=%cuda_nvml_bin%;%PATH%\nset PATH=%cuda_nvml_lib%;%PATH%\nset PATH=%cuda_nvvm_bin%;%PATH%\nset PATH=%cuda_nvvm_lib%;%PATH%\n"
            )
        } else { "REM No CUDA paths configured".into() } } else { "REM No CUDA paths configured".into() };

        let content = format!("@echo off\n").to_string() + &format!(
            "echo Launch {}...\n\nsubst X: {}\nX:\n\nset env_path=X:\\ps_env\nset envs_path=X:\\envs\nset repos_path=X:\\repos\nset ffmpeg_path=%env_path%\\ffmpeg\nset python_path=%envs_path%\\{}\nset python_exe=%python_path%\\python.exe\nset repo_path=%repos_path%\\{}\n\nset tmp_path=X:\\tmp\nset USERPROFILE=%tmp_path%\nset TEMP=%tmp_path%\\Temp\nset TMP=%tmp_path%\\Temp\nset APPDATA=%tmp_path%\\AppData\\Roaming\nset LOCALAPPDATA=%tmp_path%\\AppData\\Local\nset HF_HOME=%tmp_path%\\huggingface\nset XDG_CACHE_HOME=%tmp_path%\nset HF_DATASETS_CACHE=%HF_HOME%\\datasets\n\nset PYTHONIOENCODING=utf-8\nset PYTHONUNBUFFERED=1\nset PYTHONDONTWRITEBYTECODE=1\n\nREM === CUDA PATHS ===\n{}\nset PATH=%python_path%;%PATH%\nset PATH=%python_path%\\Scripts;%PATH%\nset PATH=%ffmpeg_path%;%PATH%\n\ncd /d \"%repo_path%\"\n\"%python_exe%\" {} {}\nset EXIT_CODE=%ERRORLEVEL%\n\necho Cleaning up...\nsubst X: /D\n\nif %EXIT_CODE% neq 0 (\n    echo.\n    echo Program finished with error (code: %EXIT_CODE%)\n) else (\n    echo.\n    echo Program finished successfully\n)\n\npause\n",
            repo_name,
            install_path.display(),
            repo_name,
            repo_name,
            cuda_section,
            main_file,
            program_args,
        );
        let mut f = fs::File::create(&bat_file)?;
        f.write_all(content.as_bytes())?;
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

fn reset_hard_to(git_exe: &str, repo_path: &Path, target: &str) -> Result<()> {
    let mut cmd = std::process::Command::new(git_exe);
    cmd.current_dir(repo_path).arg("reset").arg("--hard").arg(target);
    run_with_progress(cmd, Some(&format!("Reset to {}", target)))
}

// ===== Requirements analysis (Rust port of Python logic) =====

#[derive(Clone, Debug, PartialEq, Eq)]
enum PackageType { Regular, Torch, Onnxruntime, Insightface, Triton }

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
        let mut line = line_in.split('#').next().unwrap_or("").trim().to_string();
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
        let cfg = self.config_manager.get_config();
        if let Some(gpu) = &cfg.gpu_config { if let Some(cuda) = &gpu.cuda_version {
            return match cuda { crate::config::CudaVersion::Cuda128 => "https://download.pytorch.org/whl/cu128".into(), crate::config::CudaVersion::Cuda124 => "https://download.pytorch.org/whl/cu124".into(), crate::config::CudaVersion::Cuda118 => "https://download.pytorch.org/whl/cu118".into() };
        }}
        "https://download.pytorch.org/whl/cpu".into()
    }

    fn get_onnx_package_name(&self) -> String {
        let cfg = self.config_manager.get_config();
        if let Some(gpu) = &cfg.gpu_config {
            let up = gpu.name.to_uppercase();
            if up.contains("NVIDIA") { return "onnxruntime-gpu".into(); }
            if (up.contains("AMD") || up.contains("INTEL")) && cfg!(windows) { return "onnxruntime-directml".into(); }
        }
        "onnxruntime".into()
    }
}

impl RepositoryInstaller {
    fn handle_insightface_package(&self, repo_name: &str) -> Result<()> {
        // Windows prebuilt wheel; fallback to pip package
        if cfg!(windows) {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), "-U".into(), "https://huggingface.co/hanamizuki-ai/pypi-wheels/resolve/main/insightface/insightface-0.7.3-cp311-cp311-win_amd64.whl".into()]);
            run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing insightface wheel"))
        } else {
            let mut pip_cmd = self.get_pip_executable(repo_name);
            pip_cmd.extend(["install".into(), "-U".into(), "insightface".into()]);
            run_tool_with_env(&self._env_manager, &pip_cmd, Some("Installing insightface"))
        }
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

fn run_with_progress(mut cmd: Command, label: Option<&str>) -> Result<()> {
    if let Some(l) = label { info!("{}...", l); }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = cmd.spawn().map_err(|e| PortableSourceError::command(e.to_string()))?;
    if let Some(out) = child.stdout.take() {
        let reader = BufReader::new(out);
        for line in reader.lines().flatten() { debug!("{}", line); }
    }
    let status = child.wait().map_err(|e| PortableSourceError::command(e.to_string()))?;
    if !status.success() { return Err(PortableSourceError::command(format!("Command failed with status: {}", status))); }
    Ok(())
}

fn run_tool_with_env(env_manager: &PortableEnvironmentManager, args: &Vec<String>, label: Option<&str>) -> Result<()> {
    if args.is_empty() { return Ok(()); }
    let mut cmd = Command::new(&args[0]);
    for a in &args[1..] { cmd.arg(a); }
    let envs = env_manager.setup_environment_for_subprocess();
    cmd.envs(envs).stdout(Stdio::piped()).stderr(Stdio::piped());
    run_with_progress(cmd, label)
}

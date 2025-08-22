//! Repository installer for PortableSource - Modular Version
//! 
//! This module handles installation, updating, and management of repositories
//! using a modular architecture with specialized components for different tasks.

use crate::{Result, PortableSourceError};
use crate::config::{ConfigManager, SERVER_DOMAIN};
use crate::envs_manager::PortableEnvironmentManager;
use crate::installer::{
    CommandRunner, GitManager, PipManager, DependencyInstaller, 
    ScriptGenerator, RepositoryInfo as GitRepositoryInfo,
    ScriptRepositoryInfo, ServerClient, MainFileFinder
};
use log::info;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use url::Url;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct FallbackRepo {
    pub url: Option<String>,
    pub main_file: Option<String>,
    pub program_args: Option<String>,
}

/// Main repository installer using modular components
pub struct RepositoryInstaller {
    install_path: PathBuf,
    config_manager: ConfigManager,
    env_manager: PortableEnvironmentManager,
    server_client: ServerClient,
    main_file_finder: MainFileFinder,
    fallback_repositories: HashMap<String, FallbackRepo>,
}

impl RepositoryInstaller {
    pub fn new(install_path: PathBuf, mut config_manager: ConfigManager) -> Self {
        let env_manager = PortableEnvironmentManager::with_config(install_path.clone(), config_manager.clone());
        let server_client = ServerClient::new(format!("https://{}", SERVER_DOMAIN));
        let main_file_finder = MainFileFinder::new(server_client.clone());
        let fallback_repositories = default_fallback_repositories();
        
        // Anchor config to install dir
        config_manager.get_config_mut().install_path = install_path.clone();
        config_manager.set_config_path_to_install_dir();
        
        Self {
            install_path,
            config_manager,
            env_manager,
            server_client,
            main_file_finder,
            fallback_repositories,
        }
    }
    
    /// Install a repository from URL or name
    pub async fn install_repository(&mut self, repo_url_or_name: &str) -> Result<()> {
        info!("Installing repository: {}", repo_url_or_name);
        println!("[PortableSource] Installing repository: {}", repo_url_or_name);
        
        if self.is_repository_url(repo_url_or_name) {
            self.install_from_url(repo_url_or_name).await
        } else {
            self.install_from_name(repo_url_or_name).await
        }
    }
    
    /// Update an existing repository
    pub async fn update_repository(&mut self, repo_name: &str) -> Result<()> {
        info!("Updating repository: {}", repo_name);

        let repo_path = self.install_path.join("repos").join(repo_name);

        if !repo_path.exists() {
            return Err(PortableSourceError::repository(
                format!("Repository '{}' not found", repo_name)
            ));
        }

        // Create modular components for this operation
        let command_runner = CommandRunner::new(&self.env_manager);
        let git_manager = GitManager::new(&command_runner, &self.env_manager);

        // Use GitManager for update operations
        git_manager.update_repository(&repo_path)?;

        // Create components for dependency installation
        let pip_manager = PipManager::new(&command_runner, &self.env_manager, &self.config_manager);
        let dependency_installer = DependencyInstaller::new(
            &command_runner,
            &git_manager,
            &pip_manager,
            &self.env_manager,
            &self.config_manager,
            &self.server_client,
            self.install_path.clone(),
        );

        // Reinstall dependencies using DependencyInstaller
        dependency_installer.install_dependencies(&repo_path).await?;

        Ok(())
    }
    
    /// Delete a repository
    pub fn delete_repository(&self, repo_name: &str) -> Result<()> {
        info!("Deleting repository: {}", repo_name);
        
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
        
        info!("Repository '{}' deleted successfully", repo_name);
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
    
    // Private helper methods
    
    async fn install_from_url(&mut self, repo_url: &str) -> Result<()> {
        info!("Installing from URL: {}", repo_url);
        // Parse URL to get repository name
        let url = Url::parse(repo_url)
            .map_err(|e| PortableSourceError::repository(format!("Invalid repository URL: {}", e)))?;
        let repo_name = self.extract_repo_name_from_url(&url)?;
        let repo_path = self.install_path.join("repos").join(&repo_name);

        // Create modular components for this operation
        let command_runner = CommandRunner::new(&self.env_manager);
        let git_manager = GitManager::new(&command_runner, &self.env_manager);
        let pip_manager = PipManager::new(&command_runner, &self.env_manager, &self.config_manager);
        
        // Clone or update using GitManager
        let repo_info = GitRepositoryInfo { 
            url: Some(repo_url.to_string()), 
            main_file: None, 
            program_args: None 
        };
        git_manager.clone_or_update_repository(&repo_info, &repo_path).await?;

        // Create URL marker and link.txt (source)
        let _ = self.create_url_marker(&repo_path, &repo_name, repo_url);
        let _ = self.write_link_file(&repo_path, repo_url);

        // Install dependencies using DependencyInstaller
        let dependency_installer = DependencyInstaller::new(
            &command_runner,
            &git_manager,
            &pip_manager,
            &self.env_manager,
            &self.config_manager,
            &self.server_client,
            self.install_path.clone(),
        );
        dependency_installer.install_dependencies(&repo_path).await?;

        // Generate startup script using ScriptGenerator
        let script_generator = ScriptGenerator::new(
            &pip_manager,
            &self.config_manager,
            &self.main_file_finder,
            self.install_path.clone(),
        );
        let script_repo_info = ScriptRepositoryInfo {
            url: Some(repo_url.to_string()),
            main_file: None,
            program_args: None,
        };
        script_generator.generate_startup_script(&repo_path, &script_repo_info)?;

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
        
        // Create modular components for this operation
        let command_runner = CommandRunner::new(&self.env_manager);
        let git_manager = GitManager::new(&command_runner, &self.env_manager);
        let pip_manager = PipManager::new(&command_runner, &self.env_manager, &self.config_manager);
        
        // Convert to GitRepositoryInfo
        let git_repo_info = GitRepositoryInfo {
            url: repo_info.url.clone(),
            main_file: repo_info.main_file.clone(),
            program_args: repo_info.program_args.clone(),
        };
        git_manager.clone_or_update_repository(&git_repo_info, &repo_path).await?;

        println!("[PortableSource] Installing dependencies...");
        let dependency_installer = DependencyInstaller::new(
            &command_runner,
            &git_manager,
            &pip_manager,
            &self.env_manager,
            &self.config_manager,
            &self.server_client,
            self.install_path.clone(),
        );
        dependency_installer.install_dependencies(&repo_path).await?;

        // Generate startup script using ScriptGenerator
        let script_generator = ScriptGenerator::new(
            &pip_manager,
            &self.config_manager,
            &self.main_file_finder,
            self.install_path.clone(),
        );
        let script_repo_info = ScriptRepositoryInfo {
            url: repo_info.url.clone(),
            main_file: repo_info.main_file.clone(),
            program_args: repo_info.program_args.clone(),
        };
        script_generator.generate_startup_script(&repo_path, &script_repo_info)?;

        let _ = self.server_client.send_download_stats(&name);

        // Special setup hooks
        self.apply_special_setup(&name, &repo_path)?;
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

    fn get_repository_info(&self, repo_name: &str) -> Result<Option<FallbackRepo>> {
        // Try server first
        if let Ok(Some(server_repo)) = self.server_client.get_repository_info(repo_name) {
            return Ok(Some(FallbackRepo {
                url: server_repo.url,
                main_file: server_repo.main_file,
                program_args: server_repo.program_args,
            }));
        }
        
        // Fallback to local list
        Ok(self.fallback_repositories.get(repo_name).cloned())
    }

    fn normalize_repo_name(&self, input_name: &str, repo_info: &FallbackRepo) -> Result<String> {
        if let Some(ref url) = repo_info.url {
            if let Ok(parsed_url) = Url::parse(url) {
                return self.extract_repo_name_from_url(&parsed_url);
            }
        }
        Ok(input_name.to_string())
    }

    fn create_url_marker(&self, repo_path: &Path, repo_name: &str, repo_url: &str) -> Result<()> {
        let marker_file = repo_path.join(".portablesource_url");
        fs::write(&marker_file, format!("{}={}", repo_name, repo_url))?;
        Ok(())
    }

    fn write_link_file(&self, repo_path: &Path, repo_url: &str) -> Result<()> {
        let link_file = repo_path.join("link.txt");
        fs::write(&link_file, repo_url)?;
        Ok(())
    }

    fn apply_special_setup(&self, repo_name: &str, repo_path: &Path) -> Result<()> {
        // Special setup hooks for specific repositories
        let repo_name_lower = repo_name.to_lowercase();
        
        if repo_name_lower.contains("stable-diffusion") {
            info!("Applying Stable Diffusion specific setup");
            // Create models directory structure
            let models_dir = repo_path.join("models");
            let _ = fs::create_dir_all(&models_dir.join("Stable-diffusion"));
            let _ = fs::create_dir_all(&models_dir.join("VAE"));
            let _ = fs::create_dir_all(&models_dir.join("Lora"));
        }
        
        if repo_name_lower.contains("comfyui") {
            info!("Applying ComfyUI specific setup");
            let _ = fs::create_dir_all(&repo_path.join("models").join("checkpoints"));
            let _ = fs::create_dir_all(&repo_path.join("custom_nodes"));
            let _ = fs::create_dir_all(&repo_path.join("output"));
        }
        
        Ok(())
    }
}

fn default_fallback_repositories() -> HashMap<String, FallbackRepo> {
    let mut repos = HashMap::new();
    
    repos.insert("stable-diffusion-webui".to_string(), FallbackRepo {
        url: Some("https://github.com/AUTOMATIC1111/stable-diffusion-webui.git".to_string()),
        main_file: Some("webui.py".to_string()),
        program_args: None,
    });
    
    repos.insert("comfyui".to_string(), FallbackRepo {
        url: Some("https://github.com/comfyanonymous/ComfyUI.git".to_string()),
        main_file: Some("main.py".to_string()),
        program_args: None,
    });
    
    repos
}
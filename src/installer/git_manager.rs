//! Git manager for handling repository operations.

use crate::installer::command_runer::CommandRunner;
use crate::envs_manager::PortableEnvironmentManager;
use crate::PortableSourceError;
use crate::Result;
use std::fs;
use std::path::Path;
use log::{info, warn};

/// Repository information struct for git operations
pub struct RepositoryInfo {
    pub url: Option<String>,
    pub main_file: Option<String>,
    pub program_args: Option<String>,
}

pub struct GitManager<'a> {
    command_runner: &'a CommandRunner<'a>,
    env_manager: &'a PortableEnvironmentManager,
}

impl<'a> GitManager<'a> {
    pub fn new(command_runner: &'a CommandRunner, env_manager: &'a PortableEnvironmentManager) -> Self {
        Self { command_runner, env_manager }
    }

    fn get_git_executable(&self) -> String {
        if let Some(p) = self.env_manager.get_git_executable() { return p.to_string_lossy().to_string(); }
        "git".into()
    }

    /// Clone or update repository using RepositoryInfo struct (main interface)
    pub async fn clone_or_update_repository(&self, repo_info: &RepositoryInfo, repo_path: &Path) -> Result<()> {
        let repo_url = repo_info.url.as_ref().ok_or_else(|| PortableSourceError::repository("Missing repository URL"))?;
        self.clone_or_update_repository_from_url(repo_url, repo_path).await
    }

    /// Clone or update repository from URL (helper method)
    pub async fn clone_or_update_repository_from_url(&self, repo_url: &str, repo_path: &Path) -> Result<()> {
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
        info!("Cloning repository from URL: {}", repo_url);
        
        let parent = repo_path.parent().ok_or_else(|| PortableSourceError::repository("Invalid repo path"))?;
        fs::create_dir_all(parent)?;
        let mut args = vec![git_exe.clone(), "clone".to_string()];
        if let Some(branch) = None::<String> { 
            args.push("-b".to_string());
            args.push(branch);
        }
        args.push(repo_url.to_string());
        args.push(repo_path.file_name().unwrap().to_string_lossy().to_string());
        
        match self.command_runner.run(&args, Some("Cloning repository"), Some(parent)) {
            Ok(_) => {
                info!("Repository cloned successfully to: {:?}", repo_path);
                println!("[PortableSource] Repository cloned successfully");
                Ok(())
            }
            Err(e) => {
                eprintln!("Failed to clone repository from {}: {}", repo_url, e);
                println!("[PortableSource] Failed to clone repository: {}", e);
                Err(e)
            }
        }
    }

    fn update_repository_with_fixes(&self, git_exe: &str, repo_path: &Path) -> Result<()> {
        let max_attempts = 3;
        for attempt in 0..max_attempts {
            let args = vec![git_exe.to_string(), "pull".to_string()];
            match self.command_runner.run(&args, Some("Updating repository"), Some(repo_path)) {
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
            let _ = self.command_runner.run(&args, None, Some(repo_path));
        }
        Ok(())
    }

    pub fn update_repository(&self, repo_path: &Path) -> Result<()> {
        let git_exe = self.get_git_executable();
        {
            let args = vec![git_exe.clone(), "fetch".to_string(), "--all".to_string()];
            if let Err(e) = self.command_runner.run(&args, Some("Fetching from remote"), Some(repo_path)) {
                warn!("Failed to fetch from remote: {}", e);
            }
        }
        {
            let args = vec![git_exe.clone(), "reset".to_string(), "--hard".to_string(), "origin/main".to_string()];
            if self.command_runner.run(&args, Some("Reset to origin/main"), Some(repo_path)).is_err() {
                let args = vec![git_exe.clone(), "reset".to_string(), "--hard".to_string(), "origin/master".to_string()];
                let _ = self.command_runner.run(&args, Some("Reset to origin/master"), Some(repo_path));
            }
        }
        {
            let args = vec![git_exe.clone(), "pull".to_string()];
            if let Err(e) = self.command_runner.run(&args, Some("Pulling latest changes"), Some(repo_path)) {
                warn!("Failed to pull latest changes: {}", e);
            }
        }
        Ok(())
    }
}

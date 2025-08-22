//! Dependency installer module for managing Python environments and package installations.

use crate::installer::{CommandRunner, GitManager, PipManager, ServerClient};
use crate::envs_manager::PortableEnvironmentManager;
use crate::config::ConfigManager;
use crate::PortableSourceError;
use crate::Result;
use log::{info, warn};
use std::path::{Path, PathBuf};
use std::fs;
use serde_json::Value as JsonValue;

pub struct DependencyInstaller<'a> {
    command_runner: &'a CommandRunner<'a>,
    git_manager: &'a GitManager<'a>,
    pip_manager: &'a PipManager<'a>,
    env_manager: &'a PortableEnvironmentManager,
    config_manager: &'a ConfigManager,
    server_client: &'a ServerClient,
    install_path: PathBuf,
}

impl<'a> DependencyInstaller<'a> {
    pub fn new(
        command_runner: &'a CommandRunner,
        git_manager: &'a GitManager,
        pip_manager: &'a PipManager,
        env_manager: &'a PortableEnvironmentManager,
        config_manager: &'a ConfigManager,
        server_client: &'a ServerClient,
        install_path: PathBuf,
    ) -> Self {
        Self {
            command_runner,
            git_manager,
            pip_manager,
            env_manager,
            config_manager,
            server_client,
            install_path,
        }
    }

    /// Main entry point for installing dependencies for a repository
    pub async fn install_dependencies(&self, repo_path: &Path) -> Result<()> {
        info!("Installing dependencies for: {:?}", repo_path);
        let repo_name = repo_path.file_name().and_then(|s| s.to_str()).unwrap_or("").to_lowercase();

        // Ensure project environment exists (Windows: copy portable python; Linux: create venv)
        self.create_venv_environment(&repo_name)?;

        // Try server installation plan first
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
            if let Ok(requirements_path) = self.pip_manager.extract_dependencies_from_pyproject(&pyproject_path, repo_path) {
                info!("Installing from extracted pyproject.toml dependencies: {:?}", requirements_path);
                self.pip_manager.install_requirements_with_uv_or_pip(&repo_name, &requirements_path, Some(repo_path))?;
                
                // Install the repository itself as a package
                info!("Installing repository as package with uv pip install .");
                self.pip_manager.install_repo_as_package(&repo_name, repo_path)?;
                
                return Ok(());
            } else {
                warn!("Failed to extract dependencies from pyproject.toml, falling back to requirements.txt");
            }
        }

        // Fallback to requirements.txt variants using smart search
        if let Some(requirements_file) = self.pip_manager.find_requirements_files(repo_path) {
            info!("Installing from {:?}", requirements_file);
            self.pip_manager.install_requirements_with_uv_or_pip(&repo_name, &requirements_file, Some(repo_path))?;
        } else {
            info!("No requirements.txt or pyproject.toml found");
        }
        Ok(())
    }

    /// Create virtual environment for the repository
    fn create_venv_environment(&self, repo_name: &str) -> Result<()> {
        let install_path = self.install_path.clone();
        let envs_path = install_path.join("envs");
        let venv_path = envs_path.join(repo_name);
        
        // Remove existing environment if present
        if venv_path.exists() { 
            fs::remove_dir_all(&venv_path)?; 
        }

        if cfg!(windows) {
            // Windows: копируем портативный Python в envs/{repo}
            let ps_env_python = install_path.join("ps_env").join("python");
            if !ps_env_python.exists() { 
                return Err(PortableSourceError::installation(format!("Portable Python not found at: {:?}", ps_env_python))); 
            }
            info!("Creating environment by copying portable Python: {:?} -> {:?}", ps_env_python, venv_path);
            self.copy_dir_recursive(&ps_env_python, &venv_path)?;
            let python_exe = venv_path.join("python.exe");
            if !python_exe.exists() { 
                return Err(PortableSourceError::installation(format!("Python executable not found in {:?}", venv_path))); 
            }
        } else {
            // Linux: в DESK режиме используем python из micromamba-базы, в CLOUD — системный python3
            fs::create_dir_all(&envs_path)?;
            let mamba_py = install_path.join("ps_env").join("mamba_env").join("bin").join("python");
            
            #[cfg(unix)]
            let py_bin = if matches!(crate::utils::detect_linux_mode(), crate::utils::LinuxMode::Desk) && mamba_py.exists() { 
                mamba_py 
            } else { 
                PathBuf::from("python3") 
            };
            
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

    /// Execute server installation plan
    fn execute_server_installation_plan(&self, repo_name: &str, plan: &JsonValue, repo_path: Option<&Path>) -> Result<bool> {
        self.pip_manager.execute_server_installation_plan(repo_name, plan, repo_path)
    }

    /// Helper function to copy directories recursively (for Windows Python environment)
    fn copy_dir_recursive(&self, from: &Path, to: &Path) -> Result<()> {
        fs::create_dir_all(to)?;
        for entry in fs::read_dir(from)? {
            let entry = entry?;
            let ty = entry.file_type()?;
            let src = entry.path();
            let dst = to.join(entry.file_name());
            if ty.is_dir() { 
                self.copy_dir_recursive(&src, &dst)?; 
            } else { 
                fs::copy(&src, &dst)?; 
            }
        }
        Ok(())
    }
}
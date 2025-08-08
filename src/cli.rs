//! Command-line interface for PortableSource

use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "portablesource")]
#[command(about = "PortableSource - Portable AI/ML Environment Manager")]
#[command(version = env!("CARGO_PKG_VERSION"))]
pub struct Cli {
    /// Enable debug logging
    #[arg(long)]
    pub debug: bool,
    
    /// Installation path
    #[arg(long)]
    pub install_path: Option<PathBuf>,
    
    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Subcommand)]
pub enum Commands {
    /// Setup environment (Portable)
    SetupEnv,
    
    /// Register installation path in registry
    SetupReg,
    
    /// Unregister installation path from registry
    Unregister,
    
    /// Change installation path
    ChangePath,
    
    /// Install repository
    InstallRepo {
        /// Repository URL or name
        repo: String,
    },
    
    /// Update repository
    UpdateRepo {
        /// Repository name
        repo: String,
    },
    
    /// Delete repository
    DeleteRepo {
        /// Repository name
        repo: String,
    },
    
    /// Show installed repositories
    ListRepos,
    
    /// Show system information
    SystemInfo,
    
    /// Check environment status and tools
    CheckEnv,
    
    /// Install MSVC Build Tools
    InstallMsvc,
    
    /// Check MSVC Build Tools installation
    CheckMsvc,
    
    /// Show True if gpu nvidia. Else False
    CheckGpu,
    
    /// Show version
    Version,
}

impl Cli {
    /// Parse command line arguments
    pub fn parse_args() -> Self {
        Self::parse()
    }
    
    /// Check if any command was provided
    pub fn has_command(&self) -> bool {
        self.command.is_some()
    }
    
    /// Get the command or return a default help command
    pub fn get_command(&self) -> &Commands {
        self.command.as_ref().unwrap_or(&Commands::SystemInfo)
    }
}
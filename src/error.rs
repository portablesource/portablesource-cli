//! Error handling for PortableSource

use thiserror::Error;

/// Main error type for PortableSource operations
#[derive(Error, Debug)]
pub enum PortableSourceError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    
    #[error("HTTP request error: {0}")]
    Reqwest(#[from] reqwest::Error),
    
    #[error("JSON parsing error: {0}")]
    Json(#[from] serde_json::Error),
    
    #[error("Registry error: {0}")]
    Registry(String),
    
    #[error("URL parsing error: {0}")]
    Url(#[from] url::ParseError),
    
    #[error("Configuration error: {message}")]
    Config { message: String },
    
    #[error("GPU detection error: {message}")]
    GpuDetection { message: String },
    
    #[error("Installation error: {message}")]
    Installation { message: String },
    
    #[error("Repository error: {message}")]
    Repository { message: String },
    
    #[error("Environment error: {message}")]
    Environment { message: String },
    
    #[error("Command execution failed: {message}")]
    Command { message: String },
    
    #[error("Path validation error: {path}")]
    InvalidPath { path: String },
    
    #[error("Missing dependency: {dependency}")]
    MissingDependency { dependency: String },
}

/// Result type alias for PortableSource operations
pub type Result<T> = std::result::Result<T, PortableSourceError>;

impl PortableSourceError {
    pub fn config(message: impl Into<String>) -> Self {
        Self::Config {
            message: message.into(),
        }
    }
    
    pub fn gpu_detection(message: impl Into<String>) -> Self {
        Self::GpuDetection {
            message: message.into(),
        }
    }
    
    pub fn installation(message: impl Into<String>) -> Self {
        Self::Installation {
            message: message.into(),
        }
    }
    
    pub fn repository(message: impl Into<String>) -> Self {
        Self::Repository {
            message: message.into(),
        }
    }
    
    pub fn environment(message: impl Into<String>) -> Self {
        Self::Environment {
            message: message.into(),
        }
    }
    
    pub fn command(message: impl Into<String>) -> Self {
        Self::Command {
            message: message.into(),
        }
    }
    
    pub fn invalid_path(path: impl Into<String>) -> Self {
        Self::InvalidPath {
            path: path.into(),
        }
    }
    
    pub fn missing_dependency(dependency: impl Into<String>) -> Self {
        Self::MissingDependency {
            dependency: dependency.into(),
        }
    }
}
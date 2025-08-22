//! Server client for communicating with PortableSource API server.

use crate::Result;
use log::warn;
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RepositoryInfo {
    pub url: Option<String>,
    pub main_file: Option<String>, 
    pub program_args: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ServerClient {
    server_url: String,
    timeout_secs: u64,
}

impl Default for ServerClient {
    fn default() -> Self {
        Self {
            server_url: String::new(),
            timeout_secs: 10,
        }
    }
}

impl ServerClient {
    pub fn new(server_url: String) -> Self {
        Self {
            server_url: server_url.trim_end_matches('/').to_string(),
            timeout_secs: 10,
        }
    }

    /// Check if server is available for API calls
    #[allow(dead_code)]
    pub fn is_server_available(&self) -> bool {
        let url = format!("{}/api/repositories", self.server_url);
        let timeout = self.timeout_secs;
        
        std::thread::spawn(move || {
            match reqwest::blocking::Client::new()
                .get(&url)
                .timeout(Duration::from_secs(timeout))
                .send() {
                Ok(resp) => resp.status().is_success(),
                Err(_) => false,
            }
        }).join().unwrap_or(false)
    }

    /// Get repository information from server
    pub fn get_repository_info(&self, name: &str) -> Result<Option<RepositoryInfo>> {
        let url = format!("{}/api/repositories/{}", self.server_url, name.to_lowercase());
        let timeout = self.timeout_secs;
        
        let res = std::thread::spawn(move || {
            let resp = reqwest::blocking::Client::new()
                .get(&url)
                .timeout(Duration::from_secs(timeout))
                .send();
            
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        let v: serde_json::Value = r.json().unwrap_or(serde_json::json!({}));
                        
                        if v.get("success").and_then(|b| b.as_bool()).unwrap_or(false) {
                            // New format
                            if let Some(repo) = v.get("repository") {
                                let url = repo.get("repositoryUrl")
                                    .and_then(|s| s.as_str())
                                    .map(|s| s.trim().to_string());
                                let main_file = repo.get("filePath")
                                    .and_then(|s| s.as_str())
                                    .map(|s| s.to_string());
                                let program_args = repo.get("programArgs")
                                    .and_then(|s| s.as_str())
                                    .map(|s| s.to_string());
                                
                                return Ok(Some(RepositoryInfo { url, main_file, program_args }));
                            }
                        } else {
                            // Legacy format
                            let url = v.get("url")
                                .and_then(|s| s.as_str())
                                .map(|s| s.to_string());
                            let main_file = v.get("main_file")
                                .and_then(|s| s.as_str())
                                .map(|s| s.to_string());
                            let program_args = v.get("program_args")
                                .and_then(|s| s.as_str())
                                .map(|s| s.to_string());
                            
                            if url.is_some() || main_file.is_some() {
                                return Ok(Some(RepositoryInfo { url, main_file, program_args }));
                            }
                        }
                        Ok(None)
                    } else if r.status().as_u16() == 404 {
                        Ok(None)
                    } else {
                        Ok(None)
                    }
                }
                Err(_) => Ok(None)
            }
        }).join().unwrap_or(Ok(None));
        
        res
    }

    /// Search for repositories by name (optional enhancement)
    #[allow(dead_code)]
    pub fn search_repositories(&self, _name: &str) -> Vec<serde_json::Value> {
        // Optional enhancement: implement when server supports search endpoint
        Vec::new()
    }

    /// Get installation plan for a repository
    pub fn get_installation_plan(&self, name: &str) -> Result<Option<serde_json::Value>> {
        let url = format!("{}/api/repositories/{}/install-plan", self.server_url, name.to_lowercase());
        let timeout = self.timeout_secs;
        
        std::thread::spawn(move || {
            let resp = reqwest::blocking::Client::new()
                .get(&url)
                .timeout(Duration::from_secs(timeout))
                .send();
                
            match resp {
                Ok(r) => {
                    if r.status().is_success() {
                        let v: serde_json::Value = r.json().unwrap_or(serde_json::json!({}));
                        
                        if v.get("success").and_then(|b| b.as_bool()).unwrap_or(false) {
                            if let Some(plan) = v.get("installation_plan") {
                                return Ok(Some(plan.clone()));
                            }
                        }
                        Ok(None)
                    } else {
                        Ok(None)
                    }
                }
                Err(e) => {
                    warn!("Server error get_installation_plan: {}", e);
                    Ok(None)
                }
            }
        }).join().unwrap_or(Ok(None))
    }

    /// Send download statistics to server (non-fatal)
    pub fn send_download_stats(&self, repo_name: &str) -> Result<()> {
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
                .timeout(Duration::from_secs(timeout))
                .send();
        }).join();
        
        Ok(())
    }
}
//! Main file finder for detecting the main executable file in repositories.

use crate::installer::server_client::ServerClient;
use std::path::Path;
use std::fs;
use url::Url;

#[derive(Clone, Debug, Default)]
pub struct MainFileFinder {
    server_client: ServerClient,
}

impl MainFileFinder {
    pub fn new(server_client: ServerClient) -> Self {
        Self { server_client }
    }

    /// Find the main file for a repository using multiple strategies
    pub fn find_main_file(&self, repo_name: &str, repo_path: &Path, repo_url: Option<&str>) -> Option<String> {
        // 1) Try server first
        if let Ok(Some(info)) = self.server_client.get_repository_info(repo_name) {
            if let Some(main_file) = info.main_file {
                if self.validate_main_file(repo_path, &main_file) {
                    return Some(main_file);
                }
            }
        }
        
        // 2) Try common names
        let common_names = [
            "run.py", "app.py", "webui.py", "main.py", "start.py", 
            "launch.py", "gui.py", "interface.py", "server.py"
        ];
        
        for file_name in common_names {
            if self.validate_main_file(repo_path, file_name) {
                return Some(file_name.to_string());
            }
        }
        
        // 3) Heuristic: any single non-test python file
        let mut candidates: Vec<String> = Vec::new();
        if let Ok(entries) = fs::read_dir(repo_path) {
            for entry in entries.flatten() {
                if let Ok(file_type) = entry.file_type() {
                    if file_type.is_file() {
                        let name = entry.file_name().to_string_lossy().to_string();
                        
                        if name.to_lowercase().ends_with(".py") 
                            && !name.contains("test_") 
                            && name != "setup.py" 
                            && !name.contains("__") 
                            && !name.contains("install") {
                            candidates.push(name);
                        }
                    }
                }
            }
        }
        
        // If only one candidate, use it
        if candidates.len() == 1 {
            return candidates.into_iter().next();
        }
        
        // Look for priority keywords in candidates
        for candidate in &candidates {
            let lower_candidate = candidate.to_lowercase();
            if lower_candidate.contains("main") 
                || lower_candidate.contains("run") 
                || lower_candidate.contains("start") 
                || lower_candidate.contains("app") {
                return Some(candidate.clone());
            }
        }
        
        // 4) Last resort: use repo_url name
        if let Some(url) = repo_url {
            if let Ok(parsed_url) = Url::parse(url) {
                if let Some(name) = parsed_url.path_segments()
                    .and_then(|s| s.last())
                    .map(|s| s.trim_end_matches(".git")) {
                    let candidate = format!("{}.py", name);
                    if self.validate_main_file(repo_path, &candidate) {
                        return Some(candidate);
                    }
                }
            }
        }
        
        None
    }

    /// Validate that a main file exists in the repository
    fn validate_main_file(&self, repo_path: &Path, main_file: &str) -> bool {
        repo_path.join(main_file).exists()
    }
}
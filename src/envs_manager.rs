//! Environment manager for PortableSource
//! 
//! This module handles downloading and managing portable tools
//! like Python, Git, FFMPEG, and CUDA.

use crate::{Result, PortableSourceError};
use crate::config::{ConfigManager, ToolLinks};
use url::Url;
use std::fs::{self, OpenOptions};
use std::io::{self, Seek, SeekFrom};
use std::path::Path;
use std::process::{Command, Stdio};
use crate::gpu::GpuDetector;
use std::collections::HashMap;
use std::path::{PathBuf};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Clone, Debug)]
struct PortableToolSpec {
    name: String,
    url: String,
    extract_path: String,
    executable_path: String,
}

pub struct PortableEnvironmentManager {
    install_path: PathBuf,
    ps_env_path: PathBuf,
    config_manager: ConfigManager,
    gpu_detector: GpuDetector,
    tool_specs: HashMap<String, PortableToolSpec>,
}

impl PortableEnvironmentManager {
    pub fn new(install_path: PathBuf) -> Self {
        let ps_env_path = install_path.join("ps_env");
        let config_manager = ConfigManager::new(None).expect("ConfigManager init failed");
        let tool_specs = Self::build_tool_specs();
        Self {
            install_path,
            ps_env_path,
            config_manager,
            gpu_detector: GpuDetector::new(),
            tool_specs,
        }
    }

    fn build_tool_specs() -> HashMap<String, PortableToolSpec> {
        let mut map = HashMap::new();
        let is_windows = cfg!(windows);
        map.insert(
            "ffmpeg".to_string(),
            PortableToolSpec {
                name: "ffmpeg".to_string(),
                url: ToolLinks::Ffmpeg.url().to_string(),
                extract_path: "ffmpeg".to_string(),
                executable_path: if is_windows { "ffmpeg/ffmpeg.exe" } else { "ffmpeg/ffmpeg" }.to_string(),
            },
        );
        map.insert(
            "git".to_string(),
            PortableToolSpec {
                name: "git".to_string(),
                url: ToolLinks::Git.url().to_string(),
                extract_path: "git".to_string(),
                executable_path: if is_windows { "git/cmd/git.exe" } else { "git/bin/git" }.to_string(),
            },
        );
        map.insert(
            "python".to_string(),
            PortableToolSpec {
                name: "python".to_string(),
                url: ToolLinks::Python311.url().to_string(),
                extract_path: "python".to_string(),
                executable_path: if is_windows { "python/python.exe" } else { "python/bin/python" }.to_string(),
            },
        );
        map
    }

    // --- Downloads ---
    fn download_with_resume(&self, url: &str, destination: &Path) -> Result<()> {
        use reqwest::blocking::Client;
        use reqwest::header::{RANGE};

        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(600))
            .build()?;

        let mut existing_len: u64 = 0;
        if destination.exists() {
            existing_len = destination.metadata()?.len();
        } else if let Some(parent) = destination.parent() { fs::create_dir_all(parent)?; }

        // Try ranged request if we have partial file
        let mut resp = if existing_len > 0 {
            client.get(url).header(RANGE, format!("bytes={}-", existing_len)).send()?
        } else {
            client.get(url).send()?
        };

        if !resp.status().is_success() {
            // If ranged not supported, retry from start
            if existing_len > 0 {
                resp = client.get(url).send()?;
                if !resp.status().is_success() {
                    return Err(PortableSourceError::environment(format!(
                        "Download failed: HTTP {}", resp.status()
                    )));
                }
                // truncate file
                let _ = fs::remove_file(destination);
                let mut f = OpenOptions::new().create(true).write(true).open(destination)?;
                io::copy(&mut resp, &mut f)?;
                return Ok(());
            } else {
                return Err(PortableSourceError::environment(format!(
                    "Download failed: HTTP {}", resp.status()
                )));
            }
        }

        // Write response to file (append or create)
        let mut file = if destination.exists() && existing_len > 0 {
            let mut f = OpenOptions::new().read(true).write(true).open(destination)?;
            f.seek(SeekFrom::End(0))?;
            f
        } else {
            OpenOptions::new().create(true).write(true).open(destination)?
        };
        io::copy(&mut resp, &mut file)?;
        Ok(())
    }

    // Static helpers for parallel tasks
    fn download_with_resume_static(url: String, destination: PathBuf) -> Result<()> {
        use reqwest::blocking::Client;
        use reqwest::header::RANGE;
        let client = Client::builder().timeout(std::time::Duration::from_secs(600)).build()?;
        if let Some(parent) = destination.parent() { fs::create_dir_all(parent)?; }
        let existing_len: u64 = if destination.exists() { destination.metadata()?.len() } else { 0 };
        let mut resp = if existing_len > 0 {
            client.get(&url).header(RANGE, format!("bytes={}-", existing_len)).send()?
        } else { client.get(&url).send()? };
        if !resp.status().is_success() {
            if existing_len > 0 { resp = client.get(&url).send()?; }
            if !resp.status().is_success() {
                return Err(PortableSourceError::environment(format!("Download failed: HTTP {}", resp.status())));
            }
            let _ = fs::remove_file(&destination);
            let mut f = OpenOptions::new().create(true).write(true).open(&destination)?;
            std::io::copy(&mut resp, &mut f)?;
            return Ok(());
        }
        let mut file = if destination.exists() && existing_len > 0 {
            let mut f = OpenOptions::new().read(true).write(true).open(&destination)?;
            use std::io::Seek; use std::io::SeekFrom;
            f.seek(SeekFrom::End(0))?; f
        } else { OpenOptions::new().create(true).write(true).open(&destination)? };
        std::io::copy(&mut resp, &mut file)?;
        Ok(())
    }

    // --- Extraction (only via bundled 7z.exe) ---
    fn extract_7z(&self, archive_path: &Path, extract_to: &Path) -> Result<()> {
        if let Some(parent) = extract_to.parent() { fs::create_dir_all(parent)?; }
        fs::create_dir_all(extract_to)?;
        self.extract_with_7z_binary(archive_path, extract_to)
    }
    fn extract_7z_static(archive_path: PathBuf, extract_to: PathBuf) -> Result<()> {
        if let Some(parent) = extract_to.parent() { fs::create_dir_all(parent)?; }
        fs::create_dir_all(&extract_to)?;
        Self::extract_with_7z_binary_static(&archive_path, &extract_to)
    }

    fn ensure_7z_binary(&self) -> Result<PathBuf> {
        let seven_zip_path = self.ps_env_path.join("7z.exe");
        if seven_zip_path.exists() { return Ok(seven_zip_path); }
        // Download 7z.exe to ps_env
        let url = crate::config::ToolLinks::SevenZip.url();
        self.download_with_resume(url, &seven_zip_path)?;
        Ok(seven_zip_path)
    }

    fn extract_with_7z_binary(&self, archive_path: &Path, extract_to: &Path) -> Result<()> {
        let seven_zip = self.ensure_7z_binary()?;
        // 7z.exe prefers order: x <archive> -y -o<dir>
        let status = Command::new(&seven_zip)
            .arg("x")
            .arg(archive_path.to_string_lossy().to_string())
            .arg("-y")
            .arg(format_7z_out_arg(extract_to))
            .stdout(Stdio::null()).stderr(Stdio::null())
            .status()?;
        if status.success() { Ok(()) } else { Err(PortableSourceError::environment("7z.exe extraction failed")) }
    }

    fn extract_with_7z_binary_static(archive_path: &Path, extract_to: &Path) -> Result<()> {
        // Всегда храним 7z.exe в корне ps_env (родитель архива)
        let ps_env = archive_path.parent().unwrap_or_else(|| Path::new(".")).to_path_buf();
        let seven_zip_path = ps_env.join("7z.exe");
        if !seven_zip_path.exists() {
            let url = crate::config::ToolLinks::SevenZip.url();
            Self::download_with_resume_static(url.to_string(), seven_zip_path.clone())?;
        }
        // 7z.exe prefers order: x <archive> -y -o<dir>
        let status = Command::new(&seven_zip_path)
            .arg("x")
            .arg(archive_path.to_string_lossy().to_string())
            .arg("-y")
            .arg(format_7z_out_arg(extract_to))
            .stdout(Stdio::null()).stderr(Stdio::null())
            .status()?;
        if status.success() { Ok(()) } else { Err(PortableSourceError::environment("7z.exe extraction failed")) }
    }
    
    fn install_portable_tool(&self, key: &str) -> Result<()> {
        let spec = self.tool_specs.get(key).ok_or_else(|| PortableSourceError::environment(format!("Unknown tool: {}", key)))?;
        let exe_path = self.ps_env_path.join(&spec.executable_path);
        if exe_path.exists() { return Ok(()); }

        // Determine archive filename from URL
        let archive_name = Url::parse(&spec.url)
            .ok()
            .and_then(|u| u.path_segments().and_then(|mut s| s.next_back()).map(|s| s.to_string()))
            .unwrap_or_else(|| format!("{}.7z", spec.name));
        let archive_path = self.ps_env_path.join(&archive_name);

        self.download_with_resume(&spec.url, &archive_path)?;
        // Extract to ps_env root; archives are structured with top-level folder (ffmpeg/git/python)
        self.extract_7z(&archive_path, &self.ps_env_path)?;
        let _ = fs::remove_file(&archive_path);

        if !exe_path.exists() {
            return Err(PortableSourceError::environment(format!(
                "{} installation failed: executable not found at {:?}",
                spec.name, exe_path
            )));
        }
        Ok(())
    }

    // --- Env for subprocess ---
    pub fn setup_environment_for_subprocess(&self) -> HashMap<String, String> {
        let mut env_vars: HashMap<String, String> = std::env::vars().collect();
        if !self.ps_env_path.exists() { return env_vars; }

        let mut tool_paths: Vec<String> = Vec::new();
        for (_name, spec) in &self.tool_specs {
            let exe_dir = self.ps_env_path.join(&spec.executable_path).parent().map(|p| p.to_path_buf());
            if let Some(exe_dir) = exe_dir { if exe_dir.exists() { tool_paths.push(exe_dir.to_string_lossy().to_string()); } }
        }

        // CUDA PATH vars
        if let Some(gpu) = &self.config_manager.get_config().gpu_config {
            if let Some(paths) = &gpu.cuda_paths {
                let base = &paths.base_path;
                let bin = &paths.cuda_bin;
                let lib = &paths.cuda_lib;
                let lib64 = &paths.cuda_lib_64;
                if Path::new(&bin).exists() { tool_paths.push(bin.to_string_lossy().to_string()); }
                if Path::new(&lib64).exists() { tool_paths.push(lib64.to_string_lossy().to_string()); }
                else if Path::new(&lib).exists() { tool_paths.push(lib.to_string_lossy().to_string()); }
                env_vars.insert("CUDA_PATH".to_string(), base.to_string_lossy().to_string());
                env_vars.insert("CUDA_HOME".to_string(), base.to_string_lossy().to_string());
                env_vars.insert("CUDA_ROOT".to_string(), base.to_string_lossy().to_string());
                env_vars.insert("CUDA_BIN_PATH".to_string(), bin.to_string_lossy().to_string());
                env_vars.insert(
                    "CUDA_LIB_PATH".to_string(),
                    if Path::new(&lib64).exists() { lib64.to_string_lossy().to_string() } else { lib.to_string_lossy().to_string() }
                );
            }
        }

        if !tool_paths.is_empty() {
            let sep = if cfg!(windows) { ";" } else { ":" };
            let current = env_vars.get("PATH").cloned().unwrap_or_default();
            env_vars.insert("PATH".to_string(), format!("{}{}{}", tool_paths.join(sep), sep, current));
        }
        env_vars
    }

    fn run_in_activated_environment(&self, command: &[String], cwd: Option<&Path>) -> io::Result<std::process::Output> {
        let envs = self.setup_environment_for_subprocess();
        if cfg!(windows) {
            let joined = command.iter().map(|a| if a.contains(' ') { format!("\"{}\"", a) } else { a.clone() }).collect::<Vec<_>>().join(" ");
            let mut cmd = Command::new("cmd");
            cmd.arg("/C").arg(joined);
            if let Some(dir) = cwd { cmd.current_dir(dir); }
            cmd.envs(&envs).stdout(Stdio::piped()).stderr(Stdio::piped()).output()
        } else {
            let mut cmd = Command::new(&command[0]);
            cmd.args(&command[1..]);
            if let Some(dir) = cwd { cmd.current_dir(dir); }
            cmd.envs(&envs).stdout(Stdio::piped()).stderr(Stdio::piped()).output()
        }
    }

    fn extract_version_from_output(&self, tool_name: &str, output: &str) -> String {
        let out = output.trim();
        if out.is_empty() { return "Unknown version".to_string(); }
        let lines: Vec<&str> = out.lines().collect();
        if tool_name == "nvcc" {
            for line in &lines { if line.contains("nvcc:") || line.contains("Cuda compilation tools") { return line.trim().to_string(); } }
            for line in lines.iter().rev() {
                let l = line.trim();
                if !l.is_empty() && !l.starts_with("C:\\") && !l.contains("SET") && !l.contains("set") { return l.to_string(); }
            }
        }
        let patterns: HashMap<&str, [&str; 1]> = HashMap::from([
            ("python", ["Python "]),
            ("git", ["git version"]),
            ("ffmpeg", ["ffmpeg version"]),
        ]);
        if let Some(pats) = patterns.get(tool_name) {
            for line in &lines { for p in pats { if line.contains(p) { return line.trim().to_string(); } } }
        }
        for line in &lines {
            let l = line.trim();
            if !l.is_empty() && !l.starts_with("C:\\") && !l.contains("SET") && !l.contains("set") && !l.starts_with('(') && !l.contains('>') {
                return l.to_string();
            }
        }
        "Unknown version".to_string()
    }

    fn verify_environment_tools(&self) -> Result<bool> {
        let mut tools: Vec<(&str, Vec<&str>)> = vec![
            ("python", vec!["--version"]),
            ("git", vec!["--version"]),
            ("ffmpeg", vec!["-version"]),
        ];
        if let Ok(list) = self.gpu_detector.detect_gpu_wmi() {
            if list.iter().any(|g| g.gpu_type == crate::gpu::GpuType::Nvidia) {
                tools.push(("nvcc", vec!["--version"]));
            }
        }

        let mut all_ok = true;
        for (tool, args) in tools {
            let cmd: Vec<String> = std::iter::once(tool.to_string()).chain(args.into_iter().map(|s| s.to_string())).collect();
            match self.run_in_activated_environment(&cmd, None) {
                Ok(output) => {
                    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                    let version = self.extract_version_from_output(tool, &stdout);
                    if version != "Unknown version" {
                        log::info!("[OK] {}: {}", tool, version);
                    } else {
                        log::error!("[ERROR] {}: Failed to run (code {:?})", tool, output.status.code());
                        if !stderr.trim().is_empty() { log::error!("   Error: {}", stderr.trim()); }
                        all_ok = false;
                    }
                }
                Err(e) => {
                    log::error!("[ERROR] {}: Exception occurred - {}", tool, e);
                    all_ok = false;
                }
            }
        }
        Ok(all_ok)
    }
    
    /// Setup the portable environment
    pub async fn setup_environment(&self) -> Result<()> {
        log::info!("Setting up portable environment...");
        fs::create_dir_all(&self.ps_env_path)?;
        // Ensure install_path recorded
        let mut cfgm = self.config_manager.clone();
        if cfgm.get_config().install_path.as_os_str().is_empty() {
            cfgm.set_install_path(self.install_path.clone())?;
        }

        // Configure GPU inside manager
        let _ = cfgm.configure_gpu_from_detection();
        let cfg_now = cfgm.get_config().clone();

        // Prepare progress tracking
        let print_lock = Arc::new(Mutex::new(()));
        let completed = Arc::new(AtomicUsize::new(0));
        let mut total_steps: usize = 0;

        // Determine total steps before starting any tasks
        let mut cuda_plan: Option<(String, String)> = None; // (download_link, expected_folder)
        if let Some(gpu) = &cfg_now.gpu_config {
            if let Some(cuda_ver) = &gpu.cuda_version {
                if gpu.recommended_backend.contains("cuda") {
                    if let Some(link) = self.config_manager.get_cuda_download_link(Some(cuda_ver)) {
                        total_steps += 2; // CUDA download + extract
                        let version_debug = format!("{:?}", cuda_ver).to_lowercase();
                        let cleaned = version_debug.replace("cuda", "").replace(['_', '"'], "");
                        let expected_folder = format!("cuda_{}", cleaned);
                        cuda_plan = Some((link, expected_folder));
                    }
                }
            }
        }
        // Each tool: download + extract
        total_steps += 2 * 3; // python, git, ffmpeg

        // Announce total steps
        {
            let _g = print_lock.lock().unwrap();
            println!("[Setup] Total steps: {}", total_steps);
        }

        // Create task handles
        let mut handles = Vec::new();
        let total_c = total_steps; // captured total for closures

        if let Some((link, expected_folder)) = cuda_plan {
            let ps_env = self.ps_env_path.clone();
            let archive_path = ps_env.join(format!(
                "CUDA_{}.7z",
                expected_folder.trim_start_matches("cuda_").to_uppercase()
            ));
            let completed_c = completed.clone();
            let print_lock_c = print_lock.clone();
            handles.push(tokio::task::spawn_blocking(move || {
                {
                    let _g = print_lock_c.lock().unwrap();
                    let done = completed_c.load(Ordering::SeqCst);
                    println!("[Setup] Downloading CUDA archive... (step {}/{})", done + 1, total_c);
                }
                PortableEnvironmentManager::download_with_resume_static(link, archive_path.clone())?;
                completed_c.fetch_add(1, Ordering::SeqCst);
                {
                    let _g = print_lock_c.lock().unwrap();
                    println!("[Setup] CUDA downloaded.");
                    println!("[Setup] Extracting CUDA... (next step)");
                }
                let temp_extract = ps_env.join("__cuda_extract_temp__");
                if temp_extract.exists() { let _ = fs::remove_dir_all(&temp_extract); }
                PortableEnvironmentManager::extract_7z_static(archive_path.clone(), temp_extract.clone())?;
                let extracted_sub = temp_extract.join(&expected_folder);
                let cuda_dir = ps_env.join("CUDA");
                if cuda_dir.exists() { let _ = fs::remove_dir_all(&cuda_dir); }
                if !extracted_sub.exists() { return Err(PortableSourceError::environment("Expected CUDA folder missing after extraction")); }
                fs::rename(&extracted_sub, &cuda_dir)?;
                let _ = fs::remove_dir_all(&temp_extract);
                let _ = fs::remove_file(&archive_path);
                completed_c.fetch_add(1, Ordering::SeqCst);
                {
                    let _g = print_lock_c.lock().unwrap();
                    println!("[Setup] CUDA extracted.");
                }
                Ok::<(), PortableSourceError>(())
            }));
        }

        // Other tools in parallel
        for key in ["python", "git", "ffmpeg"] {
            if let Some(spec) = self.tool_specs.get(key) {
                let url = spec.url.clone();
                let archive_name = Url::parse(&url)
                    .ok()
                    .and_then(|u| u.path_segments().and_then(|mut s| s.next_back()).map(|s| s.to_string()))
                    .unwrap_or_else(|| format!("{}.7z", spec.name));
                let ps_env = self.ps_env_path.clone();
                let exe_rel = spec.executable_path.clone();
                let completed_t = completed.clone();
                let print_lock_t = print_lock.clone();
                handles.push(tokio::task::spawn_blocking(move || {
                    {
                        let _g = print_lock_t.lock().unwrap();
                        let done = completed_t.load(Ordering::SeqCst);
                        println!("[Setup] Downloading {}... (step {}/{})", archive_name, done + 1, total_c);
                    }
                    let archive_path = ps_env.join(&archive_name);
                    PortableEnvironmentManager::download_with_resume_static(url, archive_path.clone())?;
                    completed_t.fetch_add(1, Ordering::SeqCst);
                    {
                        let _g = print_lock_t.lock().unwrap();
                        println!("[Setup] Extracting {}...", archive_name);
                    }
                    PortableEnvironmentManager::extract_7z_static(archive_path.clone(), ps_env.clone())?;
                    let _ = fs::remove_file(&archive_path);
                    let exe_path = ps_env.join(&exe_rel);
                    if !exe_path.exists() {
                        return Err(PortableSourceError::environment(format!("Executable not found: {:?}", exe_path)));
                    }
                    completed_t.fetch_add(1, Ordering::SeqCst);
                    {
                        let _g = print_lock_t.lock().unwrap();
                        println!("[Setup] {} installed.", exe_rel);
                    }
                    Ok::<(), PortableSourceError>(())
                }));
            }
        }

        // Await all tasks with progress print (avoid duplicate prints)
        let total = total_steps;
        let mut last_printed_done: usize = 0;
        for h in handles {
            let res = h.await.map_err(|e| PortableSourceError::environment(format!("Join error: {}", e)))?;
            if let Err(err) = res { return Err(err); }
            let done = completed.load(Ordering::SeqCst);
            if done != last_printed_done {
                last_printed_done = done;
                let pct = if total > 0 { (done as f32 / total as f32) * 100.0 } else { 100.0 };
                let _g = print_lock.lock().unwrap();
                println!("[Setup] Progress: {}/{} ({:.0}%)", done, total, pct);
            }
        }

        // Ensure final 100% line if not printed
        {
            let done = completed.load(Ordering::SeqCst);
            if done < total {
                let pct = if total > 0 { (done as f32 / total as f32) * 100.0 } else { 100.0 };
                let _g = print_lock.lock().unwrap();
                println!("[Setup] Progress: {}/{} ({:.0}%)", done, total, pct);
            }
        }

        // Configure CUDA paths if present
        if let Some(gpu) = &cfg_now.gpu_config { if gpu.cuda_version.is_some() && gpu.recommended_backend.contains("cuda") { cfgm.configure_cuda_paths(); } }

        // Verify tools
        if !self.verify_environment_tools()? { return Err(PortableSourceError::environment("Environment tools verification failed")); }

        // Mark completed
        cfgm.mark_environment_setup_completed(true)?;
        Ok(())
    }
    
    /// Check if environment is properly set up
    pub fn check_environment_status(&self) -> Result<bool> {
        // Check if ps_env directory exists and has required tools
        if !self.ps_env_path.exists() {
            return Ok(false);
        }
        let py = self.get_python_executable().map(|p| p.exists()).unwrap_or(false);
        let git = self.get_git_executable().map(|p| p.exists()).unwrap_or(false);
        let ffmpeg = self.get_ffmpeg_executable().map(|p| p.exists()).unwrap_or(false);
        Ok(py && git && ffmpeg)
    }
    
    /// Install a specific tool
    pub async fn install_tool(&self, tool_name: &str) -> Result<()> {
        log::info!("Installing tool: {}", tool_name);
        
        match tool_name {
            "python" => self.install_python().await,
            "git" => self.install_git().await,
            "ffmpeg" => self.install_ffmpeg().await,
            "cuda" => self.install_cuda().await,
            _ => Err(PortableSourceError::environment(
                format!("Unknown tool: {}", tool_name)
            )),
        }
    }
    
    async fn install_python(&self) -> Result<()> { self.install_portable_tool("python") }
    
    async fn install_git(&self) -> Result<()> { self.install_portable_tool("git") }
    
    async fn install_ffmpeg(&self) -> Result<()> { self.install_portable_tool("ffmpeg") }
    
    async fn install_cuda(&self) -> Result<()> {
        let cfg = self.config_manager.get_config();
        if let Some(gpu) = &cfg.gpu_config {
            if let Some(cuda_ver) = &gpu.cuda_version {
                if !gpu.recommended_backend.contains("cuda") { return Ok(()); }

                let cuda_dir = self.ps_env_path.join("CUDA");
                if cuda_dir.join("bin").exists() { return Ok(()); }

                // Ссылка на архив
                let link = self
                    .config_manager
                    .get_cuda_download_link(Some(cuda_ver))
                    .ok_or_else(|| PortableSourceError::environment("CUDA download link not available"))?;

                // Вычисляем версию в имени папки: CUDA_118.7z -> cuda_118
                let version_debug = format!("{:?}", cuda_ver).to_lowercase();
                let cleaned = version_debug.replace("cuda", "").replace(['_', '"'], "");
                let expected_folder = format!("cuda_{}", cleaned);

                let archive_path = self.ps_env_path.join(format!("CUDA_{}.7z", cleaned.to_uppercase()));
                self.download_with_resume(&link, &archive_path)?;

                // Распаковка во временную директорию
                let temp_extract = self.ps_env_path.join("__cuda_extract_temp__");
                if temp_extract.exists() { let _ = fs::remove_dir_all(&temp_extract); }
                self.extract_7z(&archive_path, &temp_extract)?;

                // Переименование папки cuda_{ver} -> CUDA (строго без манкипатчей)
                let extracted_sub = temp_extract.join(&expected_folder);
                if !extracted_sub.exists() {
                    return Err(PortableSourceError::environment(format!(
                        "Expected folder '{}' not found after extraction", expected_folder
                    )));
                }

                if cuda_dir.exists() { let _ = fs::remove_dir_all(&cuda_dir); }
                fs::rename(&extracted_sub, &cuda_dir)?;
                let _ = fs::remove_dir_all(&temp_extract);
                let _ = fs::remove_file(&archive_path);

                if !cuda_dir.join("bin").exists() {
                    return Err(PortableSourceError::environment("CUDA installation failed: bin not found"));
                }
                let mut cfgm = self.config_manager.clone();
                cfgm.configure_cuda_paths();
                log::info!("Successfully processed CUDA");
            }
        }
        Ok(())
    }
    
    /// Get path to Python executable
    pub fn get_python_executable(&self) -> Option<PathBuf> {
        let python_path = self.ps_env_path.join("python").join("python.exe");
        if python_path.exists() {
            Some(python_path)
        } else {
            None
        }
    }

    /// Get path to pip executable in ps_env
    pub fn get_ps_env_pip(&self) -> Option<PathBuf> {
        let p = if cfg!(windows) {
            self.ps_env_path.join("python").join("Scripts").join("pip.exe")
        } else {
            self.ps_env_path.join("python").join("bin").join("pip")
        };
        if p.exists() { Some(p) } else { None }
    }
    
    /// Get path to Git executable
    pub fn get_git_executable(&self) -> Option<PathBuf> {
        let git_path = self.ps_env_path.join("git").join("bin").join("git.exe");
        if git_path.exists() {
            Some(git_path)
        } else {
            None
        }
    }

    /// Get path to FFmpeg executable
    pub fn get_ffmpeg_executable(&self) -> Option<PathBuf> {
        let ffmpeg_path = self.ps_env_path.join("ffmpeg").join("ffmpeg.exe");
        if ffmpeg_path.exists() { Some(ffmpeg_path) } else { None }
    }
    
    /// Detailed environment status (summary)
    pub fn get_environment_status(&self) -> Result<EnvironmentStatus> {
        let mut status = EnvironmentStatus {
            environment_exists: self.ps_env_path.exists(),
            environment_setup_completed: self.config_manager.is_environment_setup_completed(),
            tools_status: HashMap::new(),
            all_tools_working: true,
            overall_status: String::new(),
        };

        if !status.environment_exists {
            status.overall_status = "Environment not found".to_string();
            return Ok(status);
        }

        self.check_and_suggest_cuda_installation();

        let mut tools: Vec<(&str, Vec<&str>)> = vec![
            ("python", vec!["--version"]),
            ("git", vec!["--version"]),
            ("ffmpeg", vec!["-version"]),
        ];
        if let Ok(list) = self.gpu_detector.detect_gpu_wmi() {
            if list.iter().any(|g| g.gpu_type == crate::gpu::GpuType::Nvidia) {
                tools.push(("nvcc", vec!["--version"]));
            }
        }

        for (tool, args) in tools {
            let cmd: Vec<String> = std::iter::once(tool.to_string()).chain(args.into_iter().map(|s| s.to_string())).collect();
            match self.run_in_activated_environment(&cmd, None) {
                Ok(output) => {
                    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
                    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
                    let version = self.extract_version_from_output(tool, &stdout);
                    if version != "Unknown version" {
                        status.tools_status.insert(tool.to_string(), ToolStatus { working: true, version: Some(version), error: None, stderr: None });
                    } else {
                        status.tools_status.insert(tool.to_string(), ToolStatus { working: false, version: None, error: Some(format!("Exit code {:?}", output.status.code())), stderr: if stderr.trim().is_empty() { None } else { Some(stderr.trim().to_string()) } });
                        status.all_tools_working = false;
                    }
                }
                Err(e) => {
                    status.tools_status.insert(tool.to_string(), ToolStatus { working: false, version: None, error: Some(e.to_string()), stderr: None });
                    status.all_tools_working = false;
                }
            }
        }
        status.overall_status = if status.all_tools_working { "Ready".to_string() } else { "Issues detected".to_string() };
        Ok(status)
    }

    /// Get environment info (paths and installed tools)
    pub fn get_environment_info(&self) -> EnvironmentInfo {
        let python_path = self.get_python_executable();
        let base_env_exists = self.ps_env_path.exists() && python_path.as_ref().map(|p| p.exists()).unwrap_or(false);
        let mut installed_tools = HashMap::new();
        for (name, spec) in &self.tool_specs {
            let tool_dir = self.ps_env_path.join(&spec.extract_path);
            installed_tools.insert(name.clone(), tool_dir.exists());
        }
        EnvironmentInfo {
            base_env_exists,
            base_env_python: python_path.map(|p| p.to_string_lossy().to_string()),
            base_env_pip: self.get_ps_env_pip().map(|p| p.to_string_lossy().to_string()),
            installed_tools,
            paths: EnvironmentPaths { ps_env_path: self.ps_env_path.to_string_lossy().to_string() },
        }
    }

    /// Suggest CUDA installation if misconfigured
    fn check_and_suggest_cuda_installation(&self) {
        if let Some(gpu) = &self.config_manager.get_config().gpu_config {
            if let Some(_cv) = &gpu.cuda_version {
                if let Some(paths) = &gpu.cuda_paths {
                    let base = &paths.base_path;
                    let bin = &paths.cuda_bin;
                    if !Path::new(&base).exists() {
                        log::warn!("CUDA is configured but not installed at {}", base.display());
                    } else if !Path::new(&bin).exists() {
                        log::warn!("CUDA installation incomplete: bin not found at {}", bin.display());
                    }
                } else {
                    log::warn!("CUDA version is set but paths are not configured");
                }
            }
        }
    }
}

fn sanitize_windows_path_for_7z(path: &Path) -> String {
    let mut s = path.to_string_lossy().to_string();
    if s.starts_with(r"\\?\") { s = s.trim_start_matches(r"\\?\").to_string(); }
    if s.starts_with('"') && s.ends_with('"') && s.len() >= 2 { s = s[1..s.len()-1].to_string(); }
    while s.ends_with('\\') { s.pop(); }
    s
}

fn format_7z_out_arg(path: &Path) -> String {
    let s = sanitize_windows_path_for_7z(path);
    if s.contains(' ') { format!("-o\"{}\"", s) } else { format!("-o{}", s) }
}

// Data structures for detailed status/info
pub struct ToolStatus {
    pub working: bool,
    pub version: Option<String>,
    pub error: Option<String>,
    pub stderr: Option<String>,
}

pub struct EnvironmentStatus {
    pub environment_exists: bool,
    pub environment_setup_completed: bool,
    pub tools_status: HashMap<String, ToolStatus>,
    pub all_tools_working: bool,
    pub overall_status: String,
}

pub struct EnvironmentPaths { pub ps_env_path: String }

pub struct EnvironmentInfo {
    pub base_env_exists: bool,
    pub base_env_python: Option<String>,
    pub base_env_pip: Option<String>,
    pub installed_tools: HashMap<String, bool>,
    pub paths: EnvironmentPaths,
}
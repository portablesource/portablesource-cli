use portablesource_rs::{
    cli::{Cli, Commands},
    config::ConfigManager,
    gpu::GpuDetector,
    utils,
    envs_manager::PortableEnvironmentManager,
    repository_installer::RepositoryInstaller,
    PortableSourceError,
    Result,
};
use log::{info, error, warn, LevelFilter};
use std::path::PathBuf;
use std::sync::OnceLock;
// use std::io; // not used

// Глобальная переменная для хранения install_path в текущей сессии
static SESSION_INSTALL_PATH: OnceLock<PathBuf> = OnceLock::new();

#[tokio::main]
async fn main() {
    // Parse command line arguments
    let cli = Cli::parse_args();

    // Initialize logging with default INFO (DEBUG if --debug)
    let mut builder = env_logger::Builder::from_default_env();
    if cli.debug { builder.filter_level(LevelFilter::Debug); } else { builder.filter_level(LevelFilter::Info); }
    let _ = builder.try_init();
    
    // Run the application
    if let Err(e) = run(cli).await {
        error!("Application error: {}", e);
        std::process::exit(1);
    }
}

async fn run(cli: Cli) -> Result<()> {
    // Fast-path: commands that don't require config or install_path
    match cli.command.as_ref() {
        Some(Commands::CheckGpu) => {
            return check_gpu();
        }
        Some(Commands::Version) => {
            utils::show_version();
            return Ok(());
        }
        _ => {}
    }

    // Initialize configuration manager
    let mut config_manager = ConfigManager::new(None)?;
    
    // Handle install path from CLI, registry, config, or default
    // Skip interactive prompt for commands that don't need install_path
    #[cfg(windows)]
    let needs_install_path = matches!(cli.command, Some(Commands::SetupEnv) | Some(Commands::InstallRepo { .. }) | Some(Commands::UpdateRepo { .. }) | Some(Commands::DeleteRepo { .. }) | Some(Commands::ListRepos) | Some(Commands::CheckEnv));
    #[cfg(unix)]
    let needs_install_path = matches!(cli.command, Some(Commands::SetupEnv) | Some(Commands::InstallRepo { .. }) | Some(Commands::UpdateRepo { .. }) | Some(Commands::DeleteRepo { .. }) | Some(Commands::ListRepos) | Some(Commands::ChangePath) | Some(Commands::CheckEnv) | Some(Commands::Uninstall));
    #[cfg(all(not(windows), not(unix)))]
    let needs_install_path = matches!(cli.command, Some(Commands::SetupEnv) | Some(Commands::InstallRepo { .. }) | Some(Commands::UpdateRepo { .. }) | Some(Commands::DeleteRepo { .. }) | Some(Commands::ListRepos) | Some(Commands::CheckEnv));

    let install_path = if let Some(cached_path) = SESSION_INSTALL_PATH.get() {
        // Используем сохраненный путь из текущей сессии
        cached_path.clone()
    } else if let Some(path) = cli.install_path {
        let validated_path = utils::validate_and_create_path(&path)?;
        config_manager.set_install_path(validated_path.clone())?;
        
        // Сохраняем путь в сессии
        let _ = SESSION_INSTALL_PATH.set(validated_path.clone());
        
        // Портативная логика только для Windows
        #[cfg(windows)]
        {
            // Просто запоминаем путь установки для текущей сессии
            // Копирование exe произойдет после команды setup-env
        }
        
        // Для Linux сохраняем в реестр как раньше
        #[cfg(unix)]
        {
            let _ = utils::save_install_path_to_registry(&validated_path);
        }
        // Для Windows больше не используем реестр - только портативный режим
        
        validated_path
    } else {
        // Портативная логика только для Windows
        #[cfg(windows)]
        {
            // Путь не указан - определяем автоматически
            let current_dir = std::env::current_exe()?
                .parent()
                .ok_or_else(|| PortableSourceError::installation("Cannot determine current directory".to_string()))?
                .to_path_buf();
            
            // Проверяем, находимся ли мы уже в установленной директории
            if !utils::is_first_installation(&current_dir) {
                // Мы в установленной директории - используем её
                // Сохраняем путь в сессии
                let _ = SESSION_INSTALL_PATH.set(current_dir.clone());
                current_dir
            } else {
                // Первый запуск - нужно выбрать путь установки
                if !needs_install_path {
                    // Для команд, не требующих установки, используем текущую директорию
                    // Сохраняем путь в сессии
                    let _ = SESSION_INSTALL_PATH.set(current_dir.clone());
                    current_dir
                } else {
                    // Для команд установки показываем интерактивный выбор
                    let default_path = std::env::current_dir()?.join("portablesource");
                    println!("Choose installation path (default: {})", default_path.display());
                    print!("Enter path or press Enter: ");
                    use std::io::{self, Write};
                    io::stdout().flush().ok();
                    let mut input = String::new();
                    io::stdin().read_line(&mut input).ok();
                    let input = input.trim();
                    
                    let chosen_path = if input.is_empty() {
                        default_path
                    } else {
                        PathBuf::from(input)
                    };
                    
                    let validated_path = utils::validate_and_create_path(&chosen_path)?;
                    utils::copy_executable_to_install_path(&validated_path)?;
                    // Сохраняем путь в сессии
                    let _ = SESSION_INSTALL_PATH.set(validated_path.clone());
                    validated_path
                }
            }
        }
        
        // Для Linux оставляем старую логику
        #[cfg(unix)]
        {
            if !needs_install_path {
                // Use existing config or silent defaults without prompting
                if let Some(path) = utils::load_install_path_from_registry()? {
                    utils::validate_and_create_path(&path)?
                } else if !config_manager.get_config().install_path.as_os_str().is_empty() {
                    let existing = config_manager.get_config().install_path.clone();
                    utils::validate_and_create_path(&existing)?
                } else {
                    let default_path = utils::default_install_path_linux();
                    utils::validate_and_create_path(&default_path)?
                }
            } else if let Some(path) = utils::load_install_path_from_registry()? {
                let validated_path = utils::validate_and_create_path(&path)?;
                config_manager.set_install_path(validated_path.clone())?;
                validated_path
            } else if !config_manager.get_config().install_path.as_os_str().is_empty() {
                let existing = config_manager.get_config().install_path.clone();
                if matches!(cli.command, Some(Commands::SetupEnv)) {
                    println!("\nCurrent installation path: {}", existing.display());
                    let chosen = utils::prompt_install_path_linux(&existing)?;
                    let _ = utils::save_install_path_to_registry(&chosen);
                    config_manager.set_install_path(chosen.clone())?;
                    chosen
                } else {
                    let validated_path = utils::validate_and_create_path(&existing)?;
                    config_manager.set_install_path(validated_path.clone())?;
                    validated_path
                }
            } else {
                if matches!(cli.command, Some(Commands::SetupEnv)) {
                    let default_path = utils::default_install_path_linux();
                    let chosen = utils::prompt_install_path_linux(&default_path)?;
                    let _ = utils::save_install_path_to_registry(&chosen);
                    config_manager.set_install_path(chosen.clone())?;
                    chosen
                } else {
                    let default_path = utils::default_install_path_linux();
                    utils::validate_and_create_path(&default_path)?
                }
            }
        }
    };
    
    // Всегда привязываем конфиг к install_path и сохраняем туда
    // (для Linux не требуем root и не используем /etc для persist)
    let _ = config_manager.set_install_path(install_path.clone());
    config_manager.set_config_path_to_install_dir();
    // Конфигурация больше не сохраняется на диск - только сессионные настройки
    info!("Using install path: {:?}", install_path);
    #[cfg(not(windows))]
    {
        // На Linux работаем как менеджер репозиториев без постоянного конфига
        // (используем только в памяти ConfigManager)
    }
    // Hydrate config from current environment (no extra save here)
    ensure_config_initialized(&mut config_manager)?;
    config_manager.hydrate_from_existing_env()?;

    // Linux: выбор режима CLOUD/DESK и базовая подготовка — только когда действительно готовим базу
    #[cfg(unix)]
    if matches!(cli.command, Some(Commands::SetupEnv)) {
        use portablesource_rs::utils::{detect_linux_mode, LinuxMode, detect_cuda_version_from_system, setup_micromamba_base_env};
        match detect_linux_mode() {
                        LinuxMode::Cloud => {
                info!("Linux CLOUD mode detected: using system git/python/cuda");
                let _cv_for_indexes = detect_cuda_version_from_system();
                let check = |name: &str| -> bool { utils::is_command_available(name) };
                let git_ok = check("git");
                let py_ok = check("python3") || check("python");
                let ff_ok = check("ffmpeg");
                let nvcc_ok = check("nvcc");
                println!(
                    "CLOUD requirements: git={} python={} ffmpeg={} nvcc={}",
                    if git_ok { "OK" } else { "Missing" },
                    if py_ok { "OK" } else { "Missing" },
                    if ff_ok { "OK" } else { "Missing" },
                    if nvcc_ok { "OK" } else { "Missing" }
                );
                if !(git_ok && py_ok && ff_ok) {
                    warn!("Some system tools missing; attempting to install missing packages (best-effort). You can also set PORTABLESOURCE_MODE=DESK.");
                    let _ = utils::prepare_linux_system();
                }
            }
            LinuxMode::Desk => {
                info!("Linux DESK mode detected: setting up micromamba base env");
                let cv = match detect_cuda_version_from_system() {
                    Some(_) => None,
                    None => {
                        if config_manager.has_cuda() {
                            if let Some(cuda_version) = config_manager.get_cuda_version() {
                                Some(match cuda_version {
                                    portablesource_rs::config::CudaVersion::Cuda128 => portablesource_rs::config::CudaVersionLinux::Cuda128,
                                    portablesource_rs::config::CudaVersion::Cuda124 => portablesource_rs::config::CudaVersionLinux::Cuda124,
                                    portablesource_rs::config::CudaVersion::Cuda118 => portablesource_rs::config::CudaVersionLinux::Cuda118,
                                })
                            } else {
                                None
                            }
                        } else {
                            None
                        }
                    }
                };
                setup_micromamba_base_env(&install_path, cv)?;
            }
        }
    }
    
    // Handle commands
    match cli.command.as_ref() {
        Some(Commands::SetupEnv) => {
            setup_environment(&install_path, &mut config_manager).await
        }
        #[cfg(unix)]
        Some(Commands::SetupReg) => {
            utils::save_install_path_to_registry(&install_path)?;
            println!("Installation path registered successfully");
            Ok(())
        }
        #[cfg(unix)]
        Some(Commands::Unregister) => {
            utils::delete_install_path_from_registry()?;
            println!("Installation path unregistered successfully");
            Ok(())
        }
        #[cfg(unix)]
        Some(Commands::Uninstall) => {
            utils::uninstall_portablesource(&install_path).await
        }
        #[cfg(unix)]
        Some(Commands::ChangePath) => {
            change_installation_path(&mut config_manager).await
        }
        Some(Commands::InstallRepo { repo }) => {
            install_repository(repo, &install_path, &config_manager).await
        }
        Some(Commands::UpdateRepo { repo }) => {
            update_repository(repo.clone(), &install_path, &config_manager).await
        }
        Some(Commands::DeleteRepo { repo }) => {
            delete_repository(repo, &install_path, &config_manager)
        }
        Some(Commands::ListRepos) => {
            list_repositories(&install_path, &config_manager)
        }
        Some(Commands::RunRepo { repo, args }) => {
            utils::run_repository(repo, &install_path, args).await
        }
        Some(Commands::SystemInfo) => {
            show_system_info(&mut config_manager).await
        }
        Some(Commands::CheckEnv) => {
            check_environment(&install_path, &config_manager).await
        }
        #[cfg(windows)]
        Some(Commands::InstallMsvc) => {
            utils::install_msvc_build_tools()
        }
        #[cfg(windows)]
        Some(Commands::CheckMsvc) => {
            let installed = utils::check_msvc_build_tools_installed();
            println!("MSVC Build Tools: {}", if installed { "Installed" } else { "Not installed" });
            Ok(())
        }
        Some(Commands::CheckGpu) => {
            check_gpu()
        }
        Some(Commands::Version) => {
            utils::show_version();
            Ok(())
        }
        None => {
            // No command provided, show system info by default
            show_system_info(&mut config_manager).await
        }
    }
}

async fn setup_environment(install_path: &PathBuf, config_manager: &mut ConfigManager) -> Result<()> {
    // Create directory structure
    utils::create_directory_structure(install_path)?;
    
    // Windows: ставим портативные инструменты (tar zstd архивы)
    #[cfg(windows)]
    {
        // Initialize environment manager
        let env_manager = PortableEnvironmentManager::new(install_path.clone());
        // Setup environment via portable archives
        env_manager.setup_environment().await?;
    }

    // Linux/macOS: используем системный tar, готовим базу через micromamba
    #[cfg(unix)]
    {
        use portablesource_rs::utils::{detect_cuda_version_from_system, setup_micromamba_base_env};
        // Если системная CUDA есть — не ставим CUDA в базу
        let cv = match detect_cuda_version_from_system() {
            Some(_) => None,
            None => {
                if config_manager.has_cuda() {
                    if let Some(cuda_version) = config_manager.get_cuda_version() {
                        Some(match cuda_version {
                            portablesource_rs::config::CudaVersion::Cuda128 => portablesource_rs::config::CudaVersionLinux::Cuda128,
                            portablesource_rs::config::CudaVersion::Cuda124 => portablesource_rs::config::CudaVersionLinux::Cuda124,
                            portablesource_rs::config::CudaVersion::Cuda118 => portablesource_rs::config::CudaVersionLinux::Cuda118,
                        })
                    } else {
                        None
                    }
                } else {
                    None
                }
            }
        };
        setup_micromamba_base_env(install_path, cv)?;
    }
    
    // GPU detection is now handled dynamically by ConfigManager
    let gpu_detector = GpuDetector::new();
    if let Some(gpu_info) = gpu_detector.get_best_gpu()? {
        info!("Detected GPU: {}", gpu_info.name);
    } else {
        warn!("No GPU detected, using CPU backend");
    }
    
    // Mark environment as setup (сохранение один раз в конце)
    config_manager.get_config_mut().environment_setup_completed = true;
    // Не сохраняем здесь повторно: итоговый save будет ниже, после GPU-конфига
    
    // Сохранение конфигурации ровно один раз после всех шагов
    // Конфигурация больше не сохраняется на диск - только сессионные настройки

    // Executable was already copied during initial setup

    println!("Environment setup completed successfully!");
    Ok(())
}

#[cfg(unix)]
async fn change_installation_path(config_manager: &mut ConfigManager) -> Result<()> {
    println!("Enter new installation path:");
    let mut input = String::new();
    std::io::stdin().read_line(&mut input).unwrap();
    let path = PathBuf::from(input.trim());
    
    let validated_path = utils::validate_and_create_path(&path)?;
    config_manager.set_install_path(validated_path.clone())?;
    // Для Windows больше не используем реестр - только сессионные настройки
    #[cfg(unix)]
    {
        utils::save_install_path_to_registry(&validated_path)?;
    }
    
    println!("Installation path changed to: {:?}", validated_path);
    Ok(())
}

async fn install_repository(repo: &str, install_path: &PathBuf, config_manager: &ConfigManager) -> Result<()> {
    let mut installer = RepositoryInstaller::new(install_path.clone(), config_manager.clone());
    installer.install_repository(repo).await
}

async fn update_repository(repo: Option<String>, install_path: &PathBuf, config_manager: &ConfigManager) -> Result<()> {
    let mut installer = RepositoryInstaller::new(install_path.clone(), config_manager.clone());
    if let Some(name) = repo {
        return installer.update_repository(&name).await;
    }

    // Simple TUI: показать список и выбрать номер
    let labeled = installer.list_repositories_labeled()?;
    let names: Vec<String> = labeled.iter().map(|(raw, _)| raw.clone()).collect();
    if names.is_empty() {
        println!("No repositories installed");
        return Ok(());
    }

    println!("Select repository to update:\n");
    for (i, item) in labeled.iter().enumerate() {
        println!("  [{}] {}", i + 1, item.1);
    }
    println!("\nEnter number (or 0 to cancel): ");

    use std::io;
    let mut input = String::new();
    io::stdin().read_line(&mut input).ok();
    let trimmed = input.trim();
    let choice: usize = trimmed.parse().unwrap_or(0);
    if choice == 0 || choice > names.len() {
        println!("Cancelled.");
        return Ok(());
    }

    let selected = &names[choice - 1];
    installer.update_repository(selected).await
}

fn delete_repository(repo: &str, install_path: &PathBuf, config_manager: &ConfigManager) -> Result<()> {
    let installer = RepositoryInstaller::new(install_path.clone(), config_manager.clone());
    installer.delete_repository(repo)
}

fn list_repositories(install_path: &PathBuf, config_manager: &ConfigManager) -> Result<()> {
    let installer = RepositoryInstaller::new(install_path.clone(), config_manager.clone());
    let repos = installer.list_repositories()?;
    
    if repos.is_empty() {
        println!("No repositories installed");
    } else {
        println!("Installed repositories:");
        for repo in repos {
            println!("  - {}", repo);
        }
    }
    
    Ok(())
}

async fn show_system_info(config_manager: &mut ConfigManager) -> Result<()> {
    println!("=== PortableSource System Information ===");
    // Assemble config if empty
    ensure_config_initialized(config_manager)?;
    // Hydrate from existing ps_env and nvidia-smi
    config_manager.hydrate_from_existing_env()?;
    
    // Show configuration summary
    println!("\n{}", config_manager.get_config_summary());
    
    // Show system info
    // On Unix: if DESK mode, show only micromamba base tools; if CLOUD mode, show only system tools
    #[cfg(unix)]
    {
        use portablesource_rs::utils::{detect_linux_mode, LinuxMode};
        match detect_linux_mode() {
            LinuxMode::Desk => {
                let base_bin = config_manager
                    .get_config()
                    .install_path
                    .join("ps_env")
                    .join("mamba_env")
                    .join("bin");
                println!("\n=== Micromamba Base ===");
                if base_bin.exists() {
                    let check = |name: &str| base_bin.join(name).exists();
                    let py_ok = check("python") || check("python3");
                    let pip_ok = check("pip") || check("pip3");
                    let git_ok = check("git");
                    let ff_ok = check("ffmpeg");
                    println!("python: {}", if py_ok { "Available" } else { "Not found" });
                    println!("pip: {}", if pip_ok { "Available" } else { "Not found" });
                    println!("git: {}", if git_ok { "Available" } else { "Not found" });
                    println!("ffmpeg: {}", if ff_ok { "Available" } else { "Not found" });
                    let cuda_ok = base_bin.join("nvcc").exists();
                    println!("cuda: {}", if cuda_ok { "Available" } else { "Not found" });
                } else {
                    println!("Micromamba base not found at {}", base_bin.display());
                }
            }
            LinuxMode::Cloud => {
                println!("\n=== System Information (CLOUD) ===");
                let system_info = utils::get_system_info()?;
                println!("{}", system_info);
                println!("\nTip: set PORTABLESOURCE_MODE=DESK to force micromamba-based portable env on Linux.");
            }
        }
    }
    #[cfg(windows)]
    {
        println!("\n=== System Information ===");
        let system_info = utils::get_system_info()?;
        println!("{}", system_info);
    }
    
    // Show GPU info
    let gpu_detector = GpuDetector::new();
    if let Some(gpu_info) = gpu_detector.get_best_gpu()? {
        println!("\n=== GPU Information ===");
        println!("Name: {}", gpu_info.name);
        println!("Type: {:?}", gpu_info.gpu_type);
        println!("Memory: {} MB", gpu_info.memory_mb);
        if let Some(driver) = &gpu_info.driver_version {
            println!("Driver: {}", driver);
        }
    }
    
    Ok(())
}

fn ensure_config_initialized(config_manager: &mut ConfigManager) -> Result<()> {
    // Ensure install path set (already set in run(), but double-check)
    if config_manager.get_config().install_path.as_os_str().is_empty() {
        #[cfg(windows)]
        {
            // Для Windows используем только текущую директорию - без реестра
            let default_path = std::env::current_dir()?.join("portablesource");
            let validated = utils::validate_and_create_path(&default_path)?;
            config_manager.set_install_path(validated)?;
        }
        #[cfg(unix)]
        {
            if let Some(reg_path) = utils::load_install_path_from_registry()? {
                config_manager.set_install_path(reg_path)?;
            } else {
                let default_path = std::env::current_dir()?.join("portablesource");
                let validated = utils::validate_and_create_path(&default_path)?;
                config_manager.set_install_path(validated)?;
            }
        }
    }
    // Ensure environment vars in config
    if config_manager.get_config().environment_vars.is_none() {
        let _ = config_manager.configure_environment_vars();
    }
    // GPU detection is now handled dynamically by ConfigManager
    // No need to store GPU config as it's computed on-demand
    Ok(())
}

async fn check_environment(install_path: &PathBuf, _config_manager: &ConfigManager) -> Result<()> {
    println!("=== Environment Status ===");
    
    let env_manager = PortableEnvironmentManager::new(install_path.clone());
    #[cfg(unix)]
    let status = {
        let base_bin = install_path.join("ps_env").join("mamba_env").join("bin");
        base_bin.join("python").exists() && base_bin.join("git").exists() && base_bin.join("ffmpeg").exists()
    };
    #[cfg(windows)]
    let status = env_manager.check_environment_status()?;
    
    println!("Environment setup: {}", if status { "OK" } else { "Not setup" });
    #[cfg(windows)]
    println!("MSVC Build Tools: {}", if utils::check_msvc_build_tools_installed() { "Installed" } else { "Not installed" });
    
    // Check for tools
    println!("\n=== Available Tools ===");
    #[cfg(unix)]
    {
        let base_bin = install_path.join("ps_env").join("mamba_env").join("bin");
        let chk = |name: &str| {
            let p = base_bin.join(name);
            std::fs::metadata(&p).is_ok() || p.exists()
        };
        println!("git: {}", if chk("git") { "Available" } else { "Not found" });
        println!("python: {}", if chk("python") || chk("python3") { "Available" } else { "Not found" });
        println!("ffmpeg: {}", if chk("ffmpeg") { "Available" } else { "Not found" });
        // CUDA availability (via nvcc) in micromamba base
        let nvcc_path = base_bin.join("nvcc");
        let cuda_ok = std::fs::metadata(&nvcc_path).is_ok();
        println!("cuda: {}", if cuda_ok { "Available" } else { "Not found" });
    }
    #[cfg(windows)]
    {
        let tools = ["git", "python", "ffmpeg"];
        for tool in &tools {
            let available = utils::is_command_available(tool);
            println!("{}: {}", tool, if available { "Available" } else { "Not found" });
        }
    }
    
    Ok(())
}



fn check_gpu() -> Result<()> {
    let gpu_detector = GpuDetector::new();
    let has_nvidia = gpu_detector.has_nvidia_gpu();
    println!("{}", has_nvidia);
    Ok(())
}

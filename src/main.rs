use portablesource_rs::{
    cli::{Cli, Commands},
    config::ConfigManager,
    gpu::GpuDetector,
    utils,
    envs_manager::PortableEnvironmentManager,
    repository_installer::RepositoryInstaller,
    Result,
};
use log::{info, error, warn, LevelFilter};
use std::path::PathBuf;
// use std::io; // not used

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
    // Initialize configuration manager
    let mut config_manager = ConfigManager::new(None)?;
    
    // Handle install path from CLI or registry
    let install_path = if let Some(path) = cli.install_path {
        let validated_path = utils::validate_and_create_path(&path)?;
        config_manager.set_install_path(validated_path.clone())?;
        validated_path
    } else if let Some(path) = utils::load_install_path_from_registry()? {
        let validated_path = utils::validate_and_create_path(&path)?;
        config_manager.set_install_path(validated_path.clone())?;
        validated_path
    } else {
        // Default path: Windows -> current_dir/portablesource; Linux -> /root/portablesource
        #[cfg(windows)]
        let default_path = std::env::current_dir()?.join("portablesource");
        #[cfg(unix)]
        let default_path = PathBuf::from("/root/portablesource");
        let validated_path = utils::validate_and_create_path(&default_path)?;
        config_manager.set_install_path(validated_path.clone())?;
        validated_path
    };
    
    // Ensure config file is anchored to install_path, not AppData
    // На Linux временно не используем конфиг-файл (по требованию)
    #[cfg(windows)]
    config_manager.set_config_path_to_install_dir();
    info!("Using install path: {:?}", install_path);
    #[cfg(not(windows))]
    {
        // На Linux работаем как менеджер репозиториев без постоянного конфига
        // (используем только в памяти ConfigManager)
    }
    // Hydrate config from current environment (no extra save here)
    ensure_config_initialized(&mut config_manager)?;
    config_manager.hydrate_from_existing_env()?;

    // Linux: best-effort подготовка системы (root предпочтителен)
    #[cfg(unix)]
    {
        let _ = utils::prepare_linux_system();
    }
    
    // Handle commands
    match cli.command.as_ref() {
        Some(Commands::SetupEnv) => {
            setup_environment(&install_path, &mut config_manager).await
        }
        Some(Commands::SetupReg) => {
            utils::save_install_path_to_registry(&install_path)?;
            println!("Installation path registered successfully");
            Ok(())
        }
        Some(Commands::Unregister) => {
            utils::delete_install_path_from_registry()?;
            println!("Installation path unregistered successfully");
            Ok(())
        }
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
        Some(Commands::SystemInfo) => {
            show_system_info(&mut config_manager).await
        }
        Some(Commands::CheckEnv) => {
            check_environment(&install_path, &config_manager).await
        }
        Some(Commands::InstallMsvc) => {
            utils::install_msvc_build_tools()
        }
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
    info!("Setting up PortableSource environment...");
    
    // Create directory structure
    utils::create_directory_structure(install_path)?;
    
    // Initialize environment manager
    let env_manager = PortableEnvironmentManager::new(install_path.clone());
    
    // Setup environment
    env_manager.setup_environment().await?;
    
    // Detect and configure GPU
    let gpu_detector = GpuDetector::new();
    if let Some(gpu_info) = gpu_detector.get_best_gpu()? {
        info!("Detected GPU: {}", gpu_info.name);
        let gpu_config = gpu_detector.create_gpu_config(&gpu_info, config_manager);
        config_manager.get_config_mut().gpu_config = Some(gpu_config);
    } else {
        warn!("No GPU detected, using CPU backend");
    }
    
    // Mark environment as setup
    config_manager.get_config_mut().environment_setup_completed = true;
    config_manager.save_config()?;
    
    println!("Environment setup completed successfully!");
    Ok(())
}

async fn change_installation_path(config_manager: &mut ConfigManager) -> Result<()> {
    println!("Enter new installation path:");
    let mut input = String::new();
    std::io::stdin().read_line(&mut input).unwrap();
    let path = PathBuf::from(input.trim());
    
    let validated_path = utils::validate_and_create_path(&path)?;
    config_manager.set_install_path(validated_path.clone())?;
    utils::save_install_path_to_registry(&validated_path)?;
    
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
    println!("\n=== System Information ===");
    let system_info = utils::get_system_info()?;
    println!("{}", system_info);
    
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
        if let Some(reg_path) = utils::load_install_path_from_registry()? {
            config_manager.set_install_path(reg_path)?;
        } else {
            let default_path = std::env::current_dir()?.join("portablesource");
            let validated = utils::validate_and_create_path(&default_path)?;
            config_manager.set_install_path(validated)?;
        }
    }
    // Ensure environment vars in config
    if config_manager.get_config().environment_vars.is_none() {
        let _ = config_manager.configure_environment_vars();
    }
    // Ensure GPU config
    let gpu_missing = config_manager.get_config().gpu_config.is_none()
        || config_manager.get_config().gpu_config.as_ref().map(|g| g.name.is_empty() || g.name == "Unknown GPU" || g.memory_gb == 0).unwrap_or(true);
    if gpu_missing {
        let _ = config_manager.configure_gpu_from_detection();
        config_manager.configure_cuda_paths();
    }
    Ok(())
}

async fn check_environment(install_path: &PathBuf, _config_manager: &ConfigManager) -> Result<()> {
    println!("=== Environment Status ===");
    
    let env_manager = PortableEnvironmentManager::new(install_path.clone());
    let status = env_manager.check_environment_status()?;
    
    println!("Environment setup: {}", if status { "OK" } else { "Not setup" });
    println!("MSVC Build Tools: {}", 
        if utils::check_msvc_build_tools_installed() { "Installed" } else { "Not installed" });
    
    // Check for tools
    let tools = ["git", "python", "pip"];
    println!("\n=== Available Tools ===");
    for tool in &tools {
        let available = utils::is_command_available(tool);
        println!("{}: {}", tool, if available { "Available" } else { "Not found" });
    }
    
    Ok(())
}

fn check_gpu() -> Result<()> {
    let gpu_detector = GpuDetector::new();
    let has_nvidia = gpu_detector.has_nvidia_gpu();
    println!("{}", has_nvidia);
    Ok(())
}

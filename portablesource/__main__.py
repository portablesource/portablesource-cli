#!/usr/bin/env python3
"""
PortableSource - Main Entry Point
Emulates the behavior of a compiled .exe file
"""

import os
import sys
import logging
import argparse
from pathlib import Path
from typing import Optional
import winreg

# Relative imports
from portablesource.get_gpu import GPUDetector
from portablesource.config import ConfigManager, SERVER_DOMAIN
from portablesource.envs_manager import EnvironmentManager, EnvironmentSpec
from portablesource.repository_installer import RepositoryInstaller

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)

class PortableSourceApp:
    """Main PortableSource Application"""
    
    def __init__(self):
        self.install_path: Optional[Path] = None
        self.config_manager: Optional[ConfigManager] = None
        self.environment_manager: Optional[EnvironmentManager] = None
        self.repository_installer: Optional[RepositoryInstaller] = None
        self.gpu_detector = GPUDetector()
        
    def initialize(self, install_path: Optional[str] = None):
        """Initialize the application"""
        # Determine installation path
        if install_path:
            self.install_path = Path(install_path).resolve()
            # Save the provided path to registry
            self._save_install_path_to_registry(self.install_path)
        else:
            # Request path from user
            self.install_path = self._get_installation_path()
        
        # Create directory structure
        self._create_directory_structure()
        
        # Initialize environment manager
        self._initialize_environment_manager()
        
        # Check environment integrity on startup
        self._check_environment_on_startup()
        
        # Initialize configuration
        self._initialize_config()
        
        # Initialize repository installer
        self._initialize_repository_installer()
    
    def _save_install_path_to_registry(self, install_path: Path) -> bool:
        """Save installation path to Windows registry"""
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
            winreg.SetValueEx(key, "InstallPath", 0, winreg.REG_SZ, str(install_path))
            winreg.CloseKey(key)
            return True
        except Exception as e:
            return False
    
    def _load_install_path_from_registry(self) -> Optional[Path]:
        """Load installation path from Windows registry"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
            install_path_str, _ = winreg.QueryValueEx(key, "InstallPath")
            winreg.CloseKey(key)
            
            install_path = Path(install_path_str)
            
            # Return path from registry without checking existence
            # Directory may not exist, but that's normal - it will be created
            return install_path
        except FileNotFoundError:
            return None
        except Exception as e:
            return None

    def _get_installation_path(self) -> Path:
        """Request installation path from user"""
        # First try to load from registry
        registry_path = self._load_install_path_from_registry()
        
        if registry_path:
            # If path found in registry, use it automatically
            return registry_path
        
        # If no path in registry, request from user
        print("\n" + "="*60)
        print("PORTABLESOURCE INSTALLATION PATH SETUP")
        print("="*60)
        
        # Offer options
        default_path = Path("C:/PortableSource")
        
        print(f"\nDefault path will be used: {default_path}")
        print("\nYou can:")
        print("1. Press Enter to use the default path")
        print("2. Enter your own installation path")
        
        user_input = input("\nEnter installation path (or Enter for default): ").strip()
        
        if not user_input:
            chosen_path = default_path
        else:
            chosen_path = self._validate_and_get_path(user_input)
        
        print(f"\nChosen installation path: {chosen_path}")
        
        # Check if path exists and is not empty
        if chosen_path.exists() and any(chosen_path.iterdir()):
            print(f"\nWarning: Directory {chosen_path} already exists and is not empty.")
            while True:
                confirm = input("Continue? (y/n): ").strip().lower()
                if confirm in ['y', 'yes']:
                    break
                elif confirm in ['n', 'no']:
                    print("Installation cancelled.")
                    sys.exit(1)
                else:
                    print("Please enter 'y' or 'n'")
        
        # Save path to registry
        self._save_install_path_to_registry(chosen_path)
        
        return chosen_path
    
    def _validate_and_get_path(self, user_input: str) -> Path:
        """Validate and return path from user"""
        while True:
            try:
                chosen_path = Path(user_input).resolve()
                
                # Check that path is valid
                if chosen_path.is_absolute():
                    return chosen_path
                else:
                    print(f"Error: Path must be absolute. Please try again.")
                    user_input = input("Enter correct path: ").strip()
                    continue
                    
            except Exception as e:
                print(f"Error: Invalid path '{user_input}'. Please try again.")
                user_input = input("Enter correct path: ").strip()
                continue
    
    def _create_directory_structure(self):
        """Create directory structure"""
        if not self.install_path:
            raise ValueError("Install path is not set")
            
        directories = [
            self.install_path,
            self.install_path / "miniconda",       # Miniconda
            self.install_path / "repos",           # Repositories
            self.install_path / "envs",            # Conda environments
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def _initialize_environment_manager(self):
        """Initialize environment manager"""
        if not self.install_path:
            raise ValueError("Install path is not set")
        self.environment_manager = EnvironmentManager(self.install_path)
    
    def _check_environment_on_startup(self):
        """Check environment integrity on startup and reinstall if necessary"""
        if not self.environment_manager:
            return
        
        # Check Miniconda availability
        if not self.environment_manager.ensure_miniconda():
            logger.warning("Miniconda not found or corrupted")
            return
        
        # Check base environment integrity
        if self.install_path is not None:
            conda_env_path = self.install_path / "miniconda" / "envs" / "portablesource"
            if conda_env_path.exists():
                if not self.environment_manager.check_base_environment_integrity():
                    logger.warning("Base environment corrupted, performing automatic reinstallation...")
                    if self.environment_manager.create_base_environment():
                        logger.info("✅ Base environment successfully reinstalled")
                    else:
                        logger.error("❌ Failed to reinstall base environment")
                else:
                    logger.info("✅ Base environment is working correctly")
            else:
                logger.info("Base environment not found (will be created when needed)")
    
    def _initialize_config(self):
        """Initialize configuration"""
        if not self.install_path:
            raise ValueError("Install path is not set")
            
        # For new architecture, configuration is simplified
        # Pass correct path for configuration
        config_path = self.install_path / "portablesource_config.json"
        self.config_manager = ConfigManager(config_path)
        
        # Configure installation path (must be before configure_gpu)
        self.config_manager.configure_install_path(str(self.install_path))
        
        # Automatic GPU detection
        gpu_info = self.gpu_detector.get_gpu_info()
        if gpu_info:
            primary_gpu = gpu_info[0]
            # Pass memory in GB if available
            memory_gb = primary_gpu.memory // 1024 if primary_gpu.memory else 0
            self.config_manager.configure_gpu(primary_gpu.name, memory_gb)
        else:
            logger.warning("No GPU detected, using CPU mode")
        
        # Don't save configuration - it's generated dynamically
    
    def _initialize_repository_installer(self):
        """Initialize repository installer"""
        self.repository_installer = RepositoryInstaller(
            config_manager=self.config_manager,
            server_url=f"http://{SERVER_DOMAIN}"
        )
    
    def check_miniconda_availability(self) -> bool:
        """Check Miniconda availability"""
        if not self.environment_manager:
            return False
        return self.environment_manager.installer.is_installed()
    
    def setup_environment(self):
        """Setup environment (Miniconda + base environment)"""
        logger.info("Setting up environment...")
        
        if not self.environment_manager:
            logger.error("Environment manager not initialized")
            return False
        
        # Install Miniconda
        if not self.environment_manager.ensure_miniconda():
            logger.error("Error installing Miniconda")
            return False
        
        # Create base environment (with integrity check)
        if not self.environment_manager.create_base_environment():
            logger.error("Error creating base environment")
            return False
        
        # Additional integrity check after creation
        if not self.environment_manager.check_base_environment_integrity():
            logger.error("Base environment created, but integrity check failed")
            return False
        
        logger.info("Environment setup completed successfully")
        return True
    
    def install_repository(self, repo_url_or_name: str) -> bool:
        """Install repository"""
        if not self.repository_installer:
            logger.error("Repository installer not initialized")
            return False
        
        # Path for repository installation
        if not self.install_path:
            logger.error("Install path is not set")
            return False
            
        repo_install_path = self.install_path / "repos"
        
        # Install repository - all logic inside repository_installer
        success = self.repository_installer.install_repository(
            repo_url_or_name, 
            repo_install_path
        )
        
        return success
    
    def update_repository(self, repo_name: str) -> bool:
        """Update repository"""
        logger.info(f"Updating repository: {repo_name}")
        
        if not self.repository_installer:
            logger.error("Repository installer not initialized")
            return False
        
        # Path to repository
        if not self.install_path:
            logger.error("Install path is not set")
            return False
            
        repo_install_path = self.install_path / "repos"
        repo_path = repo_install_path / repo_name
        
        # Check if repository exists
        if not repo_path.exists():
            logger.error(f"Repository {repo_name} not found in {repo_path}")
            logger.info("Available repositories:")
            repos = self.list_installed_repositories()
            for repo in repos:
                logger.info(f"  - {repo['name']}")
            return False
        
        # Check if it's a git repository
        if not (repo_path / ".git").exists():
            logger.error(f"Directory {repo_path} is not a git repository")
            return False
        
        # Update repository using repository_installer
        git_exe = self.repository_installer._get_git_executable()
        success = self.repository_installer._update_repository_with_fixes(git_exe, repo_path)
        
        if success:
            logger.info(f"✅ Repository {repo_name} successfully updated")
        else:
            logger.error(f"❌ Failed to update repository {repo_name}")
        
        return success
    
    def _extract_repo_name(self, repo_url_or_name: str) -> str:
        """Extract repository name from URL or name"""
        # Use method from repository_installer
        if self.repository_installer is None:
            logger.error("Repository installer not initialized")
            return ""
        return self.repository_installer._extract_repo_name(repo_url_or_name)
    
    def _find_main_file(self, repo_path, repo_name, repo_url) -> Optional[str]:
        """Find main file of repository"""
        # Use MainFileFinder from repository_installer
        if self.repository_installer is None:
            logger.error("Repository installer not initialized")
            return None
        main_file = self.repository_installer.main_file_finder.find_main_file(repo_name, repo_path, repo_url)
        return main_file
    
    def list_installed_repositories(self):
        """List installed repositories"""
        if not self.install_path:
            logger.error("Install path is not set")
            return []
            
        repos_path = self.install_path / "repos"
        if not repos_path.exists():
            logger.info("Repositories directory not found")
            return []
        
        repos = []
        for item in repos_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check if launcher exists
                bat_file = item / f"start_{item.name}.bat"
                sh_file = item / f"start_{item.name}.sh"
                has_launcher = bat_file.exists() or sh_file.exists()
                
                repo_info = {
                    'name': item.name,
                    'path': str(item),
                    'has_launcher': has_launcher
                }
                repos.append(repo_info)
        
        logger.info(f"Found repositories: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"  - {repo['name']} {launcher_status}")
        
        return repos
    
    def setup_registry(self):
        """Register installation path in Windows registry"""
        if not self.install_path:
            logger.error("Installation path not defined")
            return False
        
        logger.info("Registering installation path in Windows registry...")
        
        success = self._save_install_path_to_registry(self.install_path)
        
        if success:
            logger.info("✅ Installation path successfully registered in registry")
            logger.info(f"Path: {self.install_path}")
            logger.info("PortableSource will now automatically use this path")
        else:
            logger.error("❌ Failed to register path in registry")
        
        return success
    
    def change_installation_path(self):
        """Change installation path"""
        print("\n" + "="*60)
        print("CHANGE PORTABLESOURCE INSTALLATION PATH")
        print("="*60)
        
        # Show current path
        current_path = self._load_install_path_from_registry()
        if current_path:
            print(f"\nCurrent installation path: {current_path}")
        else:
            print("\nCurrent installation path not found in registry")
        
        # Offer options
        default_path = Path("C:/PortableSource")
        
        print(f"\nDefault path will be used: {default_path}")
        print("\nYou can:")
        print("1. Press Enter to use the default path")
        print("2. Enter your own installation path")
        
        user_input = input("\nEnter new installation path (or Enter for default): ").strip()
        
        if not user_input:
            new_path = default_path
        else:
            new_path = self._validate_and_get_path(user_input)
        
        print(f"\nNew installation path: {new_path}")
        
        # Check if path exists and is not empty
        if new_path.exists() and any(new_path.iterdir()):
            print(f"\nWarning: Directory {new_path} already exists and is not empty.")
            while True:
                confirm = input("Continue? (y/n): ").strip().lower()
                if confirm in ['y', 'yes']:
                    break
                elif confirm in ['n', 'no']:
                    print("Path change cancelled.")
                    return False
                else:
                    print("Please enter 'y' or 'n'")
        
        # Save new path to registry
        success = self._save_install_path_to_registry(new_path)
        
        if success:
            logger.info("✅ Installation path successfully changed")
            logger.info(f"New path: {new_path}")
            logger.info("Restart PortableSource to apply changes")
            
            # Update current path in application
            self.install_path = new_path
        else:
            logger.error("❌ Failed to save new path to registry")
        
        return success
    
    def show_system_info(self):
        """Show system information"""
        logger.info("PortableSource - System Information:")
        logger.info(f"  - Installation path: {self.install_path}")
        logger.info(f"  - Operating system: {self.gpu_detector.system}")
        
        # Directory structure
        logger.info("  - Directory structure:")
        logger.info(f"    * {self.install_path}/miniconda")
        logger.info(f"    * {self.install_path}/repos")
        logger.info(f"    * {self.install_path}/envs")
        
        gpu_info = self.gpu_detector.get_gpu_info()
        if gpu_info:
            logger.info(f"  - GPU: {gpu_info[0].name}")
            logger.info(f"  - GPU type: {gpu_info[0].gpu_type.value}")
            if gpu_info[0].cuda_version:
                logger.info(f"  - CUDA version: {gpu_info[0].cuda_version.value}")
        
        # Miniconda status
        miniconda_status = "Installed" if self.check_miniconda_availability() else "Not installed"
        logger.info(f"  - Miniconda: {miniconda_status}")
        
        # Conda environments (common tools)
        if self.environment_manager and self.check_miniconda_availability():
            try:
                import json
                result = self.environment_manager.run_conda_command(["env", "list", "--json"])
                if result.returncode == 0:
                    data = json.loads(result.stdout)
                    conda_envs = []
                    for env_path in data.get("envs", []):
                        env_name = Path(env_path).name
                        conda_envs.append(env_name)
                    logger.info(f"  - Conda environments (common tools): {len(conda_envs)}")
                    for env in conda_envs:
                        logger.info(f"    * {env}")
            except Exception as e:
                logger.warning(f"Failed to get conda environments list: {e}")
        
        # Venv environments (repository-specific)
        if self.environment_manager:
            venv_envs = self.environment_manager.list_environments()
            logger.info(f"  - Venv environments (for repositories): {len(venv_envs)}")
            for env in venv_envs:
                logger.info(f"    * {env}")
        
        # Repository status
        repos = self.list_installed_repositories()
        logger.info(f"  - Installed repositories: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"    * {repo['name']} {launcher_status}")

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="PortableSource - Portable AI/ML Environment")
    parser.add_argument("--install-path", type=str, help="Installation path")
    parser.add_argument("--setup-env", action="store_true", help="Setup environment (Miniconda)")
    parser.add_argument("--setup-reg", action="store_true", help="Register installation path in registry")
    parser.add_argument("--change-path", action="store_true", help="Change installation path")
    parser.add_argument("--install-repo", type=str, help="Install repository")
    parser.add_argument("--update-repo", type=str, help="Update repository")
    parser.add_argument("--list-repos", action="store_true", help="Show installed repositories")
    parser.add_argument("--system-info", action="store_true", help="Show system information")
    
    args = parser.parse_args()
    
    # Create application
    app = PortableSourceApp()
    
    # For path change command, full initialization is not needed
    if args.change_path:
        app.change_installation_path()
        return
    
    # Initialize for other commands
    app.initialize(args.install_path)
    
    # Execute commands
    if args.setup_env:
        app.setup_environment()
    
    if args.setup_reg:
        app.setup_registry()
    
    if args.install_repo:
        app.install_repository(args.install_repo)
    
    if args.update_repo:
        app.update_repository(args.update_repo)
    
    if args.list_repos:
        app.list_installed_repositories()
    
    if args.system_info:
        app.show_system_info()
    
    # If no arguments, show help
    if len(sys.argv) == 1:
        app.show_system_info()
        print("\n" + "="*50)
        print("Available commands:")
        print("  --setup-env             Setup environment")
        print("  --setup-reg             Register path in registry")
        print("  --change-path           Change installation path")
        print("  --install-repo <url>    Install repository")
        print("  --update-repo <name>    Обновить репозиторий")
        print("  --list-repos            Show repositories")
        print("  --system-info           System information")
        print("  --install-path <path>   Installation path")
        print("="*50)

if __name__ == "__main__":
    main()
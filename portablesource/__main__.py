#!/usr/bin/env python3
"""
PortableSource Main Application
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

from portablesource.config import logger, ConfigManager
from portablesource.envs_manager import MicromambaManager
from portablesource.repository_installer import RepositoryInstaller
from portablesource.utils import (
    load_install_path_from_registry,
    save_install_path_to_registry,
    validate_and_get_path,
    create_directory_structure,
    change_installation_path,
    show_system_info,
    install_msvc_build_tools,
    check_msvc_build_tools_installed
)

class PortableSourceApp:
    """Main PortableSource Application"""
    
    def __init__(self):
        self.install_path: Optional[Path] = None
        self.config_manager: Optional[ConfigManager] = None
        self.environment_manager: Optional[MicromambaManager] = None
        self.repository_installer: Optional[RepositoryInstaller] = None
        
    def initialize(self, install_path: Optional[str] = None):
        """Initialize the application"""
        # Determine installation path
        if install_path:
            self.install_path = Path(install_path).resolve()
            save_install_path_to_registry(self.install_path)
        else:
            self.install_path = self._get_installation_path()
        
        # Create directory structure
        create_directory_structure(self.install_path)
        
        # Initialize config manager with proper config path
        config_path = self.install_path / "portablesource_config.json"
        self.config_manager = ConfigManager(config_path)
        self.config_manager.load_config()
        # Set install path in config if not already set
        if not self.config_manager.config.install_path:
            self.config_manager.config.install_path = str(self.install_path)
            self.config_manager.save_config()
        
        # Initialize managers
        self.environment_manager = MicromambaManager(self.install_path, self.config_manager)
        self.repository_installer = RepositoryInstaller(self.install_path, config_manager=self.config_manager)
    
    def _get_installation_path(self) -> Path:
        """Request installation path from user"""
        registry_path = load_install_path_from_registry()
        
        if registry_path:
            return registry_path
        
        # If no path in registry, request from user
        print("\n" + "="*60)
        print("PORTABLESOURCE INSTALLATION PATH SETUP")
        print("="*60)
        
        default_path = Path("C:/PortableSource")
        print(f"\nDefault path will be used: {default_path}")
        print("\nYou can:")
        print("1. Press Enter to use the default path")
        print("2. Enter your own installation path")
        
        user_input = input("\nEnter installation path (or Enter for default): ").strip()
        
        if not user_input:
            chosen_path = default_path
        else:
            chosen_path = validate_and_get_path(user_input)
        
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
        
        save_install_path_to_registry(chosen_path)
        return chosen_path
    
    def setup_environment(self):
        """Setup environment (Micromamba + base environment)"""
        if not self.environment_manager:
            logger.error("Environment manager not initialized")
            return False
        
        return self.environment_manager.setup_environment()
    
    def install_repository(self, repo_url_or_name: str) -> bool:
        """Install repository"""
        if not self.repository_installer:
            logger.error("Repository installer not initialized")
            return False
        
        # Repositories should be installed in the repos subdirectory
        if not self.install_path:
            logger.error("install path is none")
            return False

        repos_path = self.install_path / "repos"
        return self.repository_installer.install_repository(repo_url_or_name, repos_path)
    
    def update_repository(self, repo_name: str) -> bool:
        """Update repository"""
        if not self.repository_installer:
            logger.error("Repository installer not initialized")
            return False
        
        return self.repository_installer.update_repository(repo_name)
    
    def list_installed_repositories(self):
        """List installed repositories"""
        if not self.repository_installer:
            logger.error("Repository installer not initialized")
            return []
        
        return self.repository_installer.list_installed_repositories()

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="PortableSource - Portable AI/ML Environment")
    parser.add_argument("--install-path", type=str, help="Installation path")
    parser.add_argument("--setup-env", action="store_true", help="Setup environment (Micromamba)")
    parser.add_argument("--setup-reg", action="store_true", help="Register installation path in registry")
    parser.add_argument("--change-path", action="store_true", help="Change installation path")
    parser.add_argument("--install-repo", type=str, help="Install repository")
    parser.add_argument("--update-repo", type=str, help="Update repository")
    parser.add_argument("--list-repos", action="store_true", help="Show installed repositories")
    parser.add_argument("--system-info", action="store_true", help="Show system information")
    parser.add_argument("--check-env", action="store_true", help="Check environment status and tools")
    parser.add_argument("--install-msvc", action="store_true", help="Install MSVC Build Tools")
    parser.add_argument("--check-msvc", action="store_true", help="Check MSVC Build Tools installation")
    
    args = parser.parse_args()
    
    # Create application
    app = PortableSourceApp()
    
    # For path change command, full initialization is not needed
    if args.change_path:
        change_installation_path()
        return
    
    # Initialize for other commands
    app.initialize(args.install_path)
    
    # Execute commands
    if args.setup_env:
        app.setup_environment()
    
    if args.setup_reg:
        if app.install_path:
            save_install_path_to_registry(app.install_path)
        else:
            logger.error("Installation path not defined")
    
    if args.install_repo:
        success = app.install_repository(args.install_repo)
        if success:
            logger.info(f"✅ Repository '{args.install_repo}' installed successfully")
        else:
            logger.error(f"❌ Failed to install repository '{args.install_repo}'")
            sys.exit(1)
    
    if args.update_repo:
        app.update_repository(args.update_repo)
    
    if args.list_repos:
        repos = app.list_installed_repositories()
        logger.info(f"Installed repositories: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"  * {repo['name']} {launcher_status}")
    
    if args.system_info:
        if app.install_path is None:
            logger.error("Installation path not initialized")
            return
        check_micromamba_func = app.environment_manager.check_micromamba_availability if app.environment_manager else None
        show_system_info(app.install_path, app.environment_manager, check_micromamba_func)
    
    if args.check_env:
        if app.install_path is None:
            logger.error("Installation path not initialized")
            return
        if not app.environment_manager:
            logger.error("Environment manager not initialized")
            return
        
        logger.info("Checking environment status...")
        status = app.environment_manager.check_environment_status()
        
        print("\n" + "="*60)
        print("ENVIRONMENT STATUS")
        print("="*60)
        print(f"Environment exists: {'✅' if status['environment_exists'] else '❌'}")
        print(f"Setup completed: {'YES' if status['environment_setup_completed'] else 'NO'}")
        print(f"Overall status: {status['overall_status']}")
        
        if status['environment_exists']:
            print("\nTools status:")
            for tool_name, tool_status in status['tools_status'].items():
                if tool_status['working']:
                    print(f"  ✅ {tool_name}: {tool_status['version']}")
                else:
                    print(f"  ❌ {tool_name}: {tool_status['error']}")
                    if 'stderr' in tool_status and tool_status['stderr']:
                        print(f"     Error details: {tool_status['stderr']}")
        
        print("="*60)
    
    if args.install_msvc:
        if app.install_path is None:
            logger.error("Installation path not initialized")
            return
        install_msvc_build_tools(app.install_path)
    
    if args.check_msvc:
        is_installed = check_msvc_build_tools_installed()
        status = "✅ Installed" if is_installed else "❌ Not installed"
        logger.info(f"MSVC Build Tools: {status}")
    
    # If no arguments, show help
    if len(sys.argv) == 1:
        if app.install_path is None:
            logger.error("Installation path not initialized")
            return
        check_micromamba_func = app.environment_manager.check_micromamba_availability if app.environment_manager else None
        show_system_info(app.install_path, app.environment_manager, check_micromamba_func)
        
        # Show repositories
        repos = app.list_installed_repositories()
        logger.info(f"  - Installed repositories: {len(repos)}")
        for repo in repos:
            launcher_status = "✅" if repo['has_launcher'] else "❌"
            logger.info(f"    * {repo['name']} {launcher_status}")
        print("\n" + "="*50)
        print("Available commands:")
        print("  --setup-env             Setup environment")
        print("  --setup-reg             Register path in registry")
        print("  --change-path           Change installation path")
        print("  --install-repo <url>    Install repository")
        print("  --update-repo <name>    Update repository")
        print("  --list-repos            Show repositories")
        print("  --system-info           System information")
        print("  --check-env             Check environment status")
        print("  --install-path <path>   Installation path")
        print("  --install-msvc          Install MSVC Build Tools")
        print("  --check-msvc            Check MSVC Build Tools")
        print("="*50)

if __name__ == "__main__":
    main()
"""Utility functions for PortableSource application."""

import winreg
import sys
from pathlib import Path
from typing import Optional

from portablesource.config import logger
from portablesource.get_gpu import GPUDetector


def save_install_path_to_registry(install_path: Path) -> bool:
    """Save installation path to Windows registry.
    
    Args:
        install_path: Path to save in registry
        
    Returns:
        True if successful, False otherwise
    """
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
        winreg.SetValueEx(key, "InstallPath", 0, winreg.REG_SZ, str(install_path))
        winreg.CloseKey(key)
        return True
    except Exception as e:
        logger.error(f"Failed to save install path to registry: {e}")
        return False


def load_install_path_from_registry() -> Optional[Path]:
    """Load installation path from Windows registry.
    
    Returns:
        Path if found in registry, None otherwise
    """
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\PortableSource")
        install_path, _ = winreg.QueryValueEx(key, "InstallPath")
        winreg.CloseKey(key)
        return Path(install_path)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.error(f"Failed to load install path from registry: {e}")
        return None


def validate_and_get_path(path_str: str) -> Path:
    """Validate and convert string to Path object.
    
    Args:
        path_str: String representation of path
        
    Returns:
        Validated Path object
    """
    path = Path(path_str).resolve()
    
    # Check if path is valid
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Invalid path {path}: {e}")
        raise ValueError(f"Invalid installation path: {path}")
    
    return path


def create_directory_structure(install_path: Path) -> None:
    """Create necessary directory structure.
    
    Args:
        install_path: Base installation path
    """
    directories = [
        install_path / "micromamba",
        install_path / "ps_env", 
        install_path / "repos",
        install_path / "envs"
    ]
    
    for directory in directories:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Created directory: {directory}")
        except Exception as e:
            logger.error(f"Failed to create directory {directory}: {e}")
            raise


def change_installation_path() -> bool:
    """Change installation path with user interaction.
    
    Returns:
        True if path was successfully changed, False otherwise
    """
    print("\n" + "="*60)
    print("CHANGE PORTABLESOURCE INSTALLATION PATH")
    print("="*60)
    
    # Show current path
    current_path = load_install_path_from_registry()
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
        try:
            new_path = validate_and_get_path(user_input)
        except ValueError as e:
            logger.error(str(e))
            return False
    
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
    success = save_install_path_to_registry(new_path)
    
    if success:
        logger.info("✅ Installation path successfully changed")
        logger.info(f"New path: {new_path}")
        logger.info("Restart PortableSource to apply changes")
    else:
        logger.error("❌ Failed to save new path to registry")
    
    return success


def show_system_info(install_path: Path, environment_manager=None, check_micromamba_func=None) -> None:
    """Show system information.
    
    Args:
        install_path: Installation path
        environment_manager: Environment manager instance (optional)
        check_micromamba_func: Function to check micromamba availability (optional)
    """
    gpu_detector = GPUDetector()
    
    logger.info("PortableSource - System Information:")
    logger.info(f"  - Installation path: {install_path}")
    logger.info(f"  - Operating system: {gpu_detector.system}")
    
    # Directory structure
    logger.info("  - Directory structure:")
    logger.info(f"    * {install_path}/micromamba")
    logger.info(f"    * {install_path}/ps_env")
    logger.info(f"    * {install_path}/repos")
    logger.info(f"    * {install_path}/envs")
    
    gpu_info = gpu_detector.get_gpu_info()
    if gpu_info:
        logger.info(f"  - GPU: {gpu_info[0].name}")
        logger.info(f"  - GPU type: {gpu_info[0].gpu_type.value}")
        if gpu_info[0].cuda_version:
            logger.info(f"  - CUDA version: {gpu_info[0].cuda_version.value}")
    
    # Micromamba status
    if check_micromamba_func:
        micromamba_status = "Installed" if check_micromamba_func() else "Not installed"
        logger.info(f"  - Micromamba: {micromamba_status}")
    
    # Base environment status
    if environment_manager:
        env_info = environment_manager.get_environment_info()
        base_env_status = "Created" if env_info["base_env_exists"] else "Not created"
        logger.info(f"  - Base environment (ps_env): {base_env_status}")
        if env_info["base_env_python"]:
            logger.info(f"    * Python: {env_info['base_env_python']}")
        if env_info["base_env_uv"]:
            logger.info(f"    * UV: {env_info['base_env_uv']}")
    
    # Repository status - this will be handled by the caller
    # as it requires repository manager functionality
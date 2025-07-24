"""Utility functions for PortableSource application."""

import subprocess
import winreg
from pathlib import Path
from typing import Optional
import os

from portablesource.config import logger
from portablesource.config import ConfigManager

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
    # Load config to get GPU information
    config_path = install_path / "portablesource_config.json"
    config_manager = ConfigManager(config_path)
    config_manager.load_config()
    
    # Determine path separator based on OS
    if os.name == 'nt':
        slash = "\\"
        os_name = "Windows"
    else:
        slash = "/"
        os_name = "Linux/macOS"
    
    logger.info("PortableSource - System Information:")
    logger.info(f"  - Installation path: {install_path}")
    logger.info(f"  - Operating system: {os_name}")
    
    # Directory structure
    logger.info("  - Directory structure:")
    logger.info(f"    * {install_path}{slash}micromamba")
    logger.info(f"    * {install_path}{slash}ps_env")
    logger.info(f"    * {install_path}{slash}repos")
    logger.info(f"    * {install_path}{slash}envs")
    
    # GPU information from config
    gpu_config = config_manager.config.gpu_config
    if gpu_config and gpu_config.name:
        logger.info(f"  - GPU: {gpu_config.name}")
        # Determine GPU type from name
        gpu_name_lower = gpu_config.name.lower()
        if "nvidia" in gpu_name_lower or "geforce" in gpu_name_lower or "rtx" in gpu_name_lower or "gtx" in gpu_name_lower:
            gpu_type = "NVIDIA"
        elif "amd" in gpu_name_lower or "radeon" in gpu_name_lower:
            gpu_type = "AMD"
        elif "intel" in gpu_name_lower:
            gpu_type = "Intel"
        else:
            gpu_type = "Unknown"
        logger.info(f"  - GPU type: {gpu_type}")
        if gpu_config.cuda_version:
            logger.info(f"  - CUDA version: {gpu_config.cuda_version}")
    else:
        logger.info("  - GPU: Not configured")
    
    # Micromamba status
    micromamba_installed = False
    if check_micromamba_func:
        micromamba_installed = check_micromamba_func()
        micromamba_status = "Installed" if micromamba_installed else "Not installed"
        logger.info(f"  - Micromamba: {micromamba_status}")
    
    # Base environment status (only if micromamba is installed)
    if environment_manager and micromamba_installed:
        env_info = environment_manager.get_environment_info()
        base_env_status = "Created" if env_info["base_env_exists"] else "Not created"
        logger.info(f"  - Base environment (ps_env): {base_env_status}")
        if env_info["base_env_python"]:
            logger.info(f"    * Python: {env_info['base_env_python']}")
        if env_info["base_env_uv"]:
            logger.info(f"    * UV: {env_info['base_env_uv']}")
    elif environment_manager and not micromamba_installed:
        logger.info("  - Base environment (ps_env): Not available (Micromamba not installed)")
    
    # MSVC Build Tools status
    msvc_status = "Installed" if check_msvc_build_tools_installed() else "Not installed"
    logger.info(f"  - MSVC Build Tools: {msvc_status}")
    
    # Repository status - this will be handled by the caller
    # as it requires repository manager functionality


def install_msvc_build_tools(install_path: Path) -> bool:
    """Install Microsoft Visual Studio Build Tools.
    
    Args:
        install_path: Base installation path
        
    Returns:
        True if installation was successful, False otherwise
    """
    try:
        # Get MSVC Build Tools configuration
        config_manager = ConfigManager()
        msvc_url, msvc_args = config_manager.msvc_bt_config()
        
        logger.info("Starting MSVC Build Tools installation...")
        logger.info(f"Download URL: {msvc_url}")
        
        # Create temp directory for installer
        temp_dir = install_path / "tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        installer_path = temp_dir / "vs_buildtools.exe"
        
        # Download installer
        logger.info("Downloading MSVC Build Tools installer...")
        import urllib.request
        urllib.request.urlretrieve(msvc_url, installer_path)
        
        if not installer_path.exists():
            logger.error("Failed to download MSVC Build Tools installer")
            return False
        
        logger.info(f"Installer downloaded to: {installer_path}")
        
        # Run installer with arguments
        logger.info("Running MSVC Build Tools installer...")
        logger.info("This may take several minutes. Please wait...")
        
        cmd = f'"{installer_path}"{msvc_args}'
        
        # Run the installer
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour timeout
        )
        
        # Clean up installer
        try:
            installer_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to remove installer: {e}")
        
        if result.returncode == 0:
            logger.info("✅ MSVC Build Tools installed successfully")
            return True
        else:
            logger.error(f"❌ MSVC Build Tools installation failed with exit code: {result.returncode}")
            if result.stderr:
                logger.error(f"Error output: {result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        logger.error("❌ MSVC Build Tools installation timed out")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to install MSVC Build Tools: {e}")
        return False


def check_msvc_build_tools_installed() -> bool:
    """Check if MSVC Build Tools are installed.
    
    Returns:
        True if MSVC Build Tools are installed, False otherwise
    """
    try:
        # Check for common MSVC installation paths
        common_paths = [
            Path("C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools"),
            Path("C:/Program Files/Microsoft Visual Studio/2022/BuildTools"),
            Path("C:/Program Files (x86)/Microsoft Visual Studio/2019/BuildTools"),
            Path("C:/Program Files/Microsoft Visual Studio/2019/BuildTools")
        ]
        
        for path in common_paths:
            if path.exists():
                # Check for MSBuild.exe as indicator
                msbuild_path = path / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
                if msbuild_path.exists():
                    logger.debug(f"Found MSVC Build Tools at: {path}")
                    return True
        
        # Alternative check using registry for Visual Studio installations
        try:
            import winreg
            # Check for Visual Studio instances in registry
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\VisualStudio\Setup\Instances"
            )
            
            # Enumerate through all instances
            i = 0
            while True:
                try:
                    instance_key_name = winreg.EnumKey(key, i)
                    instance_key = winreg.OpenKey(key, instance_key_name)
                    
                    # Check if this instance has Build Tools
                    try:
                        product_id, _ = winreg.QueryValueEx(instance_key, "ProductId")
                        if "BuildTools" in product_id:
                            winreg.CloseKey(instance_key)
                            winreg.CloseKey(key)
                            return True
                    except FileNotFoundError:
                        pass
                    
                    winreg.CloseKey(instance_key)
                    i += 1
                except OSError:
                    break
            
            winreg.CloseKey(key)
        except FileNotFoundError:
            pass
        
        return False
        
    except Exception as e:
        logger.debug(f"Error checking MSVC Build Tools: {e}")
        return False

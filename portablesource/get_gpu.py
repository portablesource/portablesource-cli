import subprocess
import platform

def get_gpu():
    system = platform.system()
    if system == "Windows":
        try:
            output = subprocess.check_output(["wmic", "path", "win32_VideoController", "get", "name"]).decode("utf-8")
            gpus = [line.strip() for line in output.split('\n') if line.strip() != "" and line.strip() != "Name"]
        except:
            gpus = []
    elif system == "Linux":
        try:
            output = subprocess.check_output(["lspci"]).decode("utf-8")
            gpus = [line for line in output.split('\n') if "VGA" in line or "3D" in line]
        except:
            gpus = []
    else:
                gpus = []

    if gpus:
        for gpu in gpus:
            if "NVIDIA" in gpu.upper():
                gpu = "NVIDIA"
            else:
                gpu = "DIRECTML"
    else:
        gpu = None

    return gpu

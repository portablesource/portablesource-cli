from .install_from_source import installed
from .downloader import download_extract_and_cleanup

if __name__ == "__main__":
    download_extract_and_cleanup()
    installed()
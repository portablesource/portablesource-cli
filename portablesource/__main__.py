from .install_from_source import installed
from .downloader import download_extract_and_cleanup

def main():
    download_extract_and_cleanup()
    installed()

if __name__ == "__main__":
    main()
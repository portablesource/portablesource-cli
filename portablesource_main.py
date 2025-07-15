#!/usr/bin/env python3
"""
PortableSource - обертка для запуска главного модуля
"""

import sys
import os
from pathlib import Path

# Добавляем путь к портабельному источнику
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Импортируем и запускаем главный модуль
if __name__ == "__main__":
    from portablesource.__main__ import main
    main() 
#!/bin/bash

# Video Creator Setup Script
# Настройка виртуального окружения и установка зависимостей

set -e  # Остановить скрипт при ошибке

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Функции для вывода
print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}  Video Creator Setup${NC}"
    echo -e "${BLUE}================================${NC}"
}

print_step() {
    echo -e "${GREEN}[STEP]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

# Проверка системы
check_system() {
    print_step "Проверка системных требований..."

    # Проверка Python
    if ! command -v python3 &> /dev/null; then
        print_error "Python 3 не найден. Установите Python 3.11+"
        exit 1
    fi

    PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d'.' -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d'.' -f2)

    if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 11 ]); then
        print_error "Требуется Python 3.11+. Текущая версия: $PYTHON_VERSION"
        exit 1
    fi
    print_success "Python $PYTHON_VERSION найден"

    # Проверка FFmpeg
    if ! command -v ffmpeg &> /dev/null; then
        print_warning "FFmpeg не найден. Устанавливаем..."
        sudo apt update
        sudo apt install -y ffmpeg
    fi
    FFMPEG_VERSION=$(ffmpeg -version | head -1 | cut -d' ' -f3)
    print_success "FFmpeg $FFMPEG_VERSION найден"
}

# Создание виртуального окружения
create_venv() {
    print_step "Создание виртуального окружения..."

    if [ -d "venv" ]; then
        print_warning "Виртуальное окружение уже существует"
        read -p "Удалить существующее и создать новое? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf venv
        else
            print_success "Используем существующее виртуальное окружение"
            return
        fi
    fi

    python3 -m venv venv
    print_success "Виртуальное окружение создано"
}

# Активация виртуального окружения и установка зависимостей
install_dependencies() {
    print_step "Активация виртуального окружения и установка зависимостей..."

    # Активация
    source venv/bin/activate

    # Обновление pip
    pip install --upgrade pip

    # Установка зависимостей
    if [ -f "requirements.txt" ]; then
        pip install -r requirements.txt
        print_success "Зависимости установлены"
    else
        print_error "Файл requirements.txt не найден"
        exit 1
    fi
}

# Создание папок
create_directories() {
    print_step "Создание необходимых папок..."

    mkdir -p videos
    mkdir -p logs

    print_success "Папки созданы"
}

# Проверка установки
test_installation() {
    print_step "Проверка установки..."

    source venv/bin/activate

    # Проверка импортов
    python3 -c "
import yaml
import sys
from pathlib import Path

print('PyYAML: OK')

try:
    import moviepy
    print('MoviePy: OK')
except ImportError:
    print('MoviePy: Не установлен (опционально)')

print('Все основные зависимости: OK')
"

    print_success "Установка проверена"
}

# Создание примера конфигурации
create_example_config() {
    print_step "Создание примера конфигурации..."

    if [ ! -f "video_creator_config_example.yaml" ]; then
        cp video_creator_config.yaml video_creator_config_example.yaml
        print_success "Пример конфигурации создан"
    else
        print_success "Пример конфигурации уже существует"
    fi
}

# Показ инструкций по использованию
show_usage() {
    echo
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE}  Video Creator готов к работе!${NC}"
    echo -e "${BLUE}================================${NC}"
    echo
    echo "Для запуска:"
    echo "  source venv/bin/activate"
    echo "  python video_creator.py --config video_creator_config.yaml"
    echo
    echo "Для тестового запуска (без создания видео):"
    echo "  python video_creator.py --dry-run --config video_creator_config.yaml"
    echo
    echo "Для просмотра справки:"
    echo "  python video_creator.py --help"
    echo
    echo "Конфигурационный файл: video_creator_config.yaml"
    echo "Логи: video_creation.log"
    echo "Выходные видео: videos/"
}

# Основная функция
main() {
    print_header

    check_system
    create_venv
    install_dependencies
    create_directories
    test_installation
    create_example_config
    show_usage

    print_success "Настройка завершена успешно!"
}

# Запуск
main "$@"

#!/usr/bin/env python3
"""
Bird Detector All-in-One v5.5
Исправленное именование фотографий с уникальным счетчиком

ОТЛИЧИЯ ОТ v5.4:
- Исправлено именование фотографий - добавлен глобальный счетчик фото
- Каждая фотография имеет уникальное имя вместо статичного "count1"
- Счетчик photo_count сохраняется в течение сессии
"""

import os
import sys
import time
import cv2
import numpy as np
import threading
import yaml
import re
from pathlib import Path
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

# GStreamer и Hailo
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib
import hailo
import hailo_platform
from hailo_apps.hailo_app_python.core.common.buffer_utils import get_numpy_from_buffer
from hailo_apps.hailo_app_python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.hailo_app_python.apps.detection.detection_pipeline import GStreamerDetectionApp

Gst.init(None)


def get_caps_info(caps):
    """Упрощенное получение параметров кадра из caps (только Метод 2, без отладочных логов)."""
    if not caps:
        return None, None, None

    try:
        caps_str = caps.to_string()

        # Парсинг через регулярные выражения (без отладочных print)
        format_match = re.search(r'format=\(string\)"([^"]+)"', caps_str)
        if not format_match:
            format_match = re.search(r'format="([^"]+)"', caps_str)

        format_str = format_match.group(1) if format_match else "RGB"

        # Ищем ширину и высоту
        width_match = re.search(r'width=\(int\)(\d+)', caps_str)
        height_match = re.search(r'height=\(int\)(\d+)', caps_str)

        width = int(width_match.group(1)) if width_match else 1280
        height = int(height_match.group(1)) if height_match else 720

        return format_str, width, height

    except Exception as e:
        # Тихая обработка ошибок без вывода в консоль
        pass

    # Возвращаем значения по умолчанию
    return "RGB", 1280, 720


# ==============================================================================
# КЛАССЫ ПОДСИСТЕМ v5.5
# ==============================================================================

class ConfigManager:
    """Менеджер конфигурации YAML."""
    def __init__(self, config_path=None):
        self.config = self.load_config(config_path)
        print("✅ Конфигурация v5.5 загружена")

    def load_config(self, config_path=None):
        if config_path is None:
            current_dir = Path(__file__).resolve().parent
            config_path = current_dir / "bird_counter_config_v5.yaml"

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            print(f"📄 Конфигурация загружена из: {config_path}")
            return config
        except Exception as e:
            print(f"❌ Ошибка загрузки конфигурации: {e}")
            return self.get_default_config()

    def get_default_config(self):
        return {
            'bird_tracking': {
                'enable_tracking': True,
                'bird_timeout_seconds': 30,
                'enable_visit_counter': True,
                'min_time_between_visits_seconds': 10
            },
            'logging': {
                'enable_text_log': True,
                'log_format': 'markdown',
                'console_output_mode': 'minimal'  # Изменено на minimal
            },
            'frame_saving': {'enable_photo_save': True, 'min_save_interval_seconds': 5},
            'detection': {'target_classes': ['bird'], 'min_confidence': 0.3},
            'web_streams': {'camera_stream_port': 8080, 'detection_stream_port': 8091}
        }

class BirdTracker:
    """Умный трекер уникальных птиц и посещений кормушки."""
    def __init__(self, config):
        self.config = config
        self.enable_tracking = config['bird_tracking']['enable_tracking']
        self.enable_visit_counter = config['bird_tracking'].get('enable_visit_counter', True)
        self.bird_timeout = config['bird_tracking']['bird_timeout_seconds']
        self.min_time_between_visits = config['bird_tracking'].get('min_time_between_visits_seconds', 10)

        # Состояние трекинга
        self.active_birds = {}  # {bird_id: last_seen_time}
        self.total_unique_birds = 0  # Уникальные птицы за сессию
        self.total_feeding_visits = 0  # Посещения кормушки (инциденты кормления)
        self.last_birds_on_frame = 0  # Для логики посещений
        self.last_bird_absence_time = 0  # Время последнего исчезновения птицы

        # Новое в v5.4: предыдущие значения для отслеживания изменений
        self.prev_total_unique = 0
        self.prev_total_feeding_visits = 0
        self.new_visit_happened = False  # Флаг нового посещения для логирования

        print("🐦 BirdTracker v5.5 инициализирован")
        print(f"   - Трекинг уникальных: {'включен' if self.enable_tracking else 'выключен'}")
        print(f"   - Подсчет посещений: {'включен' if self.enable_visit_counter else 'выключен'}")
        print(f"   - Таймаут птиц: {self.bird_timeout} сек")
        print(f"   - Мин. время между посещениями: {self.min_time_between_visits} сек")

    def update_feeding_visits(self, birds_on_frame, current_time, console_mode='all'):
        """
        Улучшенная логика подсчета посещений - учитывает "мигание" детектора.
        Сообщения в консоль зависят от режима console_mode.
        """
        if not self.enable_visit_counter:
            return

        # Сбрасываем флаг нового посещения
        self.new_visit_happened = False

        # Если птица появилась в кадре
        if birds_on_frame > 0:
            # Если ранее птиц не было (last_birds_on_frame == 0)
            if self.last_birds_on_frame == 0:
                # Проверяем, прошло ли достаточно времени с момента последнего исчезновения
                if self.last_bird_absence_time == 0:
                    # Первое посещение в сессии
                    self.total_feeding_visits += 1
                    self.new_visit_happened = True
                    if console_mode in ['all', 'changes_only']:
                        print(f"🐦 Первое посещение кормушки #{self.total_feeding_visits}")
                        print(f"   Время: {datetime.fromtimestamp(current_time).strftime('%H:%M:%S')}")
                else:
                    # Прошло ли достаточно времени?
                    time_since_absence = current_time - self.last_bird_absence_time
                    if time_since_absence >= self.min_time_between_visits:
                        # Достаточно долго не было птиц - новое посещение
                        self.total_feeding_visits += 1
                        self.new_visit_happened = True
                        if console_mode in ['all', 'changes_only']:
                            print(f"🐦 Новое посещение кормушки #{self.total_feeding_visits}")
                            print(f"   Прошло времени: {time_since_absence:.1f} сек")
                            print(f"   Время: {datetime.fromtimestamp(current_time).strftime('%H:%M:%S')}")
                    else:
                        # Недостаточно времени - это продолжение предыдущего посещения
                        if console_mode == 'all':
                            print(f"🐦 Продолжение посещения #{self.total_feeding_visits} (мигание детектора)")
            # Если птиц стало больше (групповое кормление)
            elif birds_on_frame > self.last_birds_on_frame and birds_on_frame > 1:
                self.total_feeding_visits += 1
                self.new_visit_happened = True
                if console_mode in ['all', 'changes_only']:
                    print(f"🐦 Групповое посещение кормушки #{self.total_feeding_visits}")
                    print(f"   Птиц в группе: {birds_on_frame}")

        # Если птиц не стало - фиксируем время исчезновения
        elif birds_on_frame == 0 and self.last_birds_on_frame > 0:
            self.last_bird_absence_time = current_time
            if console_mode == 'all':
                print(f"🐦 Птицы исчезли из кадра (время: {datetime.fromtimestamp(current_time).strftime('%H:%M:%S')})")

        self.last_birds_on_frame = birds_on_frame

    def update_birds(self, detections, current_time, console_mode='all'):
        """Обновление состояния птиц с правильной логикой."""
        birds_on_frame = len(detections)

        # Сначала обновляем счетчик посещений
        self.update_feeding_visits(birds_on_frame, current_time, console_mode)

        if not self.enable_tracking:
            self.current_birds_on_frame = birds_on_frame
            return birds_on_frame, 0

        # Удаление устаревших птиц
        expired_birds = []
        for bird_id, last_seen in self.active_birds.items():
            if current_time - last_seen > self.bird_timeout:
                expired_birds.append(bird_id)

        for bird_id in expired_birds:
            del self.active_birds[bird_id]

        # Обработка текущих детекций
        new_birds = 0

        for detection in detections:
            if not self.active_birds:
                # Первая птица
                self.total_unique_birds += 1
                bird_id = f"bird_{self.total_unique_birds}"
                self.active_birds[bird_id] = current_time
                new_birds += 1
            else:
                # Обновляем существующую птицу
                existing_bird = list(self.active_birds.keys())[0]
                self.active_birds[existing_bird] = current_time

        self.current_birds_on_frame = birds_on_frame
        return birds_on_frame, new_birds

    def get_stats(self):
        return {
            'total_unique': self.total_unique_birds,
            'total_feeding_visits': self.total_feeding_visits,
            'current_active': len(self.active_birds),
            'current_on_frame': self.current_birds_on_frame,
            'last_absence_time': self.last_bird_absence_time
        }

    def has_changes(self):
        """Проверяет, изменились ли счетчики с последнего вызова."""
        current_stats = self.get_stats()
        changed = (current_stats['total_unique'] != self.prev_total_unique or
                  current_stats['total_feeding_visits'] != self.prev_total_feeding_visits)

        # Обновляем предыдущие значения
        self.prev_total_unique = current_stats['total_unique']
        self.prev_total_feeding_visits = current_stats['total_feeding_visits']

        return changed

class LogManager:
    """Менеджер логирования с организацией структуры и дополнительным логом событий."""
    def __init__(self, config):
        self.config = config
        self.enable_text_log = config['logging']['enable_text_log']
        self.log_format = config['logging']['log_format']
        self.console_output_mode = config['logging'].get('console_output_mode', 'all')

        # Параметры мониторинга температуры
        self.enable_temperature_logging = config['system_monitoring']['enable_temperature_logging']
        self.temperature_log_interval = config['system_monitoring']['temperature_log_interval_minutes'] * 60  # в секунды

        # Параметры отладки производительности
        self.enable_performance_log = config['performance_debug']['enable_performance_log']

        # Параметры диагностики запуска
        self.enable_startup_log = config['startup_diagnostics']['enable_startup_log']

        if self.enable_text_log:
            self.setup_logging()
            print("📝 LogManager v5.5 инициализирован")
            print(f"   - Режим консоли: {self.console_output_mode}")
            print(f"   - Лог v5.1: {self.log_file_path}")
            print(f"   - Лог v2.0: {self.add_logs_dir / 'bird_counter_log.md'}")
            print(f"   - Лог событий: {self.events_log_path}")

            # Инициализация лога температуры
            if self.enable_temperature_logging:
                self.setup_temperature_logging(self.config)
                print(f"   - Лог температуры: {self.temperature_log_path}")
                print(f"   - Интервал температуры: {self.temperature_log_interval} сек")

    def setup_logging(self):
        """Создание структуры логирования с организацией."""
        logs_base_path = Path(self.config['logging']['logs_path'])
        logs_base_path.mkdir(exist_ok=True)

        # Папка сессии с timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.session_folder = logs_base_path / f"logs_{timestamp}"
        self.session_folder.mkdir(exist_ok=True)

        # Создаем папку для дополнительных логов
        self.add_logs_dir = self.session_folder / "add_logs"
        self.add_logs_dir.mkdir(exist_ok=True)

        # Файлы логов
        filename_pattern = self.config['logging']['log_filename_pattern']
        filename = filename_pattern.format(timestamp=timestamp)
        self.log_file_path = self.session_folder / filename  # Основной лог v5.1

        # Дополнительный лог событий изменения счетчика (в add_logs)
        self.events_log_path = self.add_logs_dir / f"bird_counter_events_{timestamp}.md"

        # Отладочный лог производительности (в add_logs)
        if self.enable_performance_log:
            performance_filename = self.config['performance_debug']['performance_log_filename']
            self.performance_log_path = self.add_logs_dir / performance_filename.format(timestamp=timestamp)

        # Инициализация всех логов
        self.init_log_file()        # Основной лог v5.1
        self.init_log_file_v2()     # Классический v2.0 в add_logs/
        self.init_events_log()      # Лог событий

        # Инициализация отладочного лога производительности
        if self.enable_performance_log:
            self.init_performance_debug_log()

        # Определение способа запуска и создание лога диагностики
        self.launch_method = self.detect_launch_method()
        if self.enable_startup_log:
            self.init_startup_diagnostics_log()
            self.log_startup_diagnostics()

    def init_performance_debug_log(self):
        """Создание отладочного лога производительности."""
        with open(self.performance_log_path, 'w', encoding='utf-8') as f:
            f.write("# Отладочный лог производительности Bird Detector\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("**Цель:** Анализ задержек и проблем производительности\n\n")

            f.write("## Метрики производительности\n\n")
            f.write("| Время | FPS | Темп. CPU | Задержка кадра | Память | Комментарий |\n")
            f.write("|-------|-----|-----------|----------------|--------|-------------|\n")

    def log_performance_debug(self, fps, cpu_temp, frame_delay, memory_usage, comment=""):
        """Логирование отладочной информации о производительности."""
        if not self.enable_performance_log:
            return

        time_str = datetime.now().strftime('%H:%M:%S')
        with open(self.performance_log_path, 'a', encoding='utf-8') as f:
            f.write(f"| {time_str} | {fps:.1f} | {cpu_temp:.1f} | {frame_delay:.3f} | {memory_usage:.1f} | {comment} |\n")

        # Инициализация лога температуры
        if self.enable_temperature_logging:
            self.setup_temperature_logging(self.config)

    def init_log_file(self):
        """Создание структуры основного лога v5.1."""
        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            f.write("# Лог детекции птиц v5.5\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("**Общее количество уникальных птиц:** 0\n\n")
            f.write("## Статистика детекций\n\n")
            f.write("| Время | Птиц на кадре | Активных | Уникальных | Посещений | Координаты |\n")
            f.write("|-------|---------------|----------|------------|-----------|------------|\n")

    def init_log_file_v2(self):
        """Создание структуры лога v2.0 в папке add_logs."""
        log_v2_path = self.add_logs_dir / "bird_counter_log.md"
        with open(log_v2_path, 'w', encoding='utf-8') as f:
            f.write("# Лог подсчета птиц у кормушки v2.0\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("**Общее количество уникальных птиц:** 0\n\n")
            f.write("## Статистика по кадрам\n\n")
            f.write("| Время | Кол-во птиц на кадре | Общее уникальных | Координаты обнаружений |\n")
            f.write("|-------|---------------------|------------------|-------------------------|\n")

    def init_events_log(self):
        """Создание лога событий изменения счетчика."""
        with open(self.events_log_path, 'w', encoding='utf-8') as f:
            f.write("# События изменения счетчика птиц\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("## События\n\n")

    def log_detection(self, timestamp, birds_on_frame, active_birds, total_unique, total_feeding_visits, detections):
        """Логирование в основной лог и v2.0."""
        if not self.enable_text_log:
            return

        # Обновление заголовка основного лога
        self.update_total_count(total_unique)

        # Формирование координат
        coords_str = "; ".join([
            f"bird: ({det['x']:.2f},{det['y']:.2f})"
            for det in detections
        ])

        # Лог в основной файл v5.1
        time_only = timestamp.split('_')[1].replace('-', ':')
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(f"| {time_only} | {birds_on_frame} | {active_birds} | {total_unique} | {total_feeding_visits} | {coords_str} |\n")

        # Лог v2.0 в add_logs/
        if detections:
            self.log_detection_v2(timestamp, birds_on_frame, total_unique, detections)

    def log_detection_v2(self, timestamp, birds_on_frame, total_unique, detections):
        """Логирование в формате v2.0."""
        # Обновление заголовка v2.0
        self.update_total_count_v2(total_unique)

        # Формирование координат в формате v2
        coords_str = "; ".join([
            f"bird: ({det['x']:.2f},{det['y']:.2f})"
            for det in detections
        ])

        # Добавление записи
        time_only = timestamp.split('_')[1].replace('-', ':')
        log_v2_path = self.add_logs_dir / "bird_counter_log.md"
        with open(log_v2_path, 'a', encoding='utf-8') as f:
            f.write(f"| {time_only} | {birds_on_frame} | {total_unique} | {coords_str} |\n")

    def log_counter_event(self, event_type, counter_value, timestamp):
        """Логирование события изменения счетчика."""
        time_str = datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')
        event_text = f"- **{time_str}**: {event_type} #{counter_value}\n"

        with open(self.events_log_path, 'a', encoding='utf-8') as f:
            f.write(event_text)

    def update_total_count(self, total_unique):
        """Обновление общего количества в заголовке основного лога."""
        with open(self.log_file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        old_line = "**Общее количество уникальных птиц:** 0"
        new_line = f"**Общее количество уникальных птиц:** {total_unique}"
        updated_content = content.replace(old_line, new_line)

        with open(self.log_file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

    def update_total_count_v2(self, total_unique):
        """Обновление общего количества в заголовке v2.0."""
        log_v2_path = self.add_logs_dir / "bird_counter_log.md"
        with open(log_v2_path, 'r', encoding='utf-8') as f:
            content = f.read()

        old_line = "**Общее количество уникальных птиц:** 0"
        new_line = f"**Общее количество уникальных птиц:** {total_unique}"
        updated_content = content.replace(old_line, new_line)

        with open(log_v2_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)

    def setup_temperature_logging(self, system_config):
        """Инициализация логирования температуры с параметрами системы."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename_pattern = self.config['system_monitoring']['temperature_log_filename']
        filename = filename_pattern.format(timestamp=timestamp)
        self.temperature_log_path = self.session_folder / filename

        # Получаем основные параметры системы
        hailo_model = system_config['hailo_model']['hef_path']
        model_name = Path(hailo_model).name
        stream_mode = system_config['web_streams'].get('stream_mode', 'both')
        confidence = system_config['detection']['min_confidence']
        target_classes = ', '.join(system_config['detection']['target_classes'])
        console_mode = system_config['logging'].get('console_output_mode', 'all')
        photo_save = "ВКЛЮЧЕНО" if system_config['frame_saving']['enable_photo_save'] else "ОТКЛЮЧЕНО"
        stream_quality = system_config['web_streams']['stream_quality']

        # Создание файла лога температуры с подробной информацией о системе
        with open(self.temperature_log_path, 'w', encoding='utf-8') as f:
            f.write("# Лог температуры процессора и параметров системы\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Интервал логирования:** каждые {self.temperature_log_interval} секунд\n\n")

            f.write("## Параметры системы (влияют на производительность)\n\n")
            f.write(f"- **Модель Hailo:** {model_name}\n")
            f.write(f"- **Режим стрима:** {stream_mode.upper()}\n")
            f.write(f"- **Качество стрима:** {stream_quality}%\n")
            f.write(f"- **Детекция:** классы [{target_classes}], уверенность {confidence}\n")
            f.write(f"- **Консоль:** режим {console_mode.upper()}\n")
            f.write(f"- **Сохранение фото:** {photo_save}\n")
            f.write(f"- **Трекинг:** таймаут {system_config['bird_tracking']['bird_timeout_seconds']}с\n")
            f.write(f"- **Посещения:** мин. интервал {system_config['bird_tracking']['min_time_between_visits_seconds']}с\n\n")

            f.write("## Температура процессора\n\n")
            f.write("| Время          | Температура (°C)    | FPS     |\n")
            f.write("|----------------|---------------------|---------|\n")

    def get_cpu_temperature(self):
        """Получение температуры процессора Raspberry Pi."""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_raw = f.read().strip()
            # Температура в миллиградусах Цельсия
            temp_celsius = float(temp_raw) / 1000.0
            return round(temp_celsius, 1)
        except Exception as e:
            print(f"⚠️ Ошибка получения температуры: {e}")
            return None

    def log_temperature(self, temperature, timestamp, fps=None):
        """Логирование температуры и FPS в файл с выравниванием колонок."""
        if not self.enable_temperature_logging:
            return

        time_str = datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')
        # Логируем FPS только если он > 0, иначе "-"
        fps_str = f"{fps:.1f}" if fps and fps > 0 else "-"

        # Выравнивание колонок по фиксированной ширине
        time_col = f"{time_str:<16}"  # 16 символов, выравнивание влево
        temp_col = f"{temperature:<21}"  # 21 символ, выравнивание влево
        fps_col = f"{fps_str:<9}"  # 9 символов, выравнивание влево

        with open(self.temperature_log_path, 'a', encoding='utf-8') as f:
            f.write(f"| {time_col}| {temp_col}| {fps_col}|\n")

    def detect_launch_method(self):
        """Определение способа запуска приложения (systemd или console)."""
        try:
            # Проверяем переменные окружения systemd
            if os.getenv('INVOCATION_ID') or os.getenv('NOTIFY_SOCKET'):
                return "systemd"

            # Проверяем PPID (родительский процесс)
            ppid = os.getppid()
            with open(f'/proc/{ppid}/cmdline', 'r') as f:
                cmdline = f.read().replace('\x00', ' ')
                if 'systemd' in cmdline.lower():
                    return "systemd"

            # Проверяем, запущен ли процесс от имени пользователя root (systemd часто запускает от root)
            import pwd
            current_user = pwd.getpwuid(os.getuid()).pw_name
            if current_user == 'root':
                # Дополнительная проверка - смотрим на переменные окружения
                systemd_vars = ['MAINPID', 'MANAGERPID', 'LISTEN_PID']
                if any(os.getenv(var) for var in systemd_vars):
                    return "systemd"

            return "console"

        except Exception as e:
            # В случае ошибки возвращаем "unknown"
            return "unknown"

    def init_startup_diagnostics_log(self):
        """Создание лога диагностики запуска."""
        startup_filename = self.config['startup_diagnostics']['startup_log_filename']
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.startup_log_path = self.add_logs_dir / startup_filename.format(timestamp=timestamp)

    def log_startup_diagnostics(self):
        """Логирование диагностической информации о запуске."""
        if not self.enable_startup_log:
            return

        with open(self.startup_log_path, 'w', encoding='utf-8') as f:
            f.write("# Диагностика запуска Bird Detector\n\n")
            f.write(f"**Дата запуска:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Способ запуска:** {self.launch_method.upper()}\n\n")

            # Системная информация
            f.write("## Системная информация\n\n")
            try:
                import platform
                f.write(f"- **ОС:** {platform.system()} {platform.release()}\n")
                f.write(f"- **Python:** {sys.version.split()[0]}\n")
                f.write(f"- **Пользователь:** {os.getenv('USER', 'unknown')}\n")
                f.write(f"- **PID:** {os.getpid()}\n")
                f.write(f"- **PPID:** {os.getppid()}\n")
            except:
                f.write("- **Ошибка получения системной информации**\n")

            # Переменные окружения
            f.write("\n## Переменные окружения\n\n")
            important_vars = ['DISPLAY', 'XAUTHORITY', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS',
                            'PATH', 'PYTHONPATH', 'GST_PLUGIN_PATH', 'LD_LIBRARY_PATH']

            for var in important_vars:
                value = os.getenv(var, 'NOT SET')
                if len(str(value)) > 50:
                    value = str(value)[:47] + '...'
                f.write(f"- **{var}:** {value}\n")

            # Информация о виртуальном окружении
            f.write("\n## Виртуальное окружение\n\n")
            venv_path = os.getenv('VIRTUAL_ENV')
            if venv_path:
                f.write(f"- **Путь к venv:** {venv_path}\n")
                f.write(f"- **Активировано:** Да\n")

                # Проверяем pip list (первые 10 пакетов)
                try:
                    import subprocess
                    result = subprocess.run([sys.executable, '-m', 'pip', 'list', '--format=freeze'],
                                          capture_output=True, text=True, timeout=10)
                    packages = result.stdout.strip().split('\n')[:10]
                    f.write("- **Установленные пакеты (первые 10):**\n")
                    for pkg in packages:
                        f.write(f"  - {pkg}\n")
                except:
                    f.write("- **Не удалось получить список пакетов**\n")
            else:
                f.write("- **Виртуальное окружение не активировано**\n")

            # GStreamer информация
            f.write("\n## GStreamer информация\n\n")
            try:
                import subprocess
                # Версия GStreamer
                gst_ver = subprocess.run(['gst-launch-1.0', '--version'],
                                       capture_output=True, text=True, timeout=5)
                if gst_ver.returncode == 0:
                    version_line = gst_ver.stdout.split('\n')[0]
                    f.write(f"- **Версия GStreamer:** {version_line}\n")
                else:
                    f.write("- **GStreamer не найден**\n")

                # Доступные плагины
                gst_inspect = subprocess.run(['gst-inspect-1.0'],
                                           capture_output=True, text=True, timeout=5)
                plugin_count = len([line for line in gst_inspect.stdout.split('\n')
                                  if line.strip() and not line.startswith('Total')])
                f.write(f"- **Количество плагинов:** {plugin_count}\n")

            except:
                f.write("- **Ошибка получения информации о GStreamer**\n")

            # Hailo информация
            f.write("\n## Hailo информация\n\n")
            try:
                import hailo_platform
                f.write(f"- **Hailo Platform доступен:** Да\n")
                try:
                    import subprocess
                    hailo_ver = subprocess.run(['hailortcli', 'version'],
                                             capture_output=True, text=True, timeout=5)
                    if hailo_ver.returncode == 0:
                        f.write(f"- **HailoRT версия:** {hailo_ver.stdout.strip()}\n")
                    else:
                        f.write("- **hailortcli не найден**\n")
                except:
                    f.write("- **hailortcli не доступен**\n")
            except ImportError:
                f.write("- **Hailo Platform не доступен**\n")

            # Конфигурация
            f.write("\n## Конфигурация\n\n")
            f.write(f"- **Модель HEF:** {self.config['hailo_model']['hef_path']}\n")
            f.write(f"- **Режим стрима:** {self.config['web_streams'].get('stream_mode', 'both')}\n")
            f.write(f"- **Минимальная уверенность:** {self.config['detection']['min_confidence']}\n")
            f.write(f"- **Режим консоли:** {self.config['logging'].get('console_output_mode', 'all')}\n")

            f.write("\n---\n*Автоматически сгенерированная диагностика запуска*\n")

# ==============================================================================
# ОСНОВНОЙ КЛАСС BIRD DETECTOR v5.5
# ==============================================================================

class BirdDetectorV55:
    """
    Bird Detector v5.5 - с исправленным именованием фотографий
    """

    def __init__(self):
        print("=" * 70)
        print("🐦 Bird Detector All-in-One v5.5")
        print("=" * 70)

        # Загрузка конфигурации
        self.config_manager = ConfigManager()
        self.config = self.config_manager.config

        # Инициализация подсистем (сначала для определения способа запуска)
        self.bird_tracker = BirdTracker(self.config)
        self.log_manager = LogManager(self.config)

        # Вывод информации о текущей конфигурации (после инициализации всех подсистем)
        self.print_configuration_info()

    def print_configuration_info(self):
        """Вывод информации о текущей конфигурации жирным шрифтом."""
        print("\n" + "=" * 70)
        print("🔧 ТЕКУЩАЯ КОНФИГУРАЦИЯ СИСТЕМЫ")
        print("=" * 70)

        # Модель Hailo
        hef_model = self.config['hailo_model']['hef_path']
        model_name = Path(hef_model).name
        print(f"🤖 МОДЕЛЬ HAILO: \033[1m{model_name}\033[0m")

        # Режим стрима
        stream_mode = self.config['web_streams'].get('stream_mode', 'both')
        print(f"📺 РЕЖИМ СТРИМА: \033[1m{stream_mode.upper()}\033[0m")

        # Основные параметры детекции
        confidence = self.config['detection']['min_confidence']
        target_classes = ', '.join(self.config['detection']['target_classes'])
        print(f"🎯 ДЕТЕКЦИЯ: \033[1mКлассы: {target_classes} | Уверенность: {confidence}\033[0m")

        # Параметры трекинга
        timeout = self.config['bird_tracking']['bird_timeout_seconds']
        min_time = self.config['bird_tracking']['min_time_between_visits_seconds']
        print(f"🐦 ТРЕКИНГ: \033[1mТаймаут: {timeout}с | Мин. время между посещениями: {min_time}с\033[0m")

        # Режим логирования
        console_mode = self.config['logging'].get('console_output_mode', 'all')
        print(f"📝 КОНСОЛЬ: \033[1mРежим: {console_mode.upper()}\033[0m")

        # Сохранение фото
        photo_save = "ВКЛЮЧЕНО" if self.config['frame_saving']['enable_photo_save'] else "ОТКЛЮЧЕНО"
        interval = self.config['frame_saving']['min_save_interval_seconds']
        print(f"📸 СОХРАНЕНИЕ ФОТО: \033[1m{photo_save} | Интервал: {interval}с\033[0m")

        # Пути к логам
        logs_path = self.config['logging']['logs_path']
        print(f"📁 ЛОГИ: \033[1m{logs_path}\033[0m")

        # Способ запуска (определяется в LogManager)
        launch_method = getattr(self.log_manager, 'launch_method', 'unknown')
        print(f"🚀 СПОСОБ ЗАПУСКА: \033[1m{launch_method.upper()}\033[0m")

        print("=" * 70 + "\n")

        # Параметры стрима из конфига
        stream_mode = self.config['web_streams'].get('stream_mode', 'both')
        if stream_mode == 'camera_only':
            self.enable_camera_stream = True
            self.enable_detection_stream = False
        elif stream_mode == 'detection_only':
            self.enable_camera_stream = False
            self.enable_detection_stream = True
        else:  # 'both'
            self.enable_camera_stream = True
            self.enable_detection_stream = True

        self.camera_port = self.config['web_streams']['camera_stream_port']
        self.detection_port = self.config['web_streams']['detection_stream_port']

        # Параметры модели Hailo из конфига
        self.hef_path = self.config['hailo_model']['hef_path']

        # Параметры детекции
        self.target_classes = self.config['detection']['target_classes']
        self.min_confidence = self.config['detection']['min_confidence']
        self.min_bbox_size = self.config['detection']['min_bbox_size']
        self.max_bbox_size = self.config['detection']['max_bbox_size']

        # Параметры сохранения
        self.enable_photo_save = self.config['frame_saving']['enable_photo_save']
        self.min_save_interval = self.config['frame_saving']['min_save_interval_seconds']
        self.last_save_time = 0
        self.photo_count = 0  # Новое в v5.5: глобальный счетчик фотографий

        # Состояние
        self.frame_count = 0
        self.fps = 0.0
        self.last_frame_time = time.time()

        # Кадры для стримов
        self.camera_frame = None
        self.detection_frame = None

        # Создание callback
        self.callback_obj = self.BirdCallback(self)

        print(f"📷 Камера: Raspberry Pi OV5647")
        print(f"📺 Стримы:")
        if self.enable_camera_stream:
            print(f"   - Чистая камера: http://localhost:{self.camera_port}")
        if self.enable_detection_stream:
            print(f"   - С детекцией: http://localhost:{self.detection_port}")

        # Запуск веб-серверов
        if self.enable_camera_stream:
            self.start_camera_stream_server()
            time.sleep(1)

        if self.enable_detection_stream:
            self.start_detection_stream_server()
            time.sleep(1)

        # Запуск логирования температуры
        if self.config['system_monitoring']['enable_temperature_logging']:
            self.start_temperature_monitoring()

        print("\n🚀 Запуск детекции v5.5...")

    class BirdCallback(app_callback_class):
        """Callback для обработки кадров."""
        def __init__(self, parent):
            super().__init__()
            self.parent = parent

        def process_callback(self, pad, info, user_data):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK

            self.parent.frame_count += 1

            # Расчет FPS в начале
            current_time = time.time()
            if self.parent.frame_count > 1:
                time_diff = current_time - self.parent.last_frame_time
                if time_diff > 0:
                    self.parent.fps = 1.0 / time_diff
            self.parent.last_frame_time = current_time

            try:
                # Получаем параметры кадра
                caps = pad.get_current_caps()
                format_str, width, height = get_caps_info(caps)

                if format_str and width and height:
                    # Получаем кадр
                    frame = get_numpy_from_buffer(buffer, format_str, width, height)

                    if frame is not None:
                        # Детекция
                        roi = hailo.get_roi_from_buffer(buffer)
                        detections_hailo = roi.get_objects_typed(hailo.HAILO_DETECTION)

                        # Фильтрация детекций
                        bird_detections = []
                        for detection in detections_hailo:
                            label = detection.get_label()
                            confidence = detection.get_confidence()

                            if (label in self.parent.target_classes and
                                confidence >= self.parent.min_confidence):

                                bbox = detection.get_bbox()
                                bbox_size = bbox.width() * bbox.height()

                                if (bbox_size >= self.parent.min_bbox_size and
                                    bbox_size <= self.parent.max_bbox_size):

                                    bird_detections.append({
                                        'label': label,
                                        'confidence': confidence,
                                        'x': bbox.xmin(),
                                        'y': bbox.ymin(),
                                        'width': bbox.width(),
                                        'height': bbox.height(),
                                        'bbox': bbox
                                    })

                        # Получаем режим консоли для передачи в трекер
                        console_mode = self.parent.config['logging'].get('console_output_mode', 'all')

                        # Обновление трекера
                        birds_on_frame, new_birds = self.parent.bird_tracker.update_birds(
                            bird_detections, current_time, console_mode)

                        # Логирование только при новом посещении или при наличии детекций в режиме 'all'
                        if bird_detections and (self.parent.bird_tracker.new_visit_happened or console_mode == 'all'):
                            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                            stats = self.parent.bird_tracker.get_stats()
                            self.parent.log_manager.log_detection(
                                timestamp, birds_on_frame, stats['current_active'],
                                stats['total_unique'], stats['total_feeding_visits'], bird_detections)

                        # Логирование событий изменения счетчика
                        if self.parent.bird_tracker.has_changes():
                            stats = self.parent.bird_tracker.get_stats()
                            if stats['total_feeding_visits'] > self.parent.bird_tracker.prev_total_feeding_visits:
                                self.parent.log_manager.log_counter_event(
                                    "Посещение кормушки", stats['total_feeding_visits'], current_time)
                            if stats['total_unique'] > self.parent.bird_tracker.prev_total_unique:
                                self.parent.log_manager.log_counter_event(
                                    "Новая уникальная птица", stats['total_unique'], current_time)

                        # Обновление кадров для стримов
                        self.parent.update_camera_frame(frame)
                        self.parent.update_detection_frame(frame, bird_detections, width, height)

                        # Сохранение фото
                        if (self.parent.enable_photo_save and
                            birds_on_frame > 0 and
                            current_time - self.parent.last_save_time >= self.parent.min_save_interval):
                            self.parent.save_bird_photo(frame, birds_on_frame)
                            self.parent.last_save_time = current_time

                        # Отладочное логирование производительности
                        if self.parent.log_manager.enable_performance_log:
                            # Получаем использование памяти (в MB)
                            try:
                                with open('/proc/meminfo', 'r') as f:
                                    mem_info = f.read()
                                total_match = re.search(r'MemTotal:\s+(\d+)', mem_info)
                                available_match = re.search(r'MemAvailable:\s+(\d+)', mem_info)
                                if total_match and available_match:
                                    total_mem = int(total_match.group(1)) / 1024  # MB
                                    available_mem = int(available_match.group(1)) / 1024  # MB
                                    used_mem = total_mem - available_mem
                                else:
                                    used_mem = 0.0
                            except:
                                used_mem = 0.0

                            # Расчет задержки кадра
                            frame_delay = time_diff if 'time_diff' in locals() else 0.0

                            # Температура CPU
                            cpu_temp = self.parent.log_manager.get_cpu_temperature() or 0.0

                            # Логируем метрики
                            self.parent.log_manager.log_performance_debug(
                                self.parent.fps, cpu_temp, frame_delay, used_mem,
                                f"birds={birds_on_frame}, frame={self.parent.frame_count}"
                            )

                # Вывод статистики в зависимости от режима
                console_mode = self.parent.config['logging'].get('console_output_mode', 'all')

                if console_mode == 'all':
                    # Выводим все как раньше
                    if self.parent.frame_count % 30 == 0:
                        stats = self.parent.bird_tracker.get_stats()
                        print(f"📊 Кадр {self.parent.frame_count} | FPS: {self.parent.fps:.1f} | "
                              f"Птиц: {stats['current_on_frame']} | Активных: {stats['current_active']} | "
                              f"Уникальных: {stats['total_unique']} | Посещений: {stats['total_feeding_visits']}")

                elif console_mode == 'changes_only':
                    # Выводим только при изменениях счетчиков
                    if self.parent.bird_tracker.has_changes():
                        stats = self.parent.bird_tracker.get_stats()
                        print(f"📊 ИЗМЕНЕНИЕ | Уникальных: {stats['total_unique']} | Посещений: {stats['total_feeding_visits']}")

                elif console_mode == 'minimal':
                    # Только критические сообщения (уже выводятся в update_feeding_visits)
                    pass

            except Exception as e:
                if self.parent.frame_count % 30 == 0:
                    print(f"⚠️ Ошибка в callback: {e}")

            return Gst.PadProbeReturn.OK

    def update_camera_frame(self, frame):
        """Обновление кадра для чистого стрима."""
        try:
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            display = frame_bgr.copy()

            # Минимальная информация
            cv2.putText(display, "Camera Stream v5.5", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            cv2.putText(display, f"Frame: {self.frame_count}", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            self.camera_frame = display

        except Exception as e:
            print(f"❌ Ошибка update_camera_frame: {e}")

    def update_detection_frame(self, frame, detections, width, height):
        """Обновление кадра для стрима с детекцией."""
        try:
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            display = frame_bgr.copy()

            # Рисуем bounding boxes
            for detection in detections:
                bbox = detection['bbox']
                x1 = int(bbox.xmin() * width)
                y1 = int(bbox.ymin() * height)
                x2 = int(bbox.xmax() * width)
                y2 = int(bbox.ymax() * height)

                cv2.rectangle(display, (x1, y1), (x2, y2), (0, 255, 0), 2)

                confidence = detection['confidence']
                text = f"{detection['label']} {confidence:.2f}"
                cv2.putText(display, text, (x1, y1-10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

            # Информационная панель
            stats = self.bird_tracker.get_stats()

            # Получаем температуру процессора (целочисленная, без значка градуса)
            cpu_temp = self.log_manager.get_cpu_temperature()
            temp_str = f"{int(cpu_temp)} C" if cpu_temp is not None else "N/A"

            info_lines = [
                f"Frame: {self.frame_count}",
                f"FPS: {self.fps:.1f}",
                f"Birds: {stats['current_on_frame']}",
                f"Active: {stats['current_active']}",
                f"Unique: {stats['total_unique']}",
                f"Visits: {stats['total_feeding_visits']}",
                f"Temp: {temp_str}",
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            ]

            y_offset = 30
            for line in info_lines:
                cv2.putText(display, line, (10, y_offset),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                y_offset += 25

            self.detection_frame = display

        except Exception as e:
            print(f"❌ Ошибка update_detection_frame: {e}")

    def save_bird_photo(self, frame, bird_count):
        """Сохранение фото с правильными цветами и уникальным счетчиком."""
        try:
            # Создание папки для фото
            photos_dir = Path(self.config['logging']['logs_path']) / self.log_manager.session_folder.name / "photos"
            photos_dir.mkdir(exist_ok=True)

            # Увеличиваем глобальный счетчик фотографий
            self.photo_count += 1

            # Имя файла с уникальным счетчиком
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = self.config['frame_saving']['photo_filename_pattern'].format(
                timestamp=timestamp, bird_count=self.photo_count)
            filepath = photos_dir / filename

            # Конвертация RGB → BGR для правильных цветов
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            cv2.imwrite(str(filepath), frame_bgr)
            print(f"💾 Фото сохранено: {filepath} (фото #{self.photo_count})")

        except Exception as e:
            print(f"❌ Ошибка сохранения фото: {e}")

    def start_camera_stream_server(self):
        """Запуск сервера чистого стрима."""
        class CameraStreamHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    html = f'''<html><head><title>Camera Stream v5.5</title></head>
                    <body><h1>Camera Stream v5.5</h1>
                    <p>Pure camera feed with smart bird tracking</p>
                    <img src="/stream" width="640" height="480">
                    </body></html>'''
                    self.wfile.write(html.encode())
                elif self.path == '/stream':
                    self.send_response(200)
                    self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()

                    try:
                        while True:
                            if self.server.detector.camera_frame is not None:
                                _, jpeg = cv2.imencode('.jpg', self.server.detector.camera_frame)
                                if jpeg is not None:
                                    self.wfile.write(b'--frame\r\n')
                                    self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                                    self.wfile.write(jpeg.tobytes())
                                    self.wfile.write(b'\r\n')
                            time.sleep(0.1)
                    except:
                        pass
                else:
                    self.send_error(404)

            def log_message(self, format, *args):
                return

        try:
            server = HTTPServer(('0.0.0.0', self.camera_port), CameraStreamHandler)
            server.detector = self
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f"✅ Чистый стрим: http://localhost:{self.camera_port}")
        except Exception as e:
            print(f"❌ Ошибка запуска стрима камеры: {e}")

    def start_detection_stream_server(self):
        """Запуск сервера стрима с детекцией."""
        class DetectionStreamHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    html = f'''<html><head><title>Bird Detection v5.5</title></head>
                    <body><h1>Bird Detection v5.5</h1>
                    <p>Smart bird tracking with unique photo naming</p>
                    <img src="/stream" width="640" height="480">
                    </body></html>'''
                    self.wfile.write(html.encode())
                elif self.path == '/stream':
                    self.send_response(200)
                    self.send_header('Content-type', 'multipart/x-mixed-replace; boundary=frame')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()

                    try:
                        while True:
                            if self.server.detector.detection_frame is not None:
                                _, jpeg = cv2.imencode('.jpg', self.server.detector.detection_frame)
                                if jpeg is not None:
                                    self.wfile.write(b'--frame\r\n')
                                    self.wfile.write(b'Content-Type: image/jpeg\r\n\r\n')
                                    self.wfile.write(jpeg.tobytes())
                                    self.wfile.write(b'\r\n')
                            time.sleep(0.1)
                    except:
                        pass
                else:
                    self.send_error(404)

            def log_message(self, format, *args):
                return

        try:
            server = HTTPServer(('0.0.0.0', self.detection_port), DetectionStreamHandler)
            server.detector = self
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            print(f"✅ Стрим с детекцией: http://localhost:{self.detection_port}")
        except Exception as e:
            print(f"❌ Ошибка запуска стрима детекции: {e}")

    def start_temperature_monitoring(self):
        """Запуск отдельного потока для мониторинга температуры."""
        def temperature_monitor():
            """Поток для периодического логирования температуры."""
            while True:
                # Получаем температуру и FPS
                temperature = self.log_manager.get_cpu_temperature()
                current_fps = getattr(self, 'fps', 0.0)

                if temperature is not None:
                    # Логируем температуру и FPS
                    self.log_manager.log_temperature(temperature, time.time(), current_fps)

                # Спим точно интервал времени
                time.sleep(self.log_manager.temperature_log_interval)

        # Запускаем поток как daemon (завершится при остановке основного процесса)
        temperature_thread = threading.Thread(target=temperature_monitor, daemon=True)
        temperature_thread.start()
        print(f"🌡️ Мониторинг температуры запущен (интервал: {self.log_manager.temperature_log_interval} сек)")

        # Первая запись температуры при запуске
        initial_temp = self.log_manager.get_cpu_temperature()
        if initial_temp is not None:
            self.log_manager.log_temperature(initial_temp, time.time())
            print(f"🌡️ Начальная температура процессора: {initial_temp}°C")

    def run(self):
        """Запуск GStreamer детекции."""
        try:
            app = GStreamerDetectionApp(self.callback_obj.process_callback, self.callback_obj)
            app.run()
        except KeyboardInterrupt:
            print("\n🛑 Остановлено пользователем")
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")

def main():
    detector = BirdDetectorV55()
    detector.run()

if __name__ == "__main__":
    main()

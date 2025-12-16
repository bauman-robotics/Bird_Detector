#!/usr/bin/env python3
"""
Video Creator для Bird Detector
Создание видеоряда из фотографий с гибкой конфигурацией

Версия: 1.0
"""

import os
import sys
import yaml
import logging
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict, Any

try:
    from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip
    from tqdm import tqdm
    MOVIEPY_AVAILABLE = True
    TQDM_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    try:
        from tqdm import tqdm
        TQDM_AVAILABLE = True
    except ImportError:
        TQDM_AVAILABLE = False

class VideoCreator:
    """Класс для создания видео из последовательности изображений"""

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self._load_config()
        self._setup_logging()
        self.logger = logging.getLogger(__name__)

    def _load_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации из YAML файла"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            return config
        except FileNotFoundError:
            print(f"Ошибка: Конфигурационный файл '{self.config_path}' не найден")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"Ошибка чтения YAML файла: {e}")
            sys.exit(1)

    def _setup_logging(self):
        """Настройка логирования"""
        log_config = self.config.get('logging', {})
        log_level = getattr(logging, log_config.get('log_level', 'INFO').upper())

        # Настройка основного логгера
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_config.get('log_file', 'video_creation.log')),
                logging.StreamHandler(sys.stdout) if log_config.get('enable_console_output', True) else logging.NullHandler()
            ]
        )

    def _parse_frame_number(self, filename: str, tag: str) -> Optional[int]:
        """Извлечение номера кадра из имени файла"""
        # Ищем тег в имени файла
        pattern = rf'{re.escape(tag)}(\d+)'
        match = re.search(pattern, filename, re.IGNORECASE)
        return int(match.group(1)) if match else None

    def _get_frame_files(self) -> List[str]:
        """Получение отсортированного списка файлов кадров"""
        input_config = self.config['input']
        frames_folder = Path(input_config['frames_folder'])

        if not frames_folder.exists():
            raise FileNotFoundError(f"Папка с кадрами не найдена: {frames_folder}")

        # Получаем все JPG файлы
        frame_files = list(frames_folder.glob('*.jpg')) + list(frames_folder.glob('*.jpeg'))

        # Фильтруем по тегу и диапазону
        filtered_files = []
        tag = input_config['tag']
        start_frame = input_config['start_frame']
        end_frame = input_config['end_frame'] or float('inf')
        exclude_frames = set(input_config.get('exclude_frames', []))

        for file_path in frame_files:
            frame_num = self._parse_frame_number(file_path.name, tag)
            if frame_num is None:
                continue
            if start_frame <= frame_num <= end_frame and frame_num not in exclude_frames:
                filtered_files.append((frame_num, str(file_path)))

        # Сортируем по номеру кадра
        filtered_files.sort(key=lambda x: x[0])
        return [fp for _, fp in filtered_files]

    def _calculate_timing(self, frame_count: int) -> tuple:
        """Расчет параметров времени"""
        timing_config = self.config['timing']

        if timing_config['interval_mode'] == 'fixed':
            interval_ms = timing_config['frame_interval_ms']
            total_duration_ms = frame_count * interval_ms
            fps = 1000 / interval_ms
        else:  # duration mode
            total_duration_ms = timing_config['total_duration_ms']
            interval_ms = total_duration_ms / frame_count if frame_count > 0 else 0
            fps = 1000 / interval_ms if interval_ms > 0 else 24

        return interval_ms, total_duration_ms, fps

    def _create_video_ffmpeg(self, frame_files: List[str], output_path: str, fps: float) -> bool:
        """Создание видео с помощью ffmpeg"""
        if not frame_files:
            self.logger.error("Нет кадров для создания видео")
            return False

        # Создаем временную папку с пронумерованными файлами
        temp_dir = Path(self.config.get('advanced', {}).get('temp_folder', '/tmp/video_creator'))
        temp_dir.mkdir(exist_ok=True)

        # Копируем файлы с последовательными именами
        import shutil
        print("📂 Подготовка кадров...")
        if TQDM_AVAILABLE:
            for i, src_file in enumerate(tqdm(frame_files, desc="Копирование кадров", unit="файл")):
                dst_file = temp_dir / f"frame_{i:06d}.jpg"
                shutil.copy2(src_file, dst_file)
        else:
            for i, src_file in enumerate(frame_files):
                dst_file = temp_dir / f"frame_{i:06d}.jpg"
                shutil.copy2(src_file, dst_file)
            print(f"✅ Скопировано {len(frame_files)} кадров")

        # Формируем команду ffmpeg
        output_config = self.config['output']
        cmd = [
            'ffmpeg',
            '-y',  # Перезаписывать без вопросов
            '-framerate', str(fps),
            '-i', str(temp_dir / 'frame_%06d.jpg'),
            '-c:v', output_config.get('video_codec', 'libx264'),
            '-crf', str(output_config.get('video_quality', 23)),
            '-preset', output_config.get('preset', 'medium'),
            '-pix_fmt', 'yuv420p',
            str(output_path)
        ]

        self.logger.info(f"Выполнение команды: {' '.join(cmd)}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)  # Увеличено до 30 минут
            if result.returncode == 0:
                self.logger.info("Видео создано успешно")
                return True
            else:
                self.logger.error(f"Ошибка ffmpeg: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            self.logger.error("Превышено время ожидания ffmpeg")
            return False
        finally:
            # Очистка временных файлов
            if self.config.get('advanced', {}).get('cleanup_temp', True):
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _add_audio(self, video_path: str, audio_config: Dict) -> bool:
        """Добавление аудио к видео"""
        if not MOVIEPY_AVAILABLE:
            self.logger.warning("MoviePy не доступен, аудио не будет добавлено")
            return False

        try:
            # Загружаем видео и аудио
            video_clip = VideoFileClip(video_path)
            audio_clip = AudioFileClip(audio_config['audio_file'])

            # Обрезаем аудио до длительности видео
            video_duration = video_clip.duration
            audio_start = audio_config.get('audio_start_ms', 0) / 1000
            audio_end = audio_start + video_duration
            audio_clip = audio_clip.subclipped(audio_start, audio_end)

            # Применяем эффекты
            fade_in = audio_config.get('fade_in_ms', 0) / 1000
            fade_out = audio_config.get('fade_out_ms', 0) / 1000
            volume = audio_config.get('volume', 1.0)

            # Применяем громкость
            audio_clip = audio_clip.with_volume_scaled(volume)

            # В MoviePy 2.x эффекты fade нужно реализовать отдельно
            # Пока оставляем без fade эффектов для совместимости

            # Композитинг
            final_audio = CompositeAudioClip([audio_clip])
            video_with_audio = video_clip.with_audio(final_audio)

            # Сохраняем
            temp_output = str(Path(video_path).with_suffix('.temp.mp4'))
            video_with_audio.write_videofile(temp_output, codec='libx264', audio_codec='aac')

            # Заменяем оригинал
            Path(temp_output).replace(video_path)

            self.logger.info("Аудио добавлено успешно")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка добавления аудио: {e}")
            return False

    def create_video(self, dry_run: bool = False) -> bool:
        """Основной метод создания видео"""
        self.logger.info("Video Creator v1.0 запущен")

        try:
            # Проверяем аудио настройки перед началом
            audio_config = self.config.get('audio', {})
            if audio_config.get('enable_audio', False):
                audio_file_path = Path(audio_config['audio_file'])
                if not audio_file_path.exists():
                    self.logger.error(f"Аудио файл не найден: {audio_file_path}")
                    print(f"❌ Ошибка: Аудио файл не найден: {audio_file_path}")
                    return False
                self.logger.info(f"Аудио файл найден: {audio_file_path}")

            # Получаем файлы кадров
            frame_files = self._get_frame_files()
            self.logger.info(f"Найдено {len(frame_files)} кадров для обработки")

            if not frame_files:
                self.logger.error("Нет подходящих кадров")
                return False

            # Расчет времени
            interval_ms, total_duration_ms, fps = self._calculate_timing(len(frame_files))
            self.logger.info(".2f")

            # Путь вывода
            output_config = self.config['output']
            output_folder = Path(output_config.get('output_folder', './videos'))
            output_folder.mkdir(exist_ok=True)
            output_path = output_folder / output_config['video_filename']

            if dry_run:
                self.logger.info("DRY RUN: Видео не создается")
                self.logger.info(f"Выходной файл: {output_path}")
                return True

            # Создание видео
            success = self._create_video_ffmpeg(frame_files, str(output_path), fps)

            if success:
                # Добавление аудио если нужно
                if audio_config.get('enable_audio', False):
                    print("🎵 Добавление аудио к видео...")
                    audio_success = self._add_audio(str(output_path), audio_config)
                    if audio_success:
                        print("✅ Аудио добавлено успешно")
                    else:
                        print("⚠️  Предупреждение: Не удалось добавить аудио")

                self.logger.info(f"Видео создано: {output_path}")
                return True
            else:
                self.logger.error("Не удалось создать видео")
                return False

        except Exception as e:
            self.logger.error(f"Ошибка создания видео: {e}")
            return False

    def print_config_summary(self):
        """Вывод сводки конфигурации в консоль"""
        print("Video Creator v1.0")
        print("=" * 50)
        print(f"Конфигурация: {self.config_path}")
        print()

        input_cfg = self.config['input']
        print("Входные параметры:")
        print(f"  Папка с кадрами: {input_cfg['frames_folder']}")
        print(f"  Тег: {input_cfg['tag']}")
        print(f"  Диапазон кадров: {input_cfg['start_frame']} - {input_cfg.get('end_frame', 'конец')}")
        if input_cfg.get('exclude_frames'):
            print(f"  Исключаемые кадры: {input_cfg['exclude_frames']}")
        print()

        timing_cfg = self.config['timing']
        print("Настройки времени:")
        print(f"  Режим: {'Фиксированный интервал' if timing_cfg['interval_mode'] == 'fixed' else 'Общая длительность'}")
        if timing_cfg['interval_mode'] == 'fixed':
            print(f"  Интервал: {timing_cfg['frame_interval_ms']} мс")
        else:
            print(f"  Длительность: {timing_cfg['total_duration_ms']} мс")
        print()

        audio_cfg = self.config.get('audio', {})
        if audio_cfg.get('enable_audio', False):
            print("🎵 Аудио: ВКЛЮЧЕНО")
            print(f"  Файл: {audio_cfg['audio_file']}")
            print(f"  Старт: {audio_cfg.get('audio_start_ms', 0)} мс")
            print(f"  Громкость: {audio_cfg.get('volume', 1.0)}")
            print(f"  Fade in: {audio_cfg.get('fade_in_ms', 0)} мс")
            print(f"  Fade out: {audio_cfg.get('fade_out_ms', 0)} мс")
            print()
        else:
            print("🔇 Аудио: ОТКЛЮЧЕНО")
            print()


def main():
    parser = argparse.ArgumentParser(description='Video Creator для Bird Detector')
    parser.add_argument('--config', '-c', default='video_creator_config.yaml',
                       help='Путь к конфигурационному файлу')
    parser.add_argument('--dry-run', action='store_true',
                       help='Только анализ, без создания видео')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Подробный вывод')
    parser.add_argument('--version', action='version', version='Video Creator 1.0')

    args = parser.parse_args()

    # Создаем экземпляр
    creator = VideoCreator(args.config)

    # Выводим сводку
    creator.print_config_summary()

    # Создаем видео
    success = creator.create_video(dry_run=args.dry_run)

    if success:
        print("\n✅ Видео создано успешно!")
    else:
        print("\n❌ Ошибка создания видео!")
        sys.exit(1)


if __name__ == '__main__':
    main()

import tkinter as tk
import threading
import time
from pynput import keyboard
import platform
import json
import os
import ctypes

class KeyboardOverlay:
    def __init__(self, config_path='config.json'):
        self.root = tk.Tk()
        self.root.title("Keyboard Overlay")
        
        # Загрузка конфигурации
        self.config = self._load_config(config_path)
        
        # Настройки
        self.position = self.config.get('position', 'bottom')
        self.scale = self.config.get('scale', 0.5)
        self.width = self.config.get('width', 1400)
        self.height = self.config.get('height', 300)
        self.colors = self.config.get('colors', {
            'bg': '#00000000',
            'key_bg': '#30202030',
            'key_border': '#60ffffff',
            'key_text': '#ffffff',
            'key_pressed': '#00d4ff',
            'key_pressed_text': '#000000',
            'key_pressed_border': '#00ffff',
        })
        
        # Состояние нажатых клавиш
        self.pressed_keys = {}  # {символ: (время, яркость)}
        self.last_activity_time = time.time()
        self.idle_timeout = self.config.get('idle_timeout', 5.0)
        self.fade_duration = self.config.get('fade_duration', 2.0)
        self.key_fade_duration = self.config.get('key_fade_duration', 0.8)
        self.current_alpha = 1.0
        self.target_alpha = 1.0
        
        # УПРОЩЕННАЯ система раскладок - показываем английскую, подсвечиваем что нажато
        self.english_layout = [
            ['`', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
            ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p', '[', ']', '\\'],
            ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l', ';', "'"],
            ['z', 'x', 'c', 'v', 'b', 'n', 'm', ',', '.', '/'],
        ]
        
        self.russian_layout = [
            ['ё', '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', '='],
            ['й', 'ц', 'у', 'к', 'е', 'н', 'г', 'ш', 'щ', 'з', 'х', 'ъ', '\\'],
            ['ф', 'ы', 'в', 'а', 'п', 'р', 'о', 'л', 'д', 'ж', 'э'],
            ['я', 'ч', 'с', 'м', 'и', 'т', 'ь', 'б', 'ю', '.'],
        ]
        
        # Маппинг русских букв к английским позициям
        self.ru_to_en_map = {}
        for row_idx in range(min(len(self.english_layout), len(self.russian_layout))):
            for col_idx in range(min(len(self.english_layout[row_idx]), len(self.russian_layout[row_idx]))):
                en_char = self.english_layout[row_idx][col_idx]
                ru_char = self.russian_layout[row_idx][col_idx]
                self.ru_to_en_map[ru_char] = en_char
        
        # Текущая раскладка (для отображения)
        self.current_display_layout = self._detect_windows_layout()  # Определяем сразу из Windows
        
        # Настройка окна
        self._setup_window()
        
        # Canvas
        self.canvas = tk.Canvas(
            self.root,
            bg='black',
            highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        if platform.system() == 'Windows':
            self.root.attributes('-transparentcolor', 'black')
        
        # Listener для клавиш
        self.listener = None
        self._start_key_listener()
        
        # Анимация
        self._animate()
    
    def _load_config(self, config_path):
        """Загрузка конфигурации"""
        default = {
            'position': 'bottom',
            'scale': 0.5,
            'width': 1400,
            'height': 300,
            'colors': {
                'bg': '#00000000',
                'key_bg': '#30202030',
                'key_border': '#60ffffff',
                'key_text': '#ffffff',
                'key_pressed': '#00d4ff',
                'key_pressed_text': '#000000',
                'key_pressed_border': '#00ffff',
            },
            'idle_timeout': 5.0,
            'fade_duration': 2.0,
            'key_fade_duration': 0.8,
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    default.update(user_config)
                    if 'colors' in user_config:
                        default['colors'].update(user_config['colors'])
            except:
                pass
        
        return default
    
    def _setup_window(self):
        """Настройка окна"""
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        
        if platform.system() == 'Windows':
            self.root.attributes('-alpha', 0.95)
            self.root.configure(bg='black')
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        if self.position == "right":
            x = screen_width - self.width - 10
            y = (screen_height - self.height) // 2
        elif self.position == "left":
            x = 10
            y = (screen_height - self.height) // 2
        elif self.position == "top":
            x = (screen_width - self.width) // 2
            y = 10
        elif self.position == "bottom":
            x = (screen_width - self.width) // 2
            y = screen_height - self.height - 10
        else:
            x = (screen_width - self.width) // 2
            y = (screen_height - self.height) // 2
        
        self.root.geometry(f"{self.width}x{self.height}+{x}+{y}")
        
        # Делаем окно прозрачным для кликов мыши (click-through)
        if platform.system() == 'Windows':
            self.root.update_idletasks()  # Обновляем окно перед получением hwnd
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            
            # Константы Windows API
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            
            # Получаем текущий стиль
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            # Добавляем флаги для прозрачности кликов
            style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
            # Устанавливаем новый стиль
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    
    def _detect_windows_layout(self):
        """Определение раскладки из Windows"""
        if platform.system() == 'Windows':
            try:
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                hwnd = user32.GetForegroundWindow()
                thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
                klid = user32.GetKeyboardLayout(thread_id)
                lid = klid & 0xFFFF
                
                # 0x0419 - русский, 0x0409 - английский
                if lid == 0x0419:
                    return 'ru'
                else:
                    return 'en'
            except:
                pass
        return 'en'
    
    def _on_key_press(self, key):
        """Обработка нажатия клавиши"""
        try:
            # СНАЧАЛА проверяем раскладку Windows
            new_layout = self._detect_windows_layout()
            if new_layout != self.current_display_layout:
                # Раскладка изменилась - очищаем старые нажатия
                self.pressed_keys.clear()
                self.current_display_layout = new_layout
            
            # Получаем символ
            if hasattr(key, 'char') and key.char:
                char = key.char.lower()
                
                # Добавляем в нажатые
                self.pressed_keys[char] = (time.time(), 1.0)
                self.last_activity_time = time.time()
                self.target_alpha = 1.0
        except:
            pass
    
    def _on_key_release(self, key):
        """Обработка отпускания"""
        pass
    
    def _start_key_listener(self):
        """Запуск listener"""
        def start():
            self.listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release
            )
            self.listener.start()
        
        thread = threading.Thread(target=start, daemon=True)
        thread.start()
    
    def _draw_keyboard(self):
        """Отрисовка клавиатуры"""
        self.canvas.delete("all")
        
        # Выбираем раскладку для отображения
        if self.current_display_layout == 'ru':
            layout = self.russian_layout
        else:
            layout = self.english_layout
        
        # Размеры
        key_width = 50 * self.scale
        key_height = 50 * self.scale
        key_spacing = 6 * self.scale
        
        # Вычисляем ширину для центрирования
        max_row_width = 0
        for row_idx, row in enumerate(layout):
            offset = 0
            if row_idx == 1:
                offset = key_width * 0.25
            elif row_idx == 2:
                offset = key_width * 0.5
            elif row_idx == 3:
                offset = key_width * 0.75
            
            row_width = len(row) * (key_width + key_spacing) - key_spacing + offset
            max_row_width = max(max_row_width, row_width)
        
        start_x = (self.width - max_row_width) // 2
        start_y = 20
        
        # Рисуем клавиши
        for row_idx, row in enumerate(layout):
            offset = 0
            if row_idx == 1:
                offset = key_width * 0.25
            elif row_idx == 2:
                offset = key_width * 0.5
            elif row_idx == 3:
                offset = key_width * 0.75
            
            x_start = start_x + offset
            
            for col_idx, char in enumerate(row):
                x = x_start + col_idx * (key_width + key_spacing)
                y = start_y + row_idx * (key_height + key_spacing)
                
                # Проверяем нажатие
                is_pressed = False
                press_alpha = 0.0
                
                # Прямая проверка
                if char in self.pressed_keys:
                    is_pressed = True
                    press_alpha = self.pressed_keys[char][1]
                # Проверка через маппинг (если русская буква, проверяем английскую позицию)
                elif self.current_display_layout == 'en' and char in self.ru_to_en_map.values():
                    # Ищем русскую букву для этой английской позиции
                    for ru_char, en_char in self.ru_to_en_map.items():
                        if en_char == char and ru_char in self.pressed_keys:
                            is_pressed = True
                            press_alpha = self.pressed_keys[ru_char][1]
                            break
                
                self._draw_key(x, y, key_width, key_height, char, press_alpha)
    
    def _draw_key(self, x, y, width, height, char, press_alpha):
        """Рисуем одну клавишу"""
        # Цвета
        if press_alpha > 0.05:
            bg_color = self.colors['key_pressed']
            text_color = '#000000'
            border_color = self.colors.get('key_pressed_border', self.colors['key_pressed'])
            border_width = 3
        else:
            bg_color = self.colors['key_bg']
            text_color = self.colors['key_text']
            border_color = self.colors['key_border']
            border_width = 2
        
        # Конвертируем цвета
        bg_rgb = self._hex_to_rgb(bg_color)
        border_rgb = self._hex_to_rgb(border_color)
        
        # Рисуем клавишу
        self.canvas.create_rectangle(
            x, y, x + width, y + height,
            fill=bg_rgb if bg_rgb else '',
            outline=border_rgb if border_rgb else border_color,
            width=border_width,
            tags="key"
        )
        
        # Эффект свечения
        if press_alpha > 0.3:
            glow = 3 * self.scale * press_alpha
            self.canvas.create_rectangle(
                x - glow, y - glow,
                x + width + glow, y + height + glow,
                fill='',
                outline=self.colors['key_pressed'],
                width=1,
                tags="key"
            )
        
        # Текст
        font_size = max(12, int(16 * self.scale))
        
        # Тень
        if press_alpha < 0.05:
            self.canvas.create_text(
                x + width / 2 + 1, y + height / 2 + 1,
                text=char.upper(),
                fill='#202020',
                font=('Arial', font_size, 'bold'),
                tags="key"
            )
        
        # Основной текст
        self.canvas.create_text(
            x + width / 2, y + height / 2,
            text=char.upper(),
            fill=text_color,
            font=('Arial', font_size, 'bold'),
            tags="key"
        )
    
    def _hex_to_rgb(self, hex_color):
        """Конвертация цвета"""
        if not hex_color or hex_color == '#00000000':
            return None
        
        hex_color = hex_color.lstrip('#')
        
        if len(hex_color) == 8:
            alpha = int(hex_color[0:2], 16) / 255.0
            if alpha < 0.05:
                return None
            rgb = hex_color[2:]
            r = int(rgb[0:2], 16)
            g = int(rgb[2:4], 16)
            b = int(rgb[4:6], 16)
            r = int(r * alpha)
            g = int(g * alpha)
            b = int(b * alpha)
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)
        
        if len(hex_color) == 6:
            return '#' + hex_color
        
        return None
    
    def _animate(self):
        """Анимация"""
        current_time = time.time()
        
        # Проверяем раскладку каждый кадр
        new_layout = self._detect_windows_layout()
        if new_layout != self.current_display_layout:
            self.current_display_layout = new_layout
            # Очищаем при смене
            self.pressed_keys.clear()
        
        # Прозрачность при неактивности
        time_since_activity = current_time - self.last_activity_time
        if time_since_activity > self.idle_timeout:
            fade = min(1.0, (time_since_activity - self.idle_timeout) / self.fade_duration)
            self.target_alpha = max(0.3, 1.0 - fade * 0.7)
        else:
            self.target_alpha = 1.0
        
        alpha_diff = self.target_alpha - self.current_alpha
        self.current_alpha += alpha_diff * 0.1
        
        if platform.system() == 'Windows':
            self.root.attributes('-alpha', self.current_alpha)
        
        # Затухание клавиш
        keys_to_remove = []
        for key_name, (press_time, alpha) in list(self.pressed_keys.items()):
            time_since = current_time - press_time
            if time_since > self.key_fade_duration:
                keys_to_remove.append(key_name)
            else:
                new_alpha = max(0.0, 1.0 - (time_since / self.key_fade_duration))
                self.pressed_keys[key_name] = (press_time, new_alpha)
        
        for key in keys_to_remove:
            del self.pressed_keys[key]
        
        # Перерисовка
        self._draw_keyboard()
        
        # Следующий кадр
        self.root.after(16, self._animate)
    
    def run(self):
        """Запуск"""
        self.root.mainloop()


if __name__ == "__main__":
    app = KeyboardOverlay()
    app.run()

import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
import threading
import time
from pynput import keyboard
import platform
import json
import os
import ctypes
from datetime import datetime

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except Exception:
    pystray = None
    Image = None
    ImageDraw = None
    ImageFont = None

class KeyboardOverlay:
    def __init__(self, config_path='config.json'):
        self.root = tk.Tk()
        self.root.title("Keyboard Overlay")

        self.log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keyboard_overlay.log')
        
        # Загрузка конфигурации
        self.config = self._load_config(config_path)
        self.config_path = config_path
        
        # Настройки
        self.position = self.config.get('position', 'bottom')
        self.custom_x = self.config.get('custom_x', None)
        self.custom_y = self.config.get('custom_y', None)
        self.scale = self.config.get('scale', 0.5)
        self.width = self.config.get('width', 1400)
        self.height = self.config.get('height', 300)
        self.max_alpha = float(self.config.get('max_alpha', 0.92))
        self.min_alpha = float(self.config.get('min_alpha', 0.30))
        self.visible_rows = self.config.get('visible_rows', [True, True, True, True])
        self.disabled_keys = self.config.get('disabled_keys', {})  # {"row_0": ["1", "2"], ...}
        self.tray_icon_path = self.config.get('tray_icon_path', 'tray.ico')
        
        # Стиль клавиш
        self.key_style = self.config.get('key_style', 'rounded')  # flat, rounded, 3d, glass
        self.border_radius = self.config.get('border_radius', 8)
        self.shadow_size = self.config.get('shadow_size', 3)
        self.glow_intensity = self.config.get('glow_intensity', 1.0)
        self.border_width = self.config.get('border_width', 2)
        self.key_padding = self.config.get('key_padding', 6)
        
        self.colors = self.config.get('colors', {
            'bg': '#00000000',
            'key_bg': '#30202030',
            'key_border': '#60ffffff',
            'key_text': '#ffffff',
            'key_pressed': '#00d4ff',
            'key_pressed_text': '#000000',
            'key_pressed_border': '#00ffff',
            'key_shadow': '#20000000',
            'key_highlight': '#40ffffff',
        })
        
        # Состояние нажатых клавиш
        self.pressed_keys = {}  # {символ: (время, яркость)}
        self.last_activity_time = time.time()
        self.idle_timeout = self.config.get('idle_timeout', 5.0)
        self.fade_duration = self.config.get('fade_duration', 2.0)
        self.key_fade_duration = self.config.get('key_fade_duration', 0.8)
        self.current_alpha = self.max_alpha
        self.target_alpha = self.max_alpha
        
        # Режим перетаскивания
        self.drag_mode = False
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.window_start_x = 0
        self.window_start_y = 0
        
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
        
        # Маппинг русских букв к английским позициям и обратно
        self.ru_to_en_map = {}
        self.en_to_ru_map = {}
        for row_idx in range(min(len(self.english_layout), len(self.russian_layout))):
            for col_idx in range(min(len(self.english_layout[row_idx]), len(self.russian_layout[row_idx]))):
                en_char = self.english_layout[row_idx][col_idx]
                ru_char = self.russian_layout[row_idx][col_idx]
                self.ru_to_en_map[ru_char] = en_char
                self.en_to_ru_map[en_char] = ru_char
        
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
        
        # Привязка событий для перетаскивания
        self.canvas.bind('<Button-1>', self._on_drag_start)
        self.canvas.bind('<B1-Motion>', self._on_drag_motion)
        self.canvas.bind('<ButtonRelease-1>', self._on_drag_end)
        
        # Listener для клавиш
        self.listener = None
        self._start_key_listener()

        # Настройки (окно + трей)
        self.settings_window = None
        self.themes = self._load_themes('themes.json')
        self.tray_icon = None
        self.tray_thread = None
        self.tray_status_var = tk.StringVar(value="Трей: инициализация...")
        # Запускаем трей сразу (после старта Tk), так стабильнее на Windows
        self.root.after(100, self._setup_tray)
        # Окно настроек НЕ показываем при запуске — только по клику из трея
        self._create_settings_window(show=False)
        
        # Анимация
        self._animate()
    
    def _load_config(self, config_path):
        """Загрузка конфигурации"""
        default = {
            'position': 'bottom',
            'custom_x': None,
            'custom_y': None,
            'scale': 0.5,
            'width': 1400,
            'height': 300,
            'max_alpha': 0.92,
            'min_alpha': 0.30,
            'visible_rows': [True, True, True, True],
            'disabled_keys': {},
            'tray_icon_path': 'tray.ico',
            'key_style': 'rounded',
            'border_radius': 8,
            'shadow_size': 3,
            'glow_intensity': 1.0,
            'border_width': 2,
            'key_padding': 6,
            'colors': {
                'bg': '#00000000',
                'key_bg': '#30202030',
                'key_border': '#60ffffff',
                'key_text': '#ffffff',
                'key_pressed': '#00d4ff',
                'key_pressed_text': '#000000',
                'key_pressed_border': '#00ffff',
                'key_shadow': '#20000000',
                'key_highlight': '#40ffffff',
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

    def _save_config(self):
        """Сохранение конфигурации в JSON"""
        data = {
            'position': self.position,
            'custom_x': self.custom_x,
            'custom_y': self.custom_y,
            'scale': self.scale,
            'width': self.width,
            'height': self.height,
            'max_alpha': self.max_alpha,
            'min_alpha': self.min_alpha,
            'visible_rows': self.visible_rows,
            'disabled_keys': self.disabled_keys,
            'tray_icon_path': self.tray_icon_path,
            'key_style': self.key_style,
            'border_radius': self.border_radius,
            'shadow_size': self.shadow_size,
            'glow_intensity': self.glow_intensity,
            'border_width': self.border_width,
            'key_padding': self.key_padding,
            'colors': self.colors,
            'idle_timeout': self.idle_timeout,
            'fade_duration': self.fade_duration,
            'key_fade_duration': self.key_fade_duration,
        }
        try:
            # Сохраняем неизвестные поля из существующего файла (например default_layout)
            merged = {}
            if os.path.exists(self.config_path):
                try:
                    with open(self.config_path, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                        if isinstance(existing, dict):
                            merged.update(existing)
                except Exception:
                    pass

            # Аккуратно мерджим colors
            if isinstance(merged.get('colors'), dict) and isinstance(data.get('colors'), dict):
                merged['colors'].update(data['colors'])
                data = dict(data)
                data.pop('colors', None)

            merged.update(data)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(merged, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось сохранить {self.config_path}\n\n{e}")

    def _log(self, msg: str):
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{ts}] {msg}\n")
        except Exception:
            pass

    def _load_themes(self, themes_path):
        """Загрузка тем из themes.json (если есть)"""
        if not os.path.exists(themes_path):
            return {}
        try:
            with open(themes_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    
    def _setup_window(self):
        """Настройка окна"""
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        
        if platform.system() == 'Windows':
            self.root.attributes('-alpha', self.max_alpha)
            self.root.configure(bg='black')
        
        self._apply_geometry()
        
        # Делаем окно прозрачным для кликов мыши (click-through)
        if platform.system() == 'Windows' and not self.drag_mode:
            self._set_click_through(True)
    
    def _set_click_through(self, enabled):
        """Включить/выключить режим прозрачности для кликов"""
        if platform.system() != 'Windows':
            return
            
        self.root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        
        if enabled:
            style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style = (style | WS_EX_LAYERED) & ~WS_EX_TRANSPARENT
        
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

    def _apply_geometry(self):
        """Применить геометрию/позицию без пересоздания окна"""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Если есть кастомные координаты - используем их
        if self.custom_x is not None and self.custom_y is not None:
            x = self.custom_x
            y = self.custom_y
        elif self.position == "right":
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

    def _on_drag_start(self, event):
        """Начало перетаскивания"""
        if not self.drag_mode:
            return
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        self.window_start_x = self.root.winfo_x()
        self.window_start_y = self.root.winfo_y()
    
    def _on_drag_motion(self, event):
        """Перетаскивание"""
        if not self.drag_mode:
            return
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y
        new_x = self.window_start_x + dx
        new_y = self.window_start_y + dy
        self.root.geometry(f"+{new_x}+{new_y}")
    
    def _on_drag_end(self, event):
        """Завершение перетаскивания"""
        if not self.drag_mode:
            return
        # Сохраняем новую позицию
        self.custom_x = self.root.winfo_x()
        self.custom_y = self.root.winfo_y()
        self.position = 'custom'
    
    def _toggle_drag_mode(self, enabled=None):
        """Переключить режим перетаскивания"""
        if enabled is None:
            self.drag_mode = not self.drag_mode
        else:
            self.drag_mode = enabled
        
        if platform.system() == 'Windows':
            self._set_click_through(not self.drag_mode)
        
        return self.drag_mode

    def _create_settings_window(self, show=False):
        """Окно настроек с вкладками"""
        if self.settings_window and tk.Toplevel.winfo_exists(self.settings_window):
            if show:
                self._show_settings()
            return

        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("Настройки Keyboard Overlay")
        win.resizable(True, True)
        win.attributes('-topmost', True)
        win.geometry("700x650")

        def on_close():
            try:
                apply_settings(save=True)
            except Exception:
                pass
            win.withdraw()

        win.protocol("WM_DELETE_WINDOW", on_close)

        # Основной контейнер
        main_frame = ttk.Frame(win, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Статус трея
        status_frame = ttk.Frame(main_frame)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(status_frame, textvariable=self.tray_status_var).pack(side=tk.LEFT)

        # --- Notebook (вкладки)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # ==================== Вкладка 1: Основные ====================
        tab_main = ttk.Frame(notebook, padding=15)
        notebook.add(tab_main, text="📍 Основные")

        # --- Переменные UI
        position_var = tk.StringVar(value=self.position)
        width_var = tk.IntVar(value=int(self.width))
        height_var = tk.IntVar(value=int(self.height))
        scale_var = tk.DoubleVar(value=float(self.scale))
        max_alpha_var = tk.DoubleVar(value=float(self.max_alpha))
        min_alpha_var = tk.DoubleVar(value=float(self.min_alpha))
        idle_timeout_var = tk.DoubleVar(value=float(self.idle_timeout))
        key_fade_duration_var = tk.DoubleVar(value=float(self.key_fade_duration))
        drag_mode_var = tk.BooleanVar(value=self.drag_mode)

        # Положение
        lf_pos = ttk.Labelframe(tab_main, text="Положение на экране", padding=10)
        lf_pos.pack(fill=tk.X, pady=(0, 10))

        pos_frame = ttk.Frame(lf_pos)
        pos_frame.pack(fill=tk.X)

        ttk.Label(pos_frame, text="Позиция:").grid(row=0, column=0, sticky="w")
        pos_cb = ttk.Combobox(pos_frame, textvariable=position_var, state="readonly",
                              values=["bottom", "top", "left", "right", "center", "custom"], width=15)
        pos_cb.grid(row=0, column=1, sticky="w", padx=(10, 0))

        # Кнопка для интерактивного перемещения
        def toggle_drag():
            enabled = self._toggle_drag_mode()
            drag_mode_var.set(enabled)
            if enabled:
                drag_btn.configure(text="🔓 Завершить перемещение")
                messagebox.showinfo("Режим перемещения", 
                    "Теперь вы можете перетаскивать клавиатуру мышью!\n\n"
                    "Нажмите кнопку снова чтобы зафиксировать положение.")
            else:
                drag_btn.configure(text="🔒 Переместить клавиатуру")
                position_var.set('custom')

        drag_btn = ttk.Button(pos_frame, text="🔒 Переместить клавиатуру", command=toggle_drag)
        drag_btn.grid(row=0, column=2, padx=(20, 0))

        def reset_position():
            self.custom_x = None
            self.custom_y = None
            self.position = position_var.get() if position_var.get() != 'custom' else 'bottom'
            position_var.set(self.position)
            self._apply_geometry()

        ttk.Button(pos_frame, text="Сбросить", command=reset_position).grid(row=0, column=3, padx=(10, 0))

        # Размеры
        lf_size = ttk.Labelframe(tab_main, text="Размеры", padding=10)
        lf_size.pack(fill=tk.X, pady=(0, 10))

        size_grid = ttk.Frame(lf_size)
        size_grid.pack(fill=tk.X)

        ttk.Label(size_grid, text="Ширина:").grid(row=0, column=0, sticky="w")
        ttk.Entry(size_grid, textvariable=width_var, width=10).grid(row=0, column=1, sticky="w", padx=(10, 30))
        ttk.Label(size_grid, text="Высота:").grid(row=0, column=2, sticky="w")
        ttk.Entry(size_grid, textvariable=height_var, width=10).grid(row=0, column=3, sticky="w", padx=(10, 0))

        ttk.Label(size_grid, text="Масштаб клавиш:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        scale_frame = ttk.Frame(size_grid)
        scale_frame.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=(10, 0))
        ttk.Scale(scale_frame, variable=scale_var, from_=0.3, to=2.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(scale_frame, textvariable=scale_var, width=6).pack(side=tk.LEFT, padx=(5, 0))

        # Прозрачность
        lf_alpha = ttk.Labelframe(tab_main, text="Прозрачность", padding=10)
        lf_alpha.pack(fill=tk.X, pady=(0, 10))

        alpha_grid = ttk.Frame(lf_alpha)
        alpha_grid.pack(fill=tk.X)

        ttk.Label(alpha_grid, text="Активная:").grid(row=0, column=0, sticky="w")
        alpha_frame1 = ttk.Frame(alpha_grid)
        alpha_frame1.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ttk.Scale(alpha_frame1, variable=max_alpha_var, from_=0.2, to=1.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(alpha_frame1, textvariable=max_alpha_var, width=6).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Label(alpha_grid, text="В простое:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        alpha_frame2 = ttk.Frame(alpha_grid)
        alpha_frame2.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 0))
        ttk.Scale(alpha_frame2, variable=min_alpha_var, from_=0.05, to=1.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(alpha_frame2, textvariable=min_alpha_var, width=6).pack(side=tk.LEFT, padx=(5, 0))

        alpha_grid.columnconfigure(1, weight=1)

        # Тайминги
        lf_time = ttk.Labelframe(tab_main, text="Тайминги", padding=10)
        lf_time.pack(fill=tk.X)

        time_grid = ttk.Frame(lf_time)
        time_grid.pack(fill=tk.X)

        ttk.Label(time_grid, text="Переход в простой (сек):").grid(row=0, column=0, sticky="w")
        time_frame1 = ttk.Frame(time_grid)
        time_frame1.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        ttk.Scale(time_frame1, variable=idle_timeout_var, from_=0.0, to=30.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Entry(time_frame1, textvariable=idle_timeout_var, width=8).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Label(time_grid, text="Затухание клавиш (сек):").grid(row=1, column=0, sticky="w", pady=(8, 0))
        time_frame2 = ttk.Frame(time_grid)
        time_frame2.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(8, 0))
        ttk.Scale(time_frame2, variable=key_fade_duration_var, from_=0.05, to=3.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Entry(time_frame2, textvariable=key_fade_duration_var, width=8).pack(side=tk.LEFT, padx=(5, 0))

        time_grid.columnconfigure(1, weight=1)

        # ==================== Вкладка 2: Стиль ====================
        tab_style = ttk.Frame(notebook, padding=15)
        notebook.add(tab_style, text="🎨 Стиль клавиш")

        style_var = tk.StringVar(value=self.key_style)
        border_radius_var = tk.IntVar(value=int(self.border_radius))
        shadow_size_var = tk.IntVar(value=int(self.shadow_size))
        glow_intensity_var = tk.DoubleVar(value=float(self.glow_intensity))
        border_width_var = tk.IntVar(value=int(self.border_width))
        key_padding_var = tk.IntVar(value=int(self.key_padding))

        # Выбор стиля
        lf_style = ttk.Labelframe(tab_style, text="Стиль клавиш", padding=15)
        lf_style.pack(fill=tk.X, pady=(0, 10))

        style_desc = {
            'flat': '⬜ Плоский — минималистичный дизайн без эффектов',
            'rounded': '🔘 Скруглённый — мягкие углы и тени',
            '3d': '📦 3D — объёмный эффект с подсветкой',
            'glass': '🪟 Стеклянный — прозрачность и отблески'
        }

        for i, (style_id, desc) in enumerate(style_desc.items()):
            rb = ttk.Radiobutton(lf_style, text=desc, variable=style_var, value=style_id)
            rb.pack(anchor='w', pady=3)

        # Настройки красивости
        lf_beauty = ttk.Labelframe(tab_style, text="Параметры оформления", padding=15)
        lf_beauty.pack(fill=tk.BOTH, expand=True)

        beauty_grid = ttk.Frame(lf_beauty)
        beauty_grid.pack(fill=tk.BOTH, expand=True)

        # Скругление
        ttk.Label(beauty_grid, text="🔵 Скругление углов:").grid(row=0, column=0, sticky="w")
        radius_frame = ttk.Frame(beauty_grid)
        radius_frame.grid(row=0, column=1, sticky="ew", padx=(15, 0), pady=5)
        ttk.Scale(radius_frame, variable=border_radius_var, from_=0, to=25, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(radius_frame, textvariable=border_radius_var, width=4).pack(side=tk.LEFT, padx=(5, 0))

        # Тень
        ttk.Label(beauty_grid, text="🌑 Размер тени:").grid(row=1, column=0, sticky="w")
        shadow_frame = ttk.Frame(beauty_grid)
        shadow_frame.grid(row=1, column=1, sticky="ew", padx=(15, 0), pady=5)
        ttk.Scale(shadow_frame, variable=shadow_size_var, from_=0, to=15, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(shadow_frame, textvariable=shadow_size_var, width=4).pack(side=tk.LEFT, padx=(5, 0))

        # Свечение
        ttk.Label(beauty_grid, text="✨ Интенсивность свечения:").grid(row=2, column=0, sticky="w")
        glow_frame = ttk.Frame(beauty_grid)
        glow_frame.grid(row=2, column=1, sticky="ew", padx=(15, 0), pady=5)
        ttk.Scale(glow_frame, variable=glow_intensity_var, from_=0.0, to=3.0, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(glow_frame, textvariable=glow_intensity_var, width=4).pack(side=tk.LEFT, padx=(5, 0))

        # Толщина границы
        ttk.Label(beauty_grid, text="📏 Толщина границы:").grid(row=3, column=0, sticky="w")
        border_frame = ttk.Frame(beauty_grid)
        border_frame.grid(row=3, column=1, sticky="ew", padx=(15, 0), pady=5)
        ttk.Scale(border_frame, variable=border_width_var, from_=0, to=8, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(border_frame, textvariable=border_width_var, width=4).pack(side=tk.LEFT, padx=(5, 0))

        # Отступ клавиш
        ttk.Label(beauty_grid, text="↔️ Отступ между клавишами:").grid(row=4, column=0, sticky="w")
        padding_frame = ttk.Frame(beauty_grid)
        padding_frame.grid(row=4, column=1, sticky="ew", padx=(15, 0), pady=5)
        ttk.Scale(padding_frame, variable=key_padding_var, from_=0, to=20, orient="horizontal").pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(padding_frame, textvariable=key_padding_var, width=4).pack(side=tk.LEFT, padx=(5, 0))

        beauty_grid.columnconfigure(1, weight=1)

        # ==================== Вкладка 3: Цвета ====================
        tab_colors = ttk.Frame(notebook, padding=15)
        notebook.add(tab_colors, text="🎨 Цвета")

        color_keys = [
            ('key_bg', '🟫 Фон клавиш'),
            ('key_border', '⬜ Контур клавиш'),
            ('key_text', '📝 Текст клавиш'),
            ('key_pressed', '🔵 Нажатая клавиша (фон)'),
            ('key_pressed_text', '📝 Нажатая клавиша (текст)'),
            ('key_pressed_border', '🔲 Нажатая клавиша (контур)'),
            ('key_shadow', '🌑 Тень клавиш'),
            ('key_highlight', '✨ Подсветка (3D/glass)'),
        ]
        color_vars = {k: tk.StringVar(value=str(self.colors.get(k, ''))) for k, _ in color_keys}

        # Темы
        lf_themes = ttk.Labelframe(tab_colors, text="Темы", padding=10)
        lf_themes.pack(fill=tk.X, pady=(0, 10))

        theme_ids = list(self.themes.keys())
        theme_var = tk.StringVar(value="")

        def apply_theme_from_var():
            tid = theme_var.get().strip()
            if not tid:
                return
            theme = self.themes.get(tid) or {}
            colors = theme.get('colors') if isinstance(theme, dict) else None
            if isinstance(colors, dict):
                for k, _ in color_keys:
                    if k in colors:
                        color_vars[k].set(colors[k])

        if theme_ids:
            theme_frame = ttk.Frame(lf_themes)
            theme_frame.pack(fill=tk.X)
            ttk.Label(theme_frame, text="Выбрать тему:").pack(side=tk.LEFT)
            theme_cb = ttk.Combobox(theme_frame, textvariable=theme_var, state="readonly", values=theme_ids, width=20)
            theme_cb.pack(side=tk.LEFT, padx=(10, 10))
            ttk.Button(theme_frame, text="Применить тему", command=apply_theme_from_var).pack(side=tk.LEFT)
        else:
            ttk.Label(lf_themes, text="Добавьте темы в themes.json").pack()

        # Цвета
        lf_colors = ttk.Labelframe(tab_colors, text="Настройка цветов", padding=10)
        lf_colors.pack(fill=tk.BOTH, expand=True)

        def choose_color(key_name):
            current = color_vars[key_name].get().strip()
            alpha_prefix = ""
            rgb = current
            if current.startswith("#") and len(current) == 9:
                alpha_prefix = current[:3]
                rgb = "#" + current[3:]
            try:
                picked = colorchooser.askcolor(color=rgb, parent=win)
                if picked and picked[1]:
                    new_rgb = picked[1]
                    if alpha_prefix:
                        color_vars[key_name].set(alpha_prefix + new_rgb.lstrip("#"))
                    else:
                        color_vars[key_name].set(new_rgb)
            except Exception:
                pass

        colors_canvas = tk.Canvas(lf_colors, highlightthickness=0)
        colors_scrollbar = ttk.Scrollbar(lf_colors, orient="vertical", command=colors_canvas.yview)
        colors_inner = ttk.Frame(colors_canvas)

        colors_inner.bind("<Configure>", lambda e: colors_canvas.configure(scrollregion=colors_canvas.bbox("all")))
        colors_canvas.create_window((0, 0), window=colors_inner, anchor="nw")
        colors_canvas.configure(yscrollcommand=colors_scrollbar.set)

        colors_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        colors_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        for i, (k, label) in enumerate(color_keys):
            row_frame = ttk.Frame(colors_inner)
            row_frame.pack(fill=tk.X, pady=4)
            ttk.Label(row_frame, text=label, width=28).pack(side=tk.LEFT)
            entry = ttk.Entry(row_frame, textvariable=color_vars[k], width=14)
            entry.pack(side=tk.LEFT, padx=(10, 5))
            ttk.Button(row_frame, text="...", width=3, command=lambda kk=k: choose_color(kk)).pack(side=tk.LEFT)

        # ==================== Вкладка 4: Клавиши ====================
        tab_keys = ttk.Frame(notebook, padding=15)
        notebook.add(tab_keys, text="⌨️ Клавиши")

        row_vars = []
        for i in range(4):
            val = bool(self.visible_rows[i]) if i < len(self.visible_rows) else True
            row_vars.append(tk.BooleanVar(value=val))

        # Отображаемые ряды
        lf_rows = ttk.Labelframe(tab_keys, text="Отображаемые ряды", padding=10)
        lf_rows.pack(fill=tk.X, pady=(0, 10))

        row_names = [
            "Ряд 1: ` 1 2 3 4 5 6 7 8 9 0 - =",
            "Ряд 2: Q W E R T Y U I O P [ ] \\",
            "Ряд 3: A S D F G H J K L ; '",
            "Ряд 4: Z X C V B N M , . /"
        ]

        for i, name in enumerate(row_names):
            ttk.Checkbutton(lf_rows, text=name, variable=row_vars[i]).pack(anchor='w', pady=2)

        # Отключение отдельных клавиш
        lf_disable = ttk.Labelframe(tab_keys, text="Отключить отдельные клавиши", padding=10)
        lf_disable.pack(fill=tk.BOTH, expand=True)

        ttk.Label(lf_disable, text="Снимите галочки с клавиш, которые не хотите отображать:", 
                  foreground='gray').pack(anchor='w', pady=(0, 10))

        # Создаём словарь переменных для каждой клавиши
        key_vars = {}  # {"row_0": {"1": BooleanVar, ...}, ...}

        keys_notebook = ttk.Notebook(lf_disable)
        keys_notebook.pack(fill=tk.BOTH, expand=True)

        for row_idx, row in enumerate(self.english_layout):
            row_tab = ttk.Frame(keys_notebook, padding=10)
            keys_notebook.add(row_tab, text=f"Ряд {row_idx + 1}")
            
            key_vars[f"row_{row_idx}"] = {}
            disabled_in_row = self.disabled_keys.get(f"row_{row_idx}", [])
            
            # Создаём сетку клавиш
            keys_frame = ttk.Frame(row_tab)
            keys_frame.pack(fill=tk.BOTH, expand=True)
            
            for col_idx, key_char in enumerate(row):
                is_enabled = key_char not in disabled_in_row
                var = tk.BooleanVar(value=is_enabled)
                key_vars[f"row_{row_idx}"][key_char] = var
                
                # Рамка для клавиши
                key_frame = ttk.Frame(keys_frame)
                key_frame.grid(row=col_idx // 7, column=col_idx % 7, padx=3, pady=3)
                
                cb = ttk.Checkbutton(key_frame, text=key_char.upper(), variable=var, width=4)
                cb.pack()

            # Кнопки управления
            btn_frame = ttk.Frame(row_tab)
            btn_frame.pack(fill=tk.X, pady=(10, 0))
            
            def select_all(ridx=row_idx):
                for kv in key_vars[f"row_{ridx}"].values():
                    kv.set(True)
            
            def deselect_all(ridx=row_idx):
                for kv in key_vars[f"row_{ridx}"].values():
                    kv.set(False)
            
            ttk.Button(btn_frame, text="Выбрать все", command=select_all).pack(side=tk.LEFT, padx=(0, 5))
            ttk.Button(btn_frame, text="Снять все", command=deselect_all).pack(side=tk.LEFT)

        # ==================== Кнопки внизу ====================
        btns = ttk.Frame(main_frame)
        btns.pack(fill=tk.X, pady=(12, 0))

        def apply_settings(save=False):
            try:
                pos = position_var.get().strip()
                if pos != 'custom':
                    self.position = pos
                    self.custom_x = None
                    self.custom_y = None
                else:
                    self.position = 'custom'
                
                self.width = int(width_var.get())
                self.height = int(height_var.get())
                self.scale = float(scale_var.get())
                self.max_alpha = float(max_alpha_var.get())
                self.min_alpha = float(min_alpha_var.get())
                self.idle_timeout = float(idle_timeout_var.get())
                self.key_fade_duration = float(key_fade_duration_var.get())

                # Стиль
                self.key_style = style_var.get()
                self.border_radius = int(border_radius_var.get())
                self.shadow_size = int(shadow_size_var.get())
                self.glow_intensity = float(glow_intensity_var.get())
                self.border_width = int(border_width_var.get())
                self.key_padding = int(key_padding_var.get())

                # sanity
                self.max_alpha = max(0.05, min(1.0, self.max_alpha))
                self.min_alpha = max(0.05, min(1.0, self.min_alpha))
                if self.min_alpha > self.max_alpha:
                    self.min_alpha, self.max_alpha = self.max_alpha, self.min_alpha
                self.idle_timeout = max(0.0, float(self.idle_timeout))
                self.key_fade_duration = max(0.02, float(self.key_fade_duration))

                self.visible_rows = [v.get() for v in row_vars]

                # Собираем отключённые клавиши
                self.disabled_keys = {}
                for row_key, keys_dict in key_vars.items():
                    disabled = []
                    for key_char, var in keys_dict.items():
                        if not var.get():
                            disabled.append(key_char)
                    if disabled:
                        self.disabled_keys[row_key] = disabled

                for k, _ in color_keys:
                    val = color_vars[k].get().strip()
                    if val:
                        self.colors[k] = val

                self._apply_geometry()
                self.current_alpha = min(self.current_alpha, self.max_alpha)
                self.target_alpha = min(self.target_alpha, self.max_alpha)
                if platform.system() == 'Windows':
                    self.root.attributes('-alpha', self.current_alpha)

                if save:
                    self._save_config()
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось применить настройки:\n\n{e}")

        ttk.Button(btns, text="💾 Сохранить", command=lambda: apply_settings(save=True)).pack(side=tk.RIGHT)
        ttk.Button(btns, text="✅ Применить", command=lambda: apply_settings(save=False)).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(btns, text="📥 Скрыть в трей", command=on_close).pack(side=tk.LEFT)

        # Позиция окна настроек по центру
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        w = win.winfo_reqwidth()
        h = win.winfo_reqheight()
        win.geometry(f"+{(sw - w)//2}+{(sh - h)//2}")

        if not show:
            win.withdraw()

    def _show_settings(self):
        if not self.settings_window:
            self._create_settings_window(show=True)
            return
        try:
            self.settings_window.deiconify()
            self.settings_window.lift()
            self.settings_window.focus_force()
        except Exception:
            pass

    def _toggle_overlay_visibility(self):
        try:
            if self.root.state() == 'withdrawn':
                self.root.deiconify()
                self.root.lift()
            else:
                self.root.withdraw()
        except Exception:
            try:
                self.root.withdraw()
            except Exception:
                pass

    def _quit(self):
        """Корректный выход"""
        try:
            self._save_config()
        except Exception:
            pass
        try:
            if self.listener:
                self.listener.stop()
        except Exception:
            pass
        try:
            if self.tray_icon:
                self.tray_icon.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _setup_tray(self):
        """Иконка в трее + меню (если доступны зависимости)"""
        if self.tray_icon is not None:
            return

        self._log("Tray setup: start")
        if pystray is None or Image is None:
            self._log("Tray setup: pystray/PIL not available; tray disabled")
            try:
                self.tray_status_var.set("Трей: недоступен (нет pystray/Pillow)")
            except Exception:
                pass
            return

        def ensure_icon_file(path):
            try:
                if os.path.exists(path):
                    return
                img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(0, 212, 255, 255))
                draw.rounded_rectangle((6, 6, 58, 58), radius=10, outline=(0, 0, 0, 220), width=2)
                try:
                    font = ImageFont.load_default() if ImageFont else None
                except Exception:
                    font = None
                draw.text((24, 18), "K", fill=(0, 0, 0, 255), font=font)
                ext = os.path.splitext(path)[1].lower()
                if ext == '.ico':
                    img.save(path, format='ICO')
                else:
                    img.save(path, format='PNG')
            except Exception:
                pass

        def load_image_from_file(path):
            try:
                return Image.open(path)
            except Exception:
                img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(0, 212, 255, 255))
                draw.text((24, 18), "K", fill=(0, 0, 0, 255))
                return img

        def post_to_tk(fn):
            self.root.after(0, fn)

        menu = pystray.Menu(
            pystray.MenuItem("Настройки", lambda: post_to_tk(self._show_settings)),
            pystray.MenuItem("Выход", lambda: post_to_tk(self._quit)),
        )

        icon_path = str(self.tray_icon_path or 'tray.ico')
        ensure_icon_file(icon_path)
        icon_image = load_image_from_file(icon_path)
        try:
            icon_image = icon_image.convert('RGBA')
        except Exception:
            pass

        self.tray_icon = pystray.Icon("keyboard_overlay", icon_image, "Keyboard Overlay", menu)
        try:
            self.tray_status_var.set("Трей: запускается...")
        except Exception:
            pass

        def run_tray():
            try:
                self._log("Tray thread: run() begin")
                self.tray_icon.run()
                self._log("Tray thread: run() finished")
            except Exception as e:
                self._log(f"Tray thread: exception: {repr(e)}")
                self.tray_icon = None
                try:
                    self.root.after(0, lambda: messagebox.showwarning(
                        "Трей не запустился",
                        "Не удалось показать иконку в трее.\n"
                        "Попробуйте перезапустить программу.\n\n"
                        f"Детали: {e}"
                    ))
                except Exception:
                    pass
                try:
                    self.tray_status_var.set("Трей: ошибка (см. keyboard_overlay.log)")
                except Exception:
                    pass

        self.tray_thread = threading.Thread(target=run_tray, daemon=False)
        self.tray_thread.start()

        def post_check():
            if self.tray_icon is None:
                try:
                    self.tray_status_var.set("Трей: не запущен (см. keyboard_overlay.log)")
                except Exception:
                    pass
            else:
                try:
                    self.tray_status_var.set("Трей: активен (ищите в ^ рядом с часами)")
                except Exception:
                    pass
            self._log(f"Tray check: tray_icon is {'set' if self.tray_icon else 'None'}")

        self.root.after(1000, post_check)
    
    def _detect_windows_layout(self):
        """Определение раскладки из Windows"""
        if platform.system() == 'Windows':
            try:
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                hwnd = user32.GetForegroundWindow()
                thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
                klid = user32.GetKeyboardLayout(thread_id)
                lid = klid & 0xFFFF
                
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
            # Обновляем раскладку для отображения (без очистки pressed_keys)
            new_layout = self._detect_windows_layout()
            if new_layout != self.current_display_layout:
                self.current_display_layout = new_layout
            
            if hasattr(key, 'char') and key.char:
                char = key.char.lower()
                current_time = time.time()
                
                # Добавляем сам символ
                self.pressed_keys[char] = (current_time, 1.0)
                
                # Также добавляем эквивалент на другой раскладке для надёжности
                # Это помогает когда pynput возвращает символы не той раскладки
                if char in self.ru_to_en_map:
                    # Это русский символ, добавляем английский эквивалент
                    en_char = self.ru_to_en_map[char]
                    self.pressed_keys[en_char] = (current_time, 1.0)
                elif char in self.en_to_ru_map:
                    # Это английский символ, добавляем русский эквивалент
                    ru_char = self.en_to_ru_map[char]
                    self.pressed_keys[ru_char] = (current_time, 1.0)
                
                self.last_activity_time = current_time
                self.target_alpha = self.max_alpha
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
        
        if self.current_display_layout == 'ru':
            layout = self.russian_layout
        else:
            layout = self.english_layout

        visible = []
        for row_idx, row in enumerate(layout):
            if row_idx < len(self.visible_rows) and not self.visible_rows[row_idx]:
                continue
            visible.append((row_idx, row))
        if not visible:
            return
        
        key_width = 50 * self.scale
        key_height = 50 * self.scale
        key_spacing = self.key_padding * self.scale
        
        max_row_width = 0
        for row_idx, row in visible:
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
        
        for display_row_idx, (row_idx, row) in enumerate(visible):
            offset = 0
            if row_idx == 1:
                offset = key_width * 0.25
            elif row_idx == 2:
                offset = key_width * 0.5
            elif row_idx == 3:
                offset = key_width * 0.75
            
            x_start = start_x + offset
            
            # Проверяем отключённые клавиши для этого ряда
            disabled_in_row = self.disabled_keys.get(f"row_{row_idx}", [])
            
            for col_idx, char in enumerate(row):
                # Пропускаем отключённые клавиши
                en_char = char
                if self.current_display_layout == 'ru' and row_idx < len(self.english_layout):
                    if col_idx < len(self.english_layout[row_idx]):
                        en_char = self.english_layout[row_idx][col_idx]
                
                if en_char in disabled_in_row:
                    continue
                
                x = x_start + col_idx * (key_width + key_spacing)
                y = start_y + display_row_idx * (key_height + key_spacing)
                
                is_pressed = False
                press_alpha = 0.0
                
                # Прямая проверка символа
                if char in self.pressed_keys:
                    is_pressed = True
                    press_alpha = self.pressed_keys[char][1]
                
                # Для русской раскладки: проверяем английский эквивалент
                # (pynput может возвращать английские символы даже при русской раскладке)
                elif self.current_display_layout == 'ru':
                    en_equivalent = self.ru_to_en_map.get(char)
                    if en_equivalent and en_equivalent in self.pressed_keys:
                        is_pressed = True
                        press_alpha = self.pressed_keys[en_equivalent][1]
                
                # Для английской раскладки: проверяем русский эквивалент
                # (если пользователь нажимал при русской раскладке)
                elif self.current_display_layout == 'en':
                    ru_equivalent = self.en_to_ru_map.get(char)
                    if ru_equivalent and ru_equivalent in self.pressed_keys:
                        is_pressed = True
                        press_alpha = self.pressed_keys[ru_equivalent][1]
                
                self._draw_key(x, y, key_width, key_height, char, press_alpha)
    
    def _draw_key(self, x, y, width, height, char, press_alpha):
        """Рисуем одну клавишу с учётом стиля"""
        is_pressed = press_alpha > 0.05
        
        if is_pressed:
            bg_color = self.colors['key_pressed']
            text_color = self.colors.get('key_pressed_text', '#000000')
            border_color = self.colors.get('key_pressed_border', self.colors['key_pressed'])
            bw = max(self.border_width, 3)
        else:
            bg_color = self.colors['key_bg']
            text_color = self.colors['key_text']
            border_color = self.colors['key_border']
            bw = self.border_width
        
        bg_rgb = self._hex_to_rgb(bg_color)
        border_rgb = self._hex_to_rgb(border_color)
        shadow_rgb = self._hex_to_rgb(self.colors.get('key_shadow', '#20000000'))
        highlight_rgb = self._hex_to_rgb(self.colors.get('key_highlight', '#40ffffff'))
        
        radius = self.border_radius * self.scale
        shadow_size = self.shadow_size * self.scale
        
        # ===== Стиль: Flat =====
        if self.key_style == 'flat':
            self.canvas.create_rectangle(
                x, y, x + width, y + height,
                fill=bg_rgb if bg_rgb else '',
                outline=border_rgb if border_rgb else border_color,
                width=bw,
                tags="key"
            )
        
        # ===== Стиль: Rounded =====
        elif self.key_style == 'rounded':
            # Тень
            if shadow_size > 0 and shadow_rgb and not is_pressed:
                self._draw_rounded_rect(x + shadow_size, y + shadow_size, width, height, radius, shadow_rgb, '', 0)
            
            self._draw_rounded_rect(x, y, width, height, radius, bg_rgb, border_rgb, bw)
        
        # ===== Стиль: 3D =====
        elif self.key_style == '3d':
            depth = 4 * self.scale
            
            if not is_pressed:
                # Нижняя часть (тень 3D)
                darker = self._darken_color(bg_rgb, 0.6) if bg_rgb else '#333333'
                self._draw_rounded_rect(x, y + depth, width, height, radius, darker, '', 0)
                
                # Верхняя часть
                self._draw_rounded_rect(x, y, width, height, radius, bg_rgb, border_rgb, bw)
                
                # Подсветка сверху
                if highlight_rgb:
                    self._draw_rounded_rect(x + 2, y + 2, width - 4, height / 3, radius / 2, highlight_rgb, '', 0)
            else:
                # Нажатая - без 3D эффекта, смещённая вниз
                self._draw_rounded_rect(x, y + depth / 2, width, height, radius, bg_rgb, border_rgb, bw)
        
        # ===== Стиль: Glass =====
        elif self.key_style == 'glass':
            # Основа
            self._draw_rounded_rect(x, y, width, height, radius, bg_rgb, border_rgb, bw)
            
            # Верхний блик
            if highlight_rgb and not is_pressed:
                self._draw_rounded_rect(x + 3, y + 2, width - 6, height / 2.5, radius / 2, highlight_rgb, '', 0)
            
            # Отражение снизу
            if not is_pressed:
                reflection = self._hex_to_rgb('#10ffffff')
                if reflection:
                    self._draw_rounded_rect(x + 3, y + height * 0.6, width - 6, height / 3, radius / 2, reflection, '', 0)
        
        else:
            # Fallback
            self.canvas.create_rectangle(
                x, y, x + width, y + height,
                fill=bg_rgb if bg_rgb else '',
                outline=border_rgb if border_rgb else border_color,
                width=bw,
                tags="key"
            )
        
        # Эффект свечения при нажатии
        if press_alpha > 0.3 and self.glow_intensity > 0:
            glow = 3 * self.scale * press_alpha * self.glow_intensity
            for i in range(int(glow)):
                alpha = (1 - i / glow) * 0.3
                glow_color = self._apply_alpha(self.colors['key_pressed'], alpha)
                self.canvas.create_rectangle(
                    x - i, y - i,
                    x + width + i, y + height + i,
                    fill='',
                    outline=glow_color,
                    width=1,
                    tags="key"
                )
        
        # Текст
        font_size = max(12, int(16 * self.scale))
        text_y = y + height / 2
        
        if self.key_style == '3d' and not is_pressed:
            text_y = y + height / 2
        elif self.key_style == '3d' and is_pressed:
            text_y = y + height / 2 + 2 * self.scale
        
        # Тень текста
        if not is_pressed and self.shadow_size > 0:
            self.canvas.create_text(
                x + width / 2 + 1, text_y + 1,
                text=char.upper(),
                fill='#202020',
                font=('Arial', font_size, 'bold'),
                tags="key"
            )
        
        # Основной текст
        self.canvas.create_text(
            x + width / 2, text_y,
            text=char.upper(),
            fill=text_color,
            font=('Arial', font_size, 'bold'),
            tags="key"
        )
    
    def _draw_rounded_rect(self, x, y, width, height, radius, fill, outline, outline_width):
        """Рисует скруглённый прямоугольник"""
        if radius <= 0:
            self.canvas.create_rectangle(
                x, y, x + width, y + height,
                fill=fill if fill else '',
                outline=outline if outline else '',
                width=outline_width,
                tags="key"
            )
            return
        
        # Ограничиваем радиус
        radius = min(radius, width / 2, height / 2)
        
        points = [
            x + radius, y,
            x + width - radius, y,
            x + width, y,
            x + width, y + radius,
            x + width, y + height - radius,
            x + width, y + height,
            x + width - radius, y + height,
            x + radius, y + height,
            x, y + height,
            x, y + height - radius,
            x, y + radius,
            x, y,
            x + radius, y,
        ]
        
        self.canvas.create_polygon(
            points,
            fill=fill if fill else '',
            outline=outline if outline else '',
            width=outline_width,
            smooth=True,
            tags="key"
        )
    
    def _darken_color(self, hex_color, factor):
        """Затемняет цвет"""
        if not hex_color:
            return '#333333'
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            r = int(r * factor)
            g = int(g * factor)
            b = int(b * factor)
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)
        return hex_color
    
    def _apply_alpha(self, hex_color, alpha):
        """Применяет альфа к цвету (возвращает RGB с уменьшенной яркостью)"""
        if not hex_color:
            return '#000000'
        hex_color = hex_color.lstrip('#')
        if len(hex_color) >= 6:
            if len(hex_color) == 8:
                hex_color = hex_color[2:]  # Убираем альфу
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            r = int(r * alpha)
            g = int(g * alpha)
            b = int(b * alpha)
            return '#{:02x}{:02x}{:02x}'.format(r, g, b)
        return hex_color
    
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
        
        # Обновляем раскладку для отображения (без очистки pressed_keys)
        new_layout = self._detect_windows_layout()
        if new_layout != self.current_display_layout:
            self.current_display_layout = new_layout
        
        time_since_activity = current_time - self.last_activity_time
        if time_since_activity > self.idle_timeout:
            fade = min(1.0, (time_since_activity - self.idle_timeout) / self.fade_duration)
            self.target_alpha = max(self.min_alpha, self.max_alpha - fade * (self.max_alpha - self.min_alpha))
        else:
            self.target_alpha = self.max_alpha
        
        alpha_diff = self.target_alpha - self.current_alpha
        self.current_alpha += alpha_diff * 0.1
        
        if platform.system() == 'Windows':
            self.root.attributes('-alpha', self.current_alpha)
        
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
        
        self._draw_keyboard()
        
        self.root.after(16, self._animate)
    
    def run(self):
        """Запуск"""
        self.root.mainloop()


if __name__ == "__main__":
    app = KeyboardOverlay()
    app.run()

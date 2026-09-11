# 🎵 Spotify Mini Player — Modern GTK4 / Libadwaita HUD

<p align="center">
  <img src="assets/icon.png" width="128" height="128" alt="Spotify Mini Player Logo">
</p>

<p align="center">
  <strong>Spotify Mini Player</strong> is a sleek, lightweight, semi-transparent HUD and desktop companion for Spotify on Linux. Built with GTK4 & Libadwaita, it provides real-time bi-directional MPRIS controls, volume synchronization, native LevelDB queue parsing, smart OSD auto-hide behavior, and automated multi-language localization.
</p>

<p align="center">
  <!-- Place your demo.gif or preview screenshot in the assets folder -->
  <img src="assets/demo.gif" alt="Spotify Mini Player Demo" width="700">
</p>

<p align="center">
  <a href="https://boosty.to/xronni/single-payment/donation/809763/target?share=target_link"><img src="https://img.shields.io/badge/Boosty-Support%20Project-orange?style=flat&logo=boosty" alt="Support on Boosty"></a>
  <img src="https://img.shields.io/badge/Platform-Linux-FCC624?style=flat&logo=linux&logoColor=black" alt="Platform: Linux">
  <img src="https://img.shields.io/badge/GTK-4.0-blue?logo=gnome" alt="GTK 4.0">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License: MIT">
</p>

---

## 🌐 Navigation / Навигация
* 🇺🇸 [English Version](#-english-version)
* 🇷🇺 [Русская версия](#-русская-версия)

---

## 🇺🇸 English Version

### ✨ Key Features
* 📋 **Real Spotify Queue Parsing:** Reads directly from Spotify's local LevelDB storage. Displays genuine upcoming tracks, currently playing songs, and previously played tracks with correct sequence numbering and full context (playlist/album) naming.
* 🔊 **Bi-directional Volume Sync:** Real-time volume slider synchronized with Spotify via MPRIS D-Bus. Adjust volume seamlessly using the slider or mouse scroll wheel over the player.
* 👁️ **Intelligent OSD HUD Mode:** Pops up smoothly when tracks change or media keys are pressed, and fades out automatically after 3.5 seconds. Automatically hides when Spotify Desktop is in focus to avoid screen clutter.
* 📌 **Always-on-Top Pinning:** Pin button keeps the player always visible above other windows.
* 🌍 **Seamless Auto-Localization (i18n):** Automatically detects Spotify's active UI language (`ru`, `en`, `de`, `es`, `fr`) and auto-restarts instantly on language change.
* 🫧 **Glassmorphism Aesthetic:** Dark frosted glass theme (`rgba(18, 18, 22, 0.85)`), animated audio visualizer bars, and smooth revealer animations.

---

### 📥 1. Installation & Requirements

#### Prerequisites:
* **Operating System:** Linux (Ubuntu 22.04+, Debian 12+, Arch, Fedora)
* **Python:** 3.10 or newer
* **Spotify Desktop:** Official Linux client
* **System Packages:**
  ```bash
  sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libx11-6
  ```

#### Option A: Install Debian / Ubuntu Package (.deb) — Recommended
Download `spotify-mini-player_1.1.0_all.deb` from the [Releases page](https://github.com/Xronni/spotify-mini-player/releases/latest) and install:
```bash
sudo dpkg -i spotify-mini-player_1.1.0_all.deb
sudo apt install -f  # automatically install dependencies if needed
```

#### Option B: Standalone Archive or Git Clone
1. Clone the repository or download `spotify-mini-player-v1.1.0-linux-x86_64.tar.gz`:
   ```bash
   git clone https://github.com/Xronni/spotify-mini-player.git
   cd spotify-mini-player
   ```
2. Run the launcher:
   ```bash
   ./run.sh
   ```
3. (Optional) Install desktop shortcut to your application menu:
   ```bash
   ./install.sh
   ```

> [!TIP]
> Running `./install.sh` registers Spotify Mini Player in your GNOME/desktop applications launcher and dashboard.

---

### ⚙️ 2. How It Works

* **D-Bus MPRIS Integration:** Communicates over `org.mpris.MediaPlayer2.spotify` for low-latency playback controls, metadata synchronization, and album artwork caching.
* **LevelDB Queue Engine:** Reverse-scans Spotify's internal cache records to reconstruct active playlist names, upcoming track queues, and history without requiring Spotify Web API developer tokens.
* **Smart Visibility Guard:** X11 window polling monitors whether the native Spotify desktop client is on-screen or minimized, ensuring the Mini Player never gets in your way.

---

### ⌨️ 3. Controls & Hotkeys

| Action / Gesture | Target | Result |
|:---:|:---:|---|
| **Hover** | Mini Player Card | Pauses auto-hide timer; keeps window fully visible |
| **Mouse Scroll** | Player or Volume Slider | Smoothly raises or lowers Spotify volume |
| **Click** | Album Cover / Logo | Raises and focuses the Spotify Desktop window |
| **Click** | 📋 Queue Button | Slides down the scrollable queue drawer with auto-centering |
| **Click** | 📌 Pin Button | Toggles "Always on Top" pinned mode |
| **Click** | ▶ Track Row | Plays the selected queue track silently via MPRIS |
| **Global Media Keys** | Keyboard | Triggers instant smooth OSD pop-up and skips track |
| **CLI / Custom Shortcuts** | `spotify-mini-player --next` / `--prev` / `--play-pause` / `--toggle-visible` | Bind to system shortcuts for instant direct control |

---

## 🇷🇺 Русская версия

### ✨ Основные возможности
* 📋 **Парсинг настоящей очереди Spotify:** Прямое чтение локального хранилища LevelDB Spotify. Отображение актуального списка очереди, истории («Ранее играли») и играющего трека со сквозной нумерацией и точным названием плейлиста/альбома.
* 🔊 **Двусторонняя синхронизация громкости:** Мгновенный отклик ползунка громкости и кнопки Mute/Unmute через системный протокол MPRIS D-Bus. Поддержка регулировки колесиком мыши.
* 👁️ **Умный OSD HUD режим:** Плавное всплывание при переключении треков или паузе глобальными горячими клавишами. Автоскрытие через 3.5 секунды. Автоматически скрывается, когда развернуто основное окно Spotify.
* 📌 **Закрепление поверх всех окон (Pin):** Фиксация мини-плеера на экране в любой удобной позиции.
* 🌍 **Автоматическая локализация (i18n):** Распознавание языка интерфейса Spotify (`ru`, `en`, `de`, `es`, `fr`) с мгновенным автоперезапуском при смене языка в настройках Spotify.
* 🫧 **Стильный дизайн (Glassmorphism):** Полупрозрачное матовое стекло, тонкие границы, акцентная анимация частот и плавное раскрытие очереди.

---

### 📥 1. Установка и запуск

#### Системные требования:
* **ОС:** Linux (Ubuntu 22.04+, Debian, Arch, Fedora)
* **Python:** 3.10 или выше
* **Spotify Desktop:** Официальный клиент для Linux
* **Зависимости:**
  ```bash
  sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libx11-6
  ```

#### Вариант А: Установка пакета Debian / Ubuntu (.deb) — Рекомендуется
Скачайте `spotify-mini-player_1.1.0_all.deb` со страницы [Релизов](https://github.com/Xronni/spotify-mini-player/releases/latest) и выполните:
```bash
sudo dpkg -i spotify-mini-player_1.1.0_all.deb
sudo apt install -f  # автоматическая установка зависимостей при необходимости
```

#### Вариант Б: Портативный запуск или сборка из Git
1. Клонируйте репозиторий или скачайте архив `spotify-mini-player-v1.1.0-linux-x86_64.tar.gz`:
   ```bash
   git clone https://github.com/Xronni/spotify-mini-player.git
   cd spotify-mini-player
   ```
2. Запустите плеер:
   ```bash
   ./run.sh
   ```
3. (Опционально) Установите ярлык в системное меню приложений GNOME:
   ```bash
   ./install.sh
   ```

> [!TIP]
> Скрипт `./install.sh` регистрирует мини-плеер в системном меню приложений, связывая его с официальной иконкой и автозапуском.

---

### ⚙️ 2. Механизм работы

* **Интеграция с MPRIS:** Взаимодействие с сервисом `org.mpris.MediaPlayer2.spotify` без задержек и без использования внешних API/токенов разработчика.
* **Движок очереди LevelDB:** Поиск и десериализация актуальных записей плейлиста и очередей прямо из локального дискового кэша Spotify.
* **Умный контроль фокуса:** Постоянный опрос X11-окон предотвращает дублирование интерфейса, если активно официальное окно Spotify.

---

### ⌨️ 3. Управление и горячие клавиши

| Действие / Жест | Элемент | Результат |
|:---:|:---:|---|
| **Наведение курсора** | Карточка плеера | Сброс таймера автоскрытия, плеер остается видимым |
| **Колесико мыши** | Карточка или ползунок | Быстрая плавная регулировка громкости Spotify |
| **Клик** | Обложка / Логотип | Разворачивает и фокусирует главное окно Spotify |
| **Клик** | 📋 Кнопка очереди | Раскрывает панель очереди с автоцентрированием на текущем треке |
| **Клик** | 📌 Кнопка булавки | Фиксация мини-плеера поверх всех окон (Always on Top) |
| **Клик** | ▶ Строка трека | Мгновенный тихий запуск трека из очереди |
| **Медиа-клавиши** | Клавиатура | Мгновенно переключает трек и показывает OSD-виджет |
| **CLI / Хоткеи системы** | `spotify-mini-player --next` / `--prev` / `--play-pause` / `--toggle-visible` | Можно назначить на любые горячие клавиши в настройках системы |

---

## 🤝 Support the Project / Поддержать проект
If you find this project useful, you can support further development:  
Если вам понравился мини-плеер, вы можете поддержать разработку:

* 🍊 **[Boosty (Поддержать автора)](https://boosty.to/xronni/single-payment/donation/809763/target?share=target_link)**

---

## 📄 License
Released under the [MIT License](LICENSE).

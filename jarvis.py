import os
import re
import time
import math
import tempfile
import asyncio
import threading
import queue
import sys
import webbrowser
import subprocess
from difflib import SequenceMatcher
from dataclasses import dataclass
from typing import Optional

# Dependências sugeridas:
# pip install SpeechRecognition pyaudio edge-tts pygame requests faster-whisper
# Para o modo chatbot, defina a variável de ambiente OPENROUTER_API_KEY.

try:
    import speech_recognition as sr
except Exception:
    sr = None

try:
    import edge_tts
except Exception:
    edge_tts = None

try:
    import pygame
except Exception:
    pygame = None

try:
    import requests as _requests
except Exception:
    _requests = None

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except Exception:
    spotipy = None
    SpotifyOAuth = None

try:
    from faster_whisper import WhisperModel as _FasterWhisperModel
except Exception:
    _FasterWhisperModel = None


# ── Visual Window ──────────────────────────────────────────────────────────────

class JarvisWindow:
    """Círculo pulsante estilo assistente de voz usando pygame."""

    WIDTH, HEIGHT = 400, 400
    CENTER = (200, 200)
    BG_COLOR = (10, 10, 20)

    IDLE = "idle"
    SPEAKING = "speaking"

    def __init__(self):
        self.state = self.IDLE
        self._target_state = self.IDLE
        self._running = False
        self._thread = None
        self._phase = 0.0
        self._speaking_mix = 0.0

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def set_speaking(self, speaking: bool):
        self._target_state = self.SPEAKING if speaking else self.IDLE

    def _loop(self):
        import pygame as pg
        pg.display.init()
        screen = pg.display.set_mode((self.WIDTH, self.HEIGHT))
        pg.display.set_caption("Jarvis")
        clock = pg.time.Clock()

        while self._running:
            for event in pg.event.get():
                if event.type == pg.QUIT:
                    self._running = False

            self._phase += 0.05
            target_mix = 1.0 if self._target_state == self.SPEAKING else 0.0
            self._speaking_mix += (target_mix - self._speaking_mix) * 0.12
            self.state = self.SPEAKING if self._speaking_mix >= 0.5 else self.IDLE
            screen.fill(self.BG_COLOR)
            self._draw(screen, pg)
            pg.display.flip()
            clock.tick(60)

        pg.display.quit()

    def _draw(self, screen, pg):
        cx, cy = self.CENTER
        t = self._phase
        mix = self._speaking_mix

        idle_pulse = math.sin(t * 0.8)
        idle_radius = int(80 + idle_pulse * 6 + mix * 10)
        idle_alpha = int(80 * (1.0 - mix * 0.75))
        idle_surf = pg.Surface((self.WIDTH, self.HEIGHT), pg.SRCALPHA)
        pg.draw.circle(idle_surf, (40, 80, 160, max(0, idle_alpha)), (cx, cy), idle_radius, 2)
        screen.blit(idle_surf, (0, 0))

        core_outer_r = int(36 + mix * 8 + math.sin(t * (1.0 + mix * 2.0)) * (2 + mix * 4))
        core_inner_r = max(8, int(26 + mix * 2))
        core_outer_color = (int(30 + mix * 10), int(60 + mix * 84), int(130 + mix * 125))
        core_inner_color = (int(60 + mix * 120), int(100 + mix * 130), int(200 + mix * 55))
        pg.draw.circle(screen, core_outer_color, (cx, cy), core_outer_r)
        pg.draw.circle(screen, core_inner_color, (cx, cy), core_inner_r)

        if mix > 0.01:
            for i in range(3):
                offset = i * (math.pi * 2 / 3)
                pulse = math.sin(t * 2 + offset)
                radius = int(110 + mix * 10 + pulse * (8 + mix * 10))
                alpha = int((60 + pulse * 40) * mix)
                surf = pg.Surface((self.WIDTH, self.HEIGHT), pg.SRCALPHA)
                pg.draw.circle(surf, (30, 144, 255, max(0, alpha)), (cx, cy), radius, 2)
                screen.blit(surf, (0, 0))

            for i in range(6):
                angle = t * (0.5 + mix) + i * (math.pi / 3)
                orbit_r = int(70 + mix * 15)
                dx = int(math.cos(angle) * orbit_r)
                dy = int(math.sin(angle) * orbit_r)
                size = max(1, int((5 + math.sin(t * 3 + i) * 2) * mix))
                pg.draw.circle(screen, (100, 200, 255), (cx + dx, cy + dy), size)


@dataclass
class CommandResult:
    handled: bool
    message: str = ""
    should_speak: bool = True


class Jarvis:
    def __init__(self, started_from_startup: bool = False) -> None:
        self.recognizer = sr.Recognizer() if sr else None
        if self.recognizer:
            self.recognizer.dynamic_energy_threshold = False
            self.recognizer.energy_threshold = 160
            self.recognizer.pause_threshold = 0.45
            self.recognizer.non_speaking_duration = 0.25
            self.recognizer.phrase_threshold = 0.18
        self.running = True
        self.voice_name = "pt-BR-AntonioNeural"
        self.openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        self.started_from_startup = started_from_startup
        self.study_opened = False
        self.standby = False
        self.is_speaking = False
        self._command_queue = queue.Queue()
        self._last_voice_command = ""
        self._last_voice_command_at = 0.0

        # Faster Whisper
        if _FasterWhisperModel:
            print("Carregando Faster Whisper...")
            self._fw_model = _FasterWhisperModel("medium", device="cuda", compute_type="float16")
            print("Modelo carregado.")
        else:
            self._fw_model = None

        self.spotify = self._init_spotify()
        self.window = JarvisWindow()
        self.window.start()

        self.apps = {
            "roblox": r"C:\Program Files (x86)\Roblox\Versions\version-6776addb8fbc4d17\RobloxPlayerBeta.exe",
            "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "google chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            "netflix": "https://www.netflix.com/",
            "bloco de notas": "notepad.exe",
            "notepad": "notepad.exe",
            "calculadora": "calc.exe",
            "explorador": "explorer.exe",
            "power bi": r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
            "spotify": r"C:\Users\%USERNAME%\AppData\Roaming\Spotify\Spotify.exe",
            "vs code": r"C:\Users\%USERNAME%\AppData\Local\Programs\Microsoft VS Code\Code.exe",
            "visual studio code": r"C:\Users\%USERNAME%\AppData\Local\Programs\Microsoft VS Code\Code.exe",
        }

        self.kill_map = {
            "roblox": "RobloxPlayerBeta.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "netflix": "chrome.exe",
            "bloco de notas": "notepad.exe",
            "notepad": "notepad.exe",
            "calculadora": "CalculatorApp.exe",
            "explorador": "explorer.exe",
            "power bi": "PBIDesktop.exe",
            "spotify": "Spotify.exe",
            "vs code": "Code.exe",
            "visual studio code": "Code.exe",
        }

    def _speak_edge(self, text: str) -> bool:
        if not edge_tts or not pygame:
            return False

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                temp_path = tmp.name

            async def generate_audio():
                communicate = edge_tts.Communicate(text=text, voice=self.voice_name)
                await communicate.save(temp_path)

            asyncio.run(generate_audio())

            if not pygame.mixer.get_init():
                pygame.mixer.init()

            pygame.mixer.music.load(temp_path)
            self.is_speaking = True
            self.window.set_speaking(True)
            pygame.mixer.music.play()

            while pygame.mixer.music.get_busy():
                time.sleep(0.1)

            self.window.set_speaking(False)
            self.is_speaking = False

            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

            try:
                os.remove(temp_path)
            except Exception:
                pass

            return True
        except Exception:
            return False

    def speak(self, text: str) -> None:
        if not text:
            return
        print(f"Jarvis: {text}")
        self._speak_edge(text)

    def _drain_command_queue(self) -> None:
        while True:
            try:
                self._command_queue.get_nowait()
            except queue.Empty:
                break

    def _should_ignore_voice_command(self, command: str) -> bool:
        if not command:
            return True
        now = time.time()
        cleaned = command.strip().lower()
        if len(cleaned) < 3:
            return True
        if cleaned == self._last_voice_command and (now - self._last_voice_command_at) < 2.5:
            return True
        self._last_voice_command = cleaned
        self._last_voice_command_at = now
        return False

    def _transcribe(self, audio) -> str:
        """Transcreve áudio usando Faster Whisper (GPU) ou Google como fallback."""
        # Faster Whisper
        if self._fw_model:
            try:
                import numpy as np
                raw = np.frombuffer(
                    audio.get_raw_data(convert_rate=16000, convert_width=2),
                    dtype=np.int16,
                ).astype(np.float32) / 32768.0
                segments, _ = self._fw_model.transcribe(raw, language="pt", beam_size=5)
                text = " ".join(s.text for s in segments).strip().lower()
                return text
            except Exception:
                pass

        # Fallback: Google Speech
        try:
            text = self.recognizer.recognize_google(audio, language="pt-BR")
            return text.lower().strip()
        except Exception:
            return ""

    def startup_greeting(self) -> str:
        if self.started_from_startup:
            return "Bem vindo de volta senhor."
        hour = time.localtime().tm_hour
        if 5 <= hour < 12:
            return "Bom dia senhor."
        if 12 <= hour < 18:
            return "Boa tarde senhor."
        return "Boa noite senhor."

    def _is_process_running(self, process_name: str) -> bool:
        process_base = process_name.replace(".exe", "")
        script = f"""
$p = Get-Process -Name '{process_base}' -ErrorAction SilentlyContinue
if ($p) {{ exit 0 }} else {{ exit 1 }}
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _focus_process_window(self, process_name: str) -> bool:
        process_base = process_name.replace(".exe", "")
        script = f"""
$wshell = New-Object -ComObject WScript.Shell
$processes = Get-Process -Name '{process_base}' -ErrorAction SilentlyContinue
foreach ($p in $processes) {{
    if ($p.MainWindowHandle -ne 0 -or $p.MainWindowTitle) {{
        if ($wshell.AppActivate($p.Id)) {{
            Start-Sleep -Milliseconds 200
            exit 0
        }}
    }}
}}
exit 1
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def open_app(self, app_name: str) -> CommandResult:
        path = self.apps.get(app_name)
        if not path:
            return CommandResult(False, "", False)
        try:
            path = os.path.expandvars(path)
            process_name = self.kill_map.get(app_name)
            if app_name == "netflix" and process_name and self._is_process_running(process_name):
                if self._focus_process_window(process_name):
                    return CommandResult(True, "Netflix aberto.")
                return CommandResult(True, "Netflix já está aberto, mas não consegui focar o Google Chrome.")
            if process_name and self._is_process_running(process_name):
                if self._focus_process_window(process_name):
                    return CommandResult(True, f"{app_name.capitalize()} aberto.")
                return CommandResult(True, f"{app_name} já está aberto, mas não consegui focar a janela.")
            if path.startswith(("http://", "https://")):
                webbrowser.open(path)
                return CommandResult(True, f"Abrindo {app_name}.")
            subprocess.Popen(path)
            return CommandResult(True, f"Abrindo {app_name}.")
        except Exception as e:
            return CommandResult(False, f"Não consegui abrir {app_name}: {e}")

    def close_app(self, app_name: str) -> CommandResult:
        process_name = self.kill_map.get(app_name)
        if not process_name:
            return CommandResult(False, "", False)
        if app_name in {"chrome", "google chrome"}:
            script = r"""
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('Google Chrome')) { exit 1 }
Start-Sleep -Milliseconds 200
$wshell.SendKeys('%{F4}')
"""
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", script],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode != 0:
                    return CommandResult(True, "Chrome não está ativo.")
                return CommandResult(True, "Fechando chrome.")
            except Exception as e:
                return CommandResult(False, f"Não consegui fechar {app_name}: {e}")
        try:
            subprocess.run(f'taskkill /IM "{process_name}" /F', shell=True, capture_output=True, text=True)
            return CommandResult(True, f"Fechando {app_name}.")
        except Exception as e:
            return CommandResult(False, f"Não consegui fechar {app_name}: {e}")

    def _chrome_sendkeys(self, keys: str) -> bool:
        script = f"""
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('Google Chrome')) {{ exit 1 }}
Start-Sleep -Milliseconds 200
$wshell.SendKeys('{keys}')
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def close_current_chrome_tab(self) -> CommandResult:
        script = r"""
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('Google Chrome')) { exit 1 }
Start-Sleep -Milliseconds 200
$wshell.SendKeys('^w')
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return CommandResult(True, "Não encontrei uma janela do Google Chrome ativa.")
            return CommandResult(True, "", False)
        except Exception as e:
            return CommandResult(False, f"Não consegui fechar a aba: {e}")

    def open_new_chrome_tab(self) -> CommandResult:
        script = r"""
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('Google Chrome')) { exit 1 }
Start-Sleep -Milliseconds 200
$wshell.SendKeys('^t')
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return CommandResult(True, "Não encontrei uma janela do Google Chrome ativa.")
            return CommandResult(True, "", False)
        except Exception as e:
            return CommandResult(False, f"Não consegui abrir nova aba: {e}")

    def focus_chrome_tab(self, action: str) -> CommandResult:
        shortcuts = {
            "next": "^{TAB}", "previous": "^+{TAB}",
            "first": "^1", "second": "^2", "third": "^3", "fourth": "^4",
            "fifth": "^5", "sixth": "^6", "seventh": "^7", "eighth": "^8", "last": "^9",
        }
        keys = shortcuts.get(action)
        if not keys:
            return CommandResult(False, "", False)
        if not self._chrome_sendkeys(keys):
            return CommandResult(True, "Não encontrei uma janela do Google Chrome ativa.")
        return CommandResult(True, "", False)

    def extract_tab_focus_action(self, command: str) -> Optional[str]:
        match = re.search(
            r"(?:ir\s+para\s+|vá\s+para\s+|va\s+para\s+)?(?:a\s+|o\s+)?(?:aba|guia)\s+(\d+|um|uma|dois|duas|tres|três|quatro|cinco|seis|sete|oito|ultima|última)",
            command,
        )
        if not match:
            return None
        number_map = {
            "1": "first", "um": "first", "uma": "first",
            "2": "second", "dois": "second", "duas": "second",
            "3": "third", "tres": "third", "três": "third",
            "4": "fourth", "quatro": "fourth",
            "5": "fifth", "cinco": "fifth",
            "6": "sixth", "seis": "sixth",
            "7": "seventh", "sete": "seventh",
            "8": "eighth", "oito": "eighth",
            "ultima": "last", "última": "last",
        }
        return number_map.get(match.group(1).lower())

    def youtube_control(self, action: str) -> CommandResult:
        if action != "toggle_playback":
            return CommandResult(False, "", False)
        script = r"""
$wshell = New-Object -ComObject WScript.Shell
if (-not $wshell.AppActivate('Google Chrome')) { exit 1 }
Start-Sleep -Milliseconds 200
$wshell.SendKeys('k')
"""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return CommandResult(True, "Não encontrei uma janela do Chrome ativa.")
            return CommandResult(True, "Alternando reprodução do YouTube.")
        except Exception as e:
            return CommandResult(False, f"Não consegui controlar o YouTube: {e}")

    def open_website(self, site: str) -> CommandResult:
        site = site.strip()
        if not site:
            return CommandResult(False, "", False)
        url = site if site.startswith("http") else f"https://{site}"
        try:
            webbrowser.open(url)
            return CommandResult(True, f"Abrindo {site}.")
        except Exception as e:
            return CommandResult(False, f"Não consegui abrir o site: {e}")

    def say_time(self) -> CommandResult:
        now = time.strftime("%H:%M")
        return CommandResult(True, f"Agora são {now}.")

    def should_open_study_mode(self, command: str) -> bool:
        triggers = [
            "prepare meus estudos", "abra meus estudos", "abrir meus estudos",
            "vamos aos estudos", "modo estudo", "modo estudos", "iniciar estudos",
            "iniciar meus estudos", "começar estudos", "começar meus estudos",
            "quero estudar", "vou estudar", "hora de estudar", "abrir ava",
            "abra o ava", "abrir notebooklm", "abra o notebooklm",
            "abrir coursera", "abra o coursera", "preparar estudo", "preparar meus estudos",
        ]
        return any(trigger in command for trigger in triggers)

    def open_study_mode(self) -> CommandResult:
        chrome_path = os.path.expandvars(self.apps["chrome"])
        if self.study_opened:
            try:
                subprocess.Popen([chrome_path, "https://ava.ufms.br/my/"])
            except Exception:
                webbrowser.open("https://ava.ufms.br/my/")
            return CommandResult(True, "As páginas já estão abertas mestre. Abrindo o AVA.")
        urls = ["https://notebooklm.google.com/", "https://ava.ufms.br/my/", "https://www.coursera.org/"]
        try:
            subprocess.Popen([chrome_path, *urls])
        except Exception:
            for url in urls:
                webbrowser.open_new_tab(url)
        self.study_opened = True
        return CommandResult(True, "Preparando seus estudos mestre.")

    def _init_spotify(self):
        if not spotipy or not SpotifyOAuth:
            return None
        client_id = os.getenv("SPOTIPY_CLIENT_ID")
        client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
        redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
        if not client_id or not client_secret:
            return None
        try:
            auth = SpotifyOAuth(
                client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri,
                scope="user-read-playback-state user-modify-playback-state user-read-currently-playing",
                open_browser=True,
            )
            return spotipy.Spotify(auth_manager=auth)
        except Exception:
            return None

    def play_spotify(self, query: str) -> CommandResult:
        if not self.spotify or not query.strip():
            return CommandResult(False, "", False)
        try:
            normalized_query = re.sub(r"\s+", " ", query.strip().lower())
            results = self.spotify.search(q=query, type="track", limit=5)
            tracks = results.get("tracks", {}).get("items", [])
            if not tracks:
                return CommandResult(True, f"Não encontrei nenhuma música para {query} no Spotify.")
            best_track, best_score = None, -1.0
            for track in tracks:
                name = track.get("name", "")
                artist = track.get("artists", [{}])[0].get("name", "")
                normalized_name = re.sub(r"\s+", " ", name.strip().lower())
                normalized_artist = re.sub(r"\s+", " ", artist.strip().lower())
                combined = f"{normalized_name} {normalized_artist}".strip()
                score = max(
                    SequenceMatcher(None, normalized_query, normalized_name).ratio(),
                    SequenceMatcher(None, normalized_query, combined).ratio(),
                ) + (0.15 if normalized_query in normalized_name else 0.0)
                if score > best_score:
                    best_score = score
                    best_track = track
            track = best_track or tracks[0]
            uri = track["uri"]
            devices = self.spotify.devices().get("devices", [])
            if not devices:
                return CommandResult(True, "Nenhum dispositivo Spotify encontrado. Abra o app primeiro.")
            device_id = devices[0]["id"]
            self.spotify.transfer_playback(device_id=device_id, force_play=False)
            time.sleep(0.5)
            self.spotify.start_playback(device_id=device_id, uris=[uri])
            return CommandResult(True, "", False)
        except Exception as e:
            print(f"SPOTIFY ERRO: {e}")
            return CommandResult(True, "Não consegui tocar no Spotify. Verifique se o app está aberto.")

    def spotify_control(self, action: str) -> CommandResult:
        if not self.spotify:
            return CommandResult(False, "", False)
        try:
            if action == "pausar":
                self.spotify.pause_playback()
                return CommandResult(True, "Spotify pausado.")
            if action == "continuar":
                self.spotify.start_playback()
                return CommandResult(True, "Continuando no Spotify.")
            if action == "proxima":
                self.spotify.next_track()
                return CommandResult(True, "Próxima música.")
            if action == "anterior":
                self.spotify.previous_track()
                return CommandResult(True, "Música anterior.")
        except Exception:
            return CommandResult(True, "Não consegui controlar o Spotify. Verifique se o app está aberto.")
        return CommandResult(False, "", False)

    def play_youtube(self, query: str) -> CommandResult:
        if not query.strip():
            return CommandResult(False, "", False)
        url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}"
        webbrowser.open(url)
        return CommandResult(True, f"Procurando {query} no YouTube.")

    def extract_youtube_query(self, command: str) -> Optional[str]:
        patterns = [
            r"(?:toque|toca|tocar|tocando)\s+(.+?)\s+no\s+youtube$",
            r"(?:pesquise|procure|buscar|busque)\s+no\s+youtube\s+(?:por\s+|sobre\s+)?(.+)$",
            r"(?:pesquise|procure|buscar|busque)\s+(.+?)\s+no\s+youtube$",
            r"(?:abra|abrir)\s+(?:o\s+)?youtube\s+(?:e\s+)?(?:pesquise|procure|busque|buscar)\s+(?:por\s+|sobre\s+)?(.+)$",
            r"(?:abrir|abra)\s+(.+?)\s+no\s+youtube$",
            r"(?:quero\s+ouvir|quero\s+ver|me\s+mostre|mostre|coloque|coloca)\s+(.+?)\s+no\s+youtube$",
        ]
        for pattern in patterns:
            match = re.search(pattern, command)
            if match:
                q = match.group(1).strip(" .,!?:;")
                if q:
                    return q
        return None

    def ask_chatbot(self, user_text: str) -> CommandResult:
        if not self.openrouter_api_key or not _requests or not user_text.strip():
            return CommandResult(False, "", False)
        try:
            response = _requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.openrouter_api_key}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "Você é o Jarvis, um assistente pessoal curto, direto e natural. Responda em português do Brasil, em no máximo 1 frase, extremamente direto."},
                        {"role": "user", "content": user_text},
                    ],
                },
                timeout=15,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"].strip()
            return CommandResult(True, text) if text else CommandResult(False, "", False)
        except Exception:
            return CommandResult(False, "", False)

    def _listen_loop(self) -> None:
        if not self.recognizer or not sr:
            return
        with sr.Microphone() as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.8)
            self.recognizer.energy_threshold = max(160, self.recognizer.energy_threshold * 1.2)
            while self.running:
                if self.is_speaking:
                    time.sleep(0.1)
                    continue
                try:
                    audio = self.recognizer.listen(source, phrase_time_limit=6)
                except KeyboardInterrupt:
                    self._command_queue.put("sair")
                    break
                except Exception:
                    continue

                command = self._transcribe(audio)
                if self._should_ignore_voice_command(command):
                    continue
                if self.standby:
                    if "modo ativo" in command:
                        self.standby = False
                        self.speak("Modo ativo. Estou ouvindo senhor.")
                    continue
                self._command_queue.put(command)

    def get_command(self) -> str:
        while self.running:
            try:
                return self._command_queue.get(timeout=0.2)
            except queue.Empty:
                continue
        return "sair"

    def process_command(self, command: str) -> CommandResult:
        if not command:
            return CommandResult(False, "", False)
        command = command.strip(".,!?;: ").lower()

        if command in {"sair", "encerrar", "fechar jarvis", "parar"}:
            self.running = False
            return CommandResult(True, "Encerrando.")

        if any(p in command for p in ["modo standby", "modo stand by", "modo standy by", "modo standy", "standby", "stand by", "standy by", "standy", "modo stand-by", "stand-by"]):
            self.standby = True
            return CommandResult(True, "Entrando em modo standby. Diga modo ativo para voltar.")

        if self.standby:
            return CommandResult(False, "", False)

        if self.should_open_study_mode(command):
            return self.open_study_mode()

        if "que horas são" in command or command == "horas":
            return self.say_time()

        spotify_match = re.search(r"(?:toque|toca|tocar|tocando) (.+?)(?:\s+no\s+spotify|$)", command)
        if spotify_match:
            return self.play_spotify(spotify_match.group(1).strip())

        youtube_query = self.extract_youtube_query(command)
        if youtube_query:
            return self.play_youtube(youtube_query)

        if any(p in command for p in ["pausa o spotify", "pausar o spotify", "pausa spotify", "pause o spotify", "pause spotify", "pausar spotify", "pause a", "pause", "pare o spotify", "pare spotify"]):
            return self.spotify_control("pausar")
        if any(p in command for p in ["continua o spotify", "continuar o spotify", "continua spotify", "continue o spotify", "continue spotify"]):
            return self.spotify_control("continuar")
        if any(p in command for p in ["próxima música", "proxima música", "próxima musica", "proxima musica"]):
            return self.spotify_control("proxima")
        if any(p in command for p in ["música anterior", "musica anterior", "voltar música", "voltar musica"]):
            return self.spotify_control("anterior")

        if any(p in command for p in ["pause o youtube", "pausar o youtube", "pausa o youtube", "pare o youtube", "pausa youtube", "pause youtube", "pare youtube", "continue o youtube", "continuar o youtube", "continua o youtube", "retome o youtube", "retomar o youtube", "continue youtube", "continua youtube", "retome youtube"]):
            return self.youtube_control("toggle_playback")

        if any(p in command for p in ["próxima guia", "proxima guia", "próxima aba", "proxima aba"]):
            return self.focus_chrome_tab("next")
        if any(p in command for p in ["guia anterior", "aba anterior", "voltar guia", "voltar aba"]):
            return self.focus_chrome_tab("previous")

        tab_focus_action = self.extract_tab_focus_action(command)
        if tab_focus_action:
            return self.focus_chrome_tab(tab_focus_action)

        if any(p in command for p in ["primeira guia", "primeira aba"]): return self.focus_chrome_tab("first")
        if any(p in command for p in ["segunda guia", "segunda aba"]): return self.focus_chrome_tab("second")
        if any(p in command for p in ["terceira guia", "terceira aba"]): return self.focus_chrome_tab("third")
        if any(p in command for p in ["quarta guia", "quarta aba"]): return self.focus_chrome_tab("fourth")
        if any(p in command for p in ["quinta guia", "quinta aba"]): return self.focus_chrome_tab("fifth")
        if any(p in command for p in ["sexta guia", "sexta aba"]): return self.focus_chrome_tab("sixth")
        if any(p in command for p in ["sétima guia", "setima guia", "sétima aba", "setima aba"]): return self.focus_chrome_tab("seventh")
        if any(p in command for p in ["oitava guia", "oitava aba"]): return self.focus_chrome_tab("eighth")
        if any(p in command for p in ["última guia", "ultima guia", "última aba", "ultima aba"]): return self.focus_chrome_tab("last")

        if any(p in command for p in ["feche a aba atual do chrome", "fechar a aba atual do chrome", "feche a aba atual", "fechar a aba atual", "fechar guia", "feche guia", "feche a guia atual", "fechar a guia atual"]):
            return self.close_current_chrome_tab()

        if any(p in command for p in ["abrir nova guia", "abrir nova aba", "abra uma nova guia", "abra uma nova aba", "abra uma guia", "abra uma aba"]):
            return self.open_new_chrome_tab()

        open_match = re.search(r"(?:abra|abre|abrir)\s+(?:(?:o|a)\s+)?(?:(?:app|aplicativo)\s+)?(.*)", command)
        if open_match:
            target = open_match.group(1).strip()
            if target.startswith("site "):
                return self.open_website(target.replace("site ", "", 1).strip())
            if ".com" in target or ".br" in target:
                return self.open_website(target)
            return self.open_app(target)

        close_match = re.search(r"(?:feche|fecha|fechar)\s+(?:(?:o|a)\s+)?(?:(?:app|aplicativo)\s+)?(.*)", command)
        if close_match:
            return self.close_app(close_match.group(1).strip())

        if command.startswith("pesquise "):
            term = command.replace("pesquise ", "", 1).strip()
            if term:
                webbrowser.open(f"https://www.google.com/search?q={term.replace(' ', '+')}")
                return CommandResult(True, f"Pesquisando {term}.")

        if command.startswith("abrir site "):
            return self.open_website(command.replace("abrir site ", "", 1).strip())

        chatbot_result = self.ask_chatbot(command)
        if chatbot_result.handled:
            return chatbot_result

        return CommandResult(False, "", False)

    def run(self) -> None:
        listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        listener_thread.start()
        print("Ouvindo...")
        self.speak(self.startup_greeting())
        self._drain_command_queue()
        while self.running:
            command = self.get_command()
            result = self.process_command(command)
            if result.should_speak and result.message:
                self._drain_command_queue()
                self.speak(result.message)
                self._drain_command_queue()
        self.window.stop()


if __name__ == "__main__":
    started_from_startup = "--startup" in sys.argv or os.getenv("JARVIS_STARTUP") == "1"
    jarvis = Jarvis(started_from_startup=started_from_startup)
    jarvis.run()
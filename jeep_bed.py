#!/usr/bin/env python3
"""
Jeep Bed Interactive Controller
--------------------------------
Raspberry Pi 4B script: 5 buttons (Engine, Horn, Music, Siren, Headlights),
sound effects out the 3.5mm jack, a Flask web control panel, and native
Apple HomeKit support via HAP-python.

INSTALL (on the Pi):
    sudo apt-get update
    sudo apt-get install -y git python3-pip python3-pygame
    sudo pip3 install --break-system-packages flask gpiozero HAP-python

CLONE GIT REPO
    cd ~
    git clone https://github.com/justinmiller24/iot-bed.git
    cd iot-bed

FORCE AUDIO OUT THE 3.5MM JACK:
    sudo raspi-config  ->  System Options -> Audio -> Headphones

AUDIO BUFFER / ALSA "underrun occurred" LOG SPAM:
    Confirmed cosmetic in this build -- audio plays back cleanly with no
    audible glitches even when these messages appear, so buffer size
    tuning wasn't the fix (tested 4096 and 8192 with no difference).
    ALSA prints these directly to stderr from the C library itself,
    bypassing Python entirely, which is why they show up as raw noise in
    `journalctl -u jeepbed.service`. Fixed by installing a silent ALSA
    error handler via ctypes before pygame.mixer.init() -- see SETUP
    section below. If you ever DO hear actual audio glitches, that's a
    different problem (real underrun) and worth revisiting buffer size
    or switching to a USB audio dongle instead of the onboard jack.

WIRING SUMMARY (paired for adjacent physical header pins, per case layout)
    - Engine:     button GPIO 22, LED GPIO 23 (green)
    - Horn:       button GPIO 17, LED GPIO 18 (red)
    - Music:      button GPIO 20, LED GPIO 12 (white)
    - Siren:      button GPIO 27, LED GPIO 4  (blue)
    - Headlight Left:  button GPIO 24, LED GPIO 25
    - Headlight Right: button GPIO 5,  LED GPIO 6
      (Either headlight button alone turns BOTH headlight LEDs on/off
      together. Pressing both buttons at the same time instead fires
      on_headlights_combo() -- currently a placeholder triple-flash;
      edit that function's body once you've decided what it should do.)
    - Buttons: one leg to GPIO, other to GND. gpiozero uses the internal
      pull-up, no external resistor needed.
    - Every LED: GPIO -> MOSFET/transistor driver -> LED -> supply (or
      direct-to-GPIO with active_high=False for LEDs with a built-in
      current-limiting resistor -- confirm your specific button's specs
      before wiring directly).

REMOTE TRIGGERING (Flask):
    Visiting http://<pi-ip>/ in a browser loads index.html -- a one-page
    control panel with all 5 buttons. index.html MUST live in the same
    folder as this script. Find the Pi's IP with `hostname -I`.
    Buttons can also be triggered directly, e.g. with curl:
        curl http://<pi-ip>/trigger/horn
    Valid names: engine, horn, music, siren, headlights
    GET http://<pi-ip>/api/triggers lists all available triggers.
    No authentication -- keep this on a trusted home network only, do
    not port-forward this to the public internet.

SYSTEM CONTROLS:
    The web panel also has restart/reboot/shutdown buttons, backed by:
        GET http://<pi-ip>/system/restart-service
        GET http://<pi-ip>/system/reboot
        GET http://<pi-ip>/system/shutdown
    These run with no auth beyond being on your home network -- same
    caution as above applies, more so given what they can do.
    All three are also exposed as HomeKit switches ("Restart Jeep
    Service", "Reboot Jeep Bed", "Shutdown Jeep Bed") -- useful for
    building a Home app automation that shuts the Pi down cleanly,
    then (with a short delay after) turns off the smart plug powering
    it, avoiding the SD card corruption risk of a hard power cut.

VOLUME CONTROL:
    The web panel has a slider controlling system-wide output volume via
    ALSA (amixer) -- affects all audio the Pi plays, not just this app.
        GET http://<pi-ip>/volume          -> current level (0-100)
        GET http://<pi-ip>/volume/<level>  -> sets level (0-100)
    Uses the "PCM" ALSA control by default; if it has no effect, run
    `amixer -c 0 scontrols` on the Pi and update ALSA_MIXER_CONTROL.

APPLE HOMEKIT (HAP-python):
    This script also runs as its own HomeKit bridge -- no separate
    Homebridge process needed. On first run, HAP-python prints a pairing
    code to the console. In the Apple Home app: Add Accessory -> More
    Options -> enter that code manually. Pairing state is saved to
    homekit.state (in the same folder as this script) so re-pairing
    isn't needed after a reboot -- don't delete that file.

STARTUP CUE:
    When the script finishes loading and buttons go live, both headlights
    and the horn LED flash 3 times while sounds/horn-long.mp3 plays --
    make sure that file exists in sounds/ or startup will throw an error.

DYNAMIC SOUND LOADING:
    Engine, Horn, Music, and Siren each pull their sounds from a folder
    instead of a hardcoded filename -- drop any .wav/.mp3/.ogg file into
    the right folder and restart the service to pick it up, no code
    changes needed:
        sounds/engine/   -> Engine
        sounds/horn/     -> Horn
        sounds/noises/   -> Music
        sounds/siren/    -> Siren
    Each button cycles through its folder's files in alphabetical order,
    one per press (Siren cycles which sound loops each time it's turned
    on). Each folder needs at least one audio file or the script will
    raise an error on startup. Engine, Horn, and Noises share a single
    "currently playing" slot -- pressing any of the three stops whatever
    was playing (from among those three) before starting the new clip,
    preventing overlapping garbled audio from rapid button-mashing.
    Siren is unaffected by this (independent loop with its own on/off).

Run manually to test:
    sudo python3 jeep_bed.py
"""

import os
import glob
import subprocess
import time
import threading
import ctypes
import re

from gpiozero import Button, LED
import pygame
from flask import Flask, jsonify, send_from_directory
from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_SWITCH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# GREEN
ENGINE_BUTTON_PIN = 22
ENGINE_LED_PIN = 23
ENGINE_SOUND_DIR = "sounds/engine"
ENGINE_HOLD_SECONDS = 2.2

# RED
HORN_BUTTON_PIN = 17
HORN_LED_PIN = 18
HORN_SOUND_DIR = "sounds/horn"
HORN_HOLD_SECONDS = 1.2

# WHITE
MUSIC_BUTTON_PIN = 20
MUSIC_LED_PIN = 12
MUSIC_SOUND_DIR = "sounds/noises"
MUSIC_HOLD_SECONDS = 1.0

# BLUE
SIREN_BUTTON_PIN = 27
SIREN_LED_PIN = 4
SIREN_SOUND_DIR = "sounds/siren"

# HEADLIGHTS -- independently controlled left/right
HEADLIGHT_LEFT_BUTTON_PIN = 24
HEADLIGHT_LEFT_LED_PIN = 25
HEADLIGHT_RIGHT_BUTTON_PIN = 5
HEADLIGHT_RIGHT_LED_PIN = 6

# STARTUP -- played/flashed once when the script finishes loading
STARTUP_SOUND = "sounds/horn-long.mp3"


# ---------------------------------------------------------------------------
# SOUND LOADING -- scans a directory so new files just need to be dropped
# in, no code changes needed to pick them up (just a restart).
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = ("*.wav", "*.mp3", "*.ogg")


def find_audio_files(directory):
    """Finds every .wav/.mp3/.ogg file in `directory` (relative to this
    script), sorted by filename for a stable, predictable cycle order."""
    full_dir = os.path.join(BASE_DIR, directory)
    filepaths = []
    for pattern in AUDIO_EXTENSIONS:
        filepaths.extend(glob.glob(os.path.join(full_dir, pattern)))
    filepaths.sort()
    if not filepaths:
        raise RuntimeError(
            f"No audio files found in {full_dir} -- add at least one "
            f".wav/.mp3/.ogg file there before starting the script."
        )
    print(f"[sounds] loaded {len(filepaths)} file(s) from {directory}")
    return filepaths


class SoundBank:
    """Wraps a directory of sounds and cycles through them in order --
    each call to play_next()/play_next_loop() advances to the next file,
    wrapping back to the start once the list is exhausted. Returns both
    the Sound object and its filename so callers can log what's playing."""

    def __init__(self, directory):
        self.filepaths = find_audio_files(directory)
        self.sounds = [pygame.mixer.Sound(f) for f in self.filepaths]
        self.index = 0

    def _advance(self):
        sound = self.sounds[self.index]
        filename = os.path.basename(self.filepaths[self.index])
        self.index = (self.index + 1) % len(self.sounds)
        return sound, filename

    def play_next(self):
        sound, filename = self._advance()
        sound.play()
        return sound, filename

    def play_next_loop(self):
        sound, filename = self._advance()
        sound.play(loops=-1)
        return sound, filename


# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------

# Silences ALSA's own low-level messages (e.g. "underrun occurred"), which
# print straight to stderr from the C library itself -- confirmed cosmetic
# in this build (audio plays back cleanly regardless), so this just stops
# them from spamming `journalctl -u jeepbed.service`.
try:
    _ALSA_ERROR_HANDLER = ctypes.CFUNCTYPE(
        None, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p
    )

    def _silent_alsa_error(filename, line, function, err, fmt):
        pass

    _c_alsa_error_handler = _ALSA_ERROR_HANDLER(_silent_alsa_error)
    _asound = ctypes.cdll.LoadLibrary("libasound.so.2")
    _asound.snd_lib_error_set_handler(_c_alsa_error_handler)
except OSError:
    # libasound.so.2 not found on this system -- harmless, just means the
    # underrun messages will still print, nothing else is affected.
    pass

pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=4096)
pygame.mixer.init()

engine_sounds = SoundBank(ENGINE_SOUND_DIR)
horn_sounds = SoundBank(HORN_SOUND_DIR)
music_sounds = SoundBank(MUSIC_SOUND_DIR)
siren_sounds = SoundBank(SIREN_SOUND_DIR)
_current_siren_sound = None
startup_sound = pygame.mixer.Sound(STARTUP_SOUND)

# Engine, Horn, and Noises share one "currently playing" slot -- pressing
# any of the three stops whatever's playing (from among those three)
# before starting the new one, so rapid button-mashing doesn't overlap
# multiple clips into a garbled mess. Siren has its own independent
# tracker above since it's a persistent loop with real on/off state.
_current_control_sound = None


def _play_control_sound(label, sound_bank):
    global _current_control_sound
    if _current_control_sound is not None:
        _current_control_sound.stop()
    sound, filename = sound_bank.play_next()
    print(f"[{label}] playing {filename}")
    _current_control_sound = sound
    return sound

engine_button = Button(ENGINE_BUTTON_PIN, bounce_time=0.05)
horn_button = Button(HORN_BUTTON_PIN, bounce_time=0.05)
music_button = Button(MUSIC_BUTTON_PIN, bounce_time=0.05)
siren_button = Button(SIREN_BUTTON_PIN, bounce_time=0.05)
headlight_left_button = Button(HEADLIGHT_LEFT_BUTTON_PIN, bounce_time=0.05)
headlight_right_button = Button(HEADLIGHT_RIGHT_BUTTON_PIN, bounce_time=0.05)

engine_led = LED(ENGINE_LED_PIN, active_high=False)
horn_led = LED(HORN_LED_PIN, active_high=False)
music_led = LED(MUSIC_LED_PIN, active_high=False)
siren_led = LED(SIREN_LED_PIN, active_high=False)
headlight_left_led = LED(HEADLIGHT_LEFT_LED_PIN, active_high=False)
headlight_right_led = LED(HEADLIGHT_RIGHT_LED_PIN, active_high=False)

siren_active = threading.Event()

# HomeKit accessory objects, assigned once the bridge is built further down.
# Referenced (not called) by the functions below, so it's fine that they're
# still None at function-definition time.
engine_accessory = None
horn_accessory = None
music_accessory = None
siren_accessory = None
headlight_accessory = None


# ---------------------------------------------------------------------------
# 1. ENGINE (green) -- momentary: rev sound
# ---------------------------------------------------------------------------

def on_engine_press():
    engine_led.on()
    _play_control_sound("engine", engine_sounds)


def on_engine_release():
    engine_led.off()

engine_button.when_pressed = on_engine_press
engine_button.when_released = on_engine_release


# ---------------------------------------------------------------------------
# 2. HORN (red) -- momentary: honk + quick headlight flash
# ---------------------------------------------------------------------------

def on_horn_press():
    horn_led.on()
    _play_control_sound("horn", horn_sounds)
    was_left_off = not headlight_left_led.is_lit
    was_right_off = not headlight_right_led.is_lit
    if was_left_off:
        headlight_left_led.on()
    if was_right_off:
        headlight_right_led.on()
    if was_left_off or was_right_off:
        time.sleep(0.15)
        if was_left_off:
            headlight_left_led.off()
        if was_right_off:
            headlight_right_led.off()

def on_horn_release():
    horn_led.off()

horn_button.when_pressed = on_horn_press
horn_button.when_released = on_horn_release


# ---------------------------------------------------------------------------
# 3. MUSIC (white) -- momentary: cycles a new clip each press
# ---------------------------------------------------------------------------

def on_music_press():
    music_led.on()
    _play_control_sound("music", music_sounds)

def on_music_release():
    music_led.off()

music_button.when_pressed = on_music_press
music_button.when_released = on_music_release


# ---------------------------------------------------------------------------
# 4. SIREN (blue) -- toggle: sound loops until pressed again
# ---------------------------------------------------------------------------

def set_siren(state):
    global _current_siren_sound
    if state:
        siren_active.set()
        _current_siren_sound, filename = siren_sounds.play_next_loop()
        print(f"[siren] looping {filename}")
        siren_led.on()
    else:
        siren_active.clear()
        if _current_siren_sound is not None:
            _current_siren_sound.stop()
        siren_led.off()
    if siren_accessory is not None:
        siren_accessory.sync(state)

def on_siren_button_press():
    new_state = not siren_active.is_set()
    print(f"[siren] {'on' if new_state else 'off'}")
    set_siren(new_state)

siren_button.when_pressed = on_siren_button_press


# ---------------------------------------------------------------------------
# 5. HEADLIGHTS -- either button alone turns both on/off together;
#    pressing BOTH at the same time triggers a separate combo action.
# ---------------------------------------------------------------------------

HEADLIGHT_COMBO_WINDOW = 0.15  # seconds -- how close together both presses
                                # must land to count as "pressed together"

_headlight_combo_lock = threading.Lock()
_headlight_last_combo_time = 0.0


def set_headlight_left(state):
    if state:
        headlight_left_led.on()
    else:
        headlight_left_led.off()


def set_headlight_right(state):
    if state:
        headlight_right_led.on()
    else:
        headlight_right_led.off()


def set_headlights(state):
    """Turns both headlights on/off together -- the normal single-button
    behavior, and what remote (Flask/HomeKit) triggers use."""
    set_headlight_left(state)
    set_headlight_right(state)
    if headlight_accessory is not None:
        headlight_accessory.sync(state)


def on_headlights_single():
    new_state = not headlight_left_led.is_lit
    print(f"[headlights] {'on' if new_state else 'off'}")
    set_headlights(new_state)


def on_headlights_combo():
    """Placeholder for whatever the simultaneous-press action should be.
    Currently just a quick triple-flash of both headlights so you can
    confirm the combo is actually being detected -- replace this body
    with the real behavior once you've decided what it should do."""
    print("[headlights] COMBO pressed (both buttons together)")
    for _ in range(3):
        set_headlight_left(True)
        set_headlight_right(True)
        time.sleep(0.08)
        set_headlight_left(False)
        set_headlight_right(False)
        time.sleep(0.08)


def _handle_headlight_button(this_button, other_button):
    global _headlight_last_combo_time
    time.sleep(0.05)  # let the other button's edge register if it's also down
    if other_button.is_pressed:
        with _headlight_combo_lock:
            now = time.monotonic()
            if now - _headlight_last_combo_time > HEADLIGHT_COMBO_WINDOW:
                _headlight_last_combo_time = now
                on_headlights_combo()
            # else: the other button's handler already fired the combo
            # a moment ago -- don't double-trigger it.
    else:
        on_headlights_single()


def on_headlight_left_button_press():
    _handle_headlight_button(headlight_left_button, headlight_right_button)


def on_headlight_right_button_press():
    _handle_headlight_button(headlight_right_button, headlight_left_button)


headlight_left_button.when_pressed = on_headlight_left_button_press
headlight_right_button.when_pressed = on_headlight_right_button_press


# ---------------------------------------------------------------------------
# READY INDICATOR -- flash both headlights + horn LED, play startup sound
# ---------------------------------------------------------------------------
# Confirms boot is complete and physical buttons are ready for input --
# useful since full startup (OS boot + this script initializing) takes
# roughly 20 seconds with no other visible cue that it's done.

def flash_ready_indicator(times=3, on_seconds=0.15, off_seconds=0.15):
    startup_sound.play()
    for _ in range(times):
        headlight_left_led.on()
        headlight_right_led.on()
        horn_led.on()
        time.sleep(on_seconds)
        headlight_left_led.off()
        headlight_right_led.off()
        horn_led.off()
        time.sleep(off_seconds)


flash_ready_indicator()


# ---------------------------------------------------------------------------
# FLASK -- web control panel + HTTP trigger endpoints
# ---------------------------------------------------------------------------

app = Flask(__name__)

# name -> (press_fn, release_fn or None, hold_seconds or None)
# release_fn/hold_seconds auto-release momentary buttons since there's no
# physical "release" event when triggered remotely.
TRIGGERS = {
    "engine": (on_engine_press, on_engine_release, ENGINE_HOLD_SECONDS),
    "horn": (on_horn_press, on_horn_release, HORN_HOLD_SECONDS),
    "music": (on_music_press, on_music_release, MUSIC_HOLD_SECONDS),
    "siren": (on_siren_button_press, None, None),
    "headlights": (on_headlights_single, None, None),
}


def _auto_release(release_fn, hold_seconds):
    time.sleep(hold_seconds)
    release_fn()


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/triggers", methods=["GET"])
def api_triggers():
    return jsonify(available_triggers=list(TRIGGERS.keys()))


@app.route("/trigger/<name>", methods=["GET"])
def trigger(name):
    if name not in TRIGGERS:
        return jsonify(error=f"unknown trigger '{name}'", available=list(TRIGGERS.keys())), 404

    press_fn, release_fn, hold_seconds = TRIGGERS[name]
    press_fn()
    if release_fn is not None:
        threading.Thread(target=_auto_release, args=(release_fn, hold_seconds), daemon=True).start()

    return jsonify(status="triggered", action=name)


# ---------------------------------------------------------------------------
# SYSTEM CONTROLS -- restart the service, reboot, or shut down the Pi
# ---------------------------------------------------------------------------
# Runs as root already (see systemd User=root), so these commands work
# directly with no sudo needed. Each is delayed ~1s in a background thread
# so the HTTP response actually reaches the browser before the process
# handling it disappears.

SYSTEM_COMMANDS = {
    "restart-service": ["systemctl", "restart", "jeepbed.service"],
    "reboot": ["reboot"],
    "shutdown": ["shutdown", "-h", "now"],
}


def _delayed_command(cmd, delay=1.0):
    time.sleep(delay)
    subprocess.run(cmd, check=False)


@app.route("/system/<action>", methods=["GET"])
def system_control(action):
    if action not in SYSTEM_COMMANDS:
        return jsonify(error=f"unknown system action '{action}'", available=list(SYSTEM_COMMANDS.keys())), 404

    threading.Thread(target=_delayed_command, args=(SYSTEM_COMMANDS[action],), daemon=True).start()
    return jsonify(status="executing", action=action)


# ---------------------------------------------------------------------------
# VOLUME -- system-wide output level via ALSA (amixer), not per-sound-file.
# ---------------------------------------------------------------------------
# ALSA_MIXER_CONTROL is "PCM" for the Pi's standard onboard bcm2835 audio.
# If setting volume has no effect, run `amixer -c 0 scontrols` on the Pi
# to see this system's actual control names and update the constant below.

ALSA_MIXER_CONTROL = "PCM"


def get_current_volume():
    result = subprocess.run(
        ["amixer", "-c", "0", "sget", ALSA_MIXER_CONTROL],
        capture_output=True, text=True, check=False,
    )
    match = re.search(r"\[(\d+)%\]", result.stdout)
    return int(match.group(1)) if match else None


@app.route("/volume", methods=["GET"])
def api_get_volume():
    return jsonify(volume=get_current_volume())


@app.route("/volume/<int:level>", methods=["GET"])
def api_set_volume(level):
    level = max(0, min(100, level))
    subprocess.run(["amixer", "-c", "0", "sset", ALSA_MIXER_CONTROL, f"{level}%"], check=False)
    return jsonify(status="set", volume=level)


# ---------------------------------------------------------------------------
# HOMEKIT (HAP-python) -- native HomeKit bridge, no Homebridge needed
# ---------------------------------------------------------------------------

class MomentarySwitch(Accessory):
    """HomeKit switch that fires an action, then auto-resets to Off."""
    category = CATEGORY_SWITCH

    def __init__(self, driver, name, press_fn, release_fn, hold_seconds):
        super().__init__(driver, name)
        self.press_fn = press_fn
        self.release_fn = release_fn
        self.hold_seconds = hold_seconds
        serv = self.add_preload_service("Switch")
        self.char_on = serv.configure_char("On", setter_callback=self._handle_set)

    def _handle_set(self, value):
        if value:
            self.press_fn()
            threading.Thread(target=self._auto_release, daemon=True).start()

    def _auto_release(self):
        time.sleep(self.hold_seconds)
        self.release_fn()
        self.char_on.set_value(False)


class ToggleSwitch(Accessory):
    """HomeKit switch that reflects a real on/off state (siren, headlights)."""
    category = CATEGORY_SWITCH

    def __init__(self, driver, name, set_fn):
        super().__init__(driver, name)
        serv = self.add_preload_service("Switch")
        self.char_on = serv.configure_char("On", setter_callback=set_fn)

    def sync(self, state):
        """Call from physical-button handlers to reflect state into HomeKit."""
        self.char_on.set_value(bool(state))


class JeepBridge(Bridge):
    """Bridge subclass so shutdown also cleans up GPIO/audio, HAP-python
    style (avoids fighting HAP-python's own SIGINT/SIGTERM handling)."""

    def stop(self):
        print("Shutting down cleanly...")
        siren_active.clear()
        for led in (engine_led, horn_led, music_led, siren_led, headlight_left_led, headlight_right_led):
            led.off()
        pygame.mixer.quit()
        super().stop()


def make_system_press_fn(action):
    """Returns a press_fn that fires the given SYSTEM_COMMANDS entry."""
    def press():
        print(f"[system] {action} requested (via HomeKit)")
        threading.Thread(target=_delayed_command, args=(SYSTEM_COMMANDS[action],), daemon=True).start()
    return press


def system_noop_release():
    """System switches have no physical state to release -- just resets the
    HomeKit toggle back to Off visually."""
    pass


def build_homekit_bridge():
    global engine_accessory, horn_accessory, music_accessory
    global siren_accessory, headlight_accessory

    persist_file = os.path.join(BASE_DIR, "homekit.state")
    driver = AccessoryDriver(port=51826, persist_file=persist_file)

    bridge = JeepBridge(driver, "Jeep Bed")

    engine_accessory = MomentarySwitch(driver, "Engine", on_engine_press, on_engine_release, ENGINE_HOLD_SECONDS)
    horn_accessory = MomentarySwitch(driver, "Horn", on_horn_press, on_horn_release, HORN_HOLD_SECONDS)
    music_accessory = MomentarySwitch(driver, "Music", on_music_press, on_music_release, MUSIC_HOLD_SECONDS)
    siren_accessory = ToggleSwitch(driver, "Siren", lambda value: set_siren(bool(value)))
    headlight_accessory = ToggleSwitch(driver, "Headlights", lambda value: set_headlights(bool(value)))

    # System controls -- short hold_seconds so the Home app switch flips
    # back to Off quickly, well before reboot/shutdown actually happens
    # (the 1s delay baked into _delayed_command still applies underneath).
    restart_service_accessory = MomentarySwitch(
        driver, "Restart Jeep Service", make_system_press_fn("restart-service"), system_noop_release, 1.5)
    reboot_accessory = MomentarySwitch(
        driver, "Reboot Jeep Bed", make_system_press_fn("reboot"), system_noop_release, 1.5)
    shutdown_accessory = MomentarySwitch(
        driver, "Shutdown Jeep Bed", make_system_press_fn("shutdown"), system_noop_release, 1.5)

    for accessory in (
        engine_accessory, horn_accessory, music_accessory, siren_accessory, headlight_accessory,
        restart_service_accessory, reboot_accessory, shutdown_accessory,
    ):
        bridge.add_accessory(accessory)

    driver.add_accessory(accessory=bridge)
    return driver


# ---------------------------------------------------------------------------
# RUN -- Flask in a background thread, HAP-python driver as the main loop
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=80, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()

    print("Jeep bed controller running. Web panel on port 80. Starting HomeKit bridge...")
    homekit_driver = build_homekit_bridge()
    homekit_driver.start()  # blocks; handles SIGINT/SIGTERM and calls JeepBridge.stop()


# ---------------------------------------------------------------------------
# AUTOSTART ON BOOT (systemd) -- save as /etc/systemd/system/jeepbed.service
# ---------------------------------------------------------------------------
# [Unit]
# Description=Jeep Bed Interactive Controller
# After=sound.target
#
# [Service]
# ExecStart=/usr/bin/python3 /home/iot/iot-bed/jeep_bed.py
# WorkingDirectory=/home/iot/iot-bed
# Restart=always
# User=root
# KillSignal=SIGINT
#
# [Install]
# WantedBy=multi-user.target
#
# Then: sudo systemctl enable jeepbed.service && sudo systemctl start jeepbed.service

#!/usr/bin/env python3
"""
Jeep Bed Interactive Controller
--------------------------------
Raspberry Pi 4B script: 5 buttons (Engine, Horn, Music, Alarm, Headlights),
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

WIRING SUMMARY
    - Engine:     button GPIO 22, LED GPIO 16 (green)
    - Horn:       button GPIO 17, LED GPIO 4  (red)
    - Music:      button GPIO 23, LED GPIO 20 (white)
    - Alarm:      button GPIO 27, LED GPIO 12 (blue)
    - Headlights: button GPIO 24 (both physical buttons wired in parallel
                  to this same pin), LED GPIO 25
    - Buttons: one leg to GPIO, other to GND. gpiozero uses the internal
      pull-up, no external resistor needed.
    - Every LED: GPIO -> MOSFET/transistor driver -> LED -> supply (or
      direct-to-GPIO with active_high=False for LEDs with a built-in
      current-limiting resistor -- confirm your specific button's specs
      before wiring directly).

REMOTE TRIGGERING (Flask):
    Visiting http://<pi-ip>/ in a browser loads index.html -- a
    one-page control panel with all 5 buttons. index.html MUST live in
    the same folder as this script. Find the Pi's IP with `hostname -I`.
    Buttons can also be triggered directly, e.g. with curl:
        curl -X POST http://<pi-ip>/trigger/horn
    Valid names: engine, horn, music, alarm, headlights
    GET http://<pi-ip>/api/triggers lists all available triggers.
    No authentication -- keep this on a trusted home network only, do
    not port-forward this to the public internet.

APPLE HOMEKIT (HAP-python):
    This script also runs as its own HomeKit bridge -- no separate
    Homebridge process needed. On first run, HAP-python prints a pairing
    code to the console. In the Apple Home app: Add Accessory -> More
    Options -> enter that code manually. Pairing state is saved to
    homekit.state (in the same folder as this script) so re-pairing
    isn't needed after a reboot -- don't delete that file.

Run manually to test:
    sudo python3 jeep_bed.py
"""

import os
import time
import threading

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
ENGINE_LED_PIN = 16
ENGINE_SOUND = "sounds/engine.wav"
ENGINE_HOLD_SECONDS = 2.2

# RED
HORN_BUTTON_PIN = 17
HORN_LED_PIN = 4
HORN_SOUND = "sounds/horn.wav"
HORN_HOLD_SECONDS = 1.2

# WHITE
MUSIC_BUTTON_PIN = 23
MUSIC_LED_PIN = 20
MUSIC_SOUNDS = [
    "sounds/cb_radio.wav",
    "sounds/gravel.wav",
    "sounds/turbo_boost.wav",
]
MUSIC_HOLD_SECONDS = 1.0

# BLUE
ALARM_BUTTON_PIN = 27
ALARM_LED_PIN = 12
ALARM_SOUND = "sounds/alarm.wav"

# HEADLIGHTS
HEADLIGHT_BUTTON_PIN = 24   # both physical headlight buttons wired here
HEADLIGHT_LED_PIN = 25


# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------

pygame.mixer.init()

engine_sound = pygame.mixer.Sound(ENGINE_SOUND)
horn_sound = pygame.mixer.Sound(HORN_SOUND)
music_sounds = [pygame.mixer.Sound(f) for f in MUSIC_SOUNDS]
music_index = 0
alarm_sound = pygame.mixer.Sound(ALARM_SOUND)

engine_button = Button(ENGINE_BUTTON_PIN, bounce_time=0.05)
horn_button = Button(HORN_BUTTON_PIN, bounce_time=0.05)
music_button = Button(MUSIC_BUTTON_PIN, bounce_time=0.05)
alarm_button = Button(ALARM_BUTTON_PIN, bounce_time=0.05)
headlight_button = Button(HEADLIGHT_BUTTON_PIN, bounce_time=0.05)

engine_led = LED(ENGINE_LED_PIN, active_high=False)
horn_led = LED(HORN_LED_PIN, active_high=False)
music_led = LED(MUSIC_LED_PIN, active_high=False)
alarm_led = LED(ALARM_LED_PIN, active_high=False)
headlight_led = LED(HEADLIGHT_LED_PIN, active_high=False)

alarm_active = threading.Event()

# HomeKit accessory objects, assigned once the bridge is built further down.
# Referenced (not called) by the functions below, so it's fine that they're
# still None at function-definition time.
engine_accessory = None
horn_accessory = None
music_accessory = None
alarm_accessory = None
headlight_accessory = None


# ---------------------------------------------------------------------------
# 1. ENGINE (green) -- momentary: rev sound
# ---------------------------------------------------------------------------

def on_engine_press():
    print("[engine] start")
    engine_led.on()
    engine_sound.play()


def on_engine_release():
    engine_led.off()

engine_button.when_pressed = on_engine_press
engine_button.when_released = on_engine_release


# ---------------------------------------------------------------------------
# 2. HORN (red) -- momentary: honk + quick headlight flash
# ---------------------------------------------------------------------------

def on_horn_press():
    print("[horn] pressed")
    horn_led.on()
    horn_sound.play()
    if not headlight_led.is_lit:
        headlight_led.on()
        time.sleep(0.15)
        headlight_led.off()

def on_horn_release():
    horn_led.off()

horn_button.when_pressed = on_horn_press
horn_button.when_released = on_horn_release


# ---------------------------------------------------------------------------
# 3. MUSIC (white) -- momentary: cycles a new clip each press
# ---------------------------------------------------------------------------

def on_music_press():
    global music_index
    print(f"[music] playing clip {music_index}")
    music_led.on()
    music_sounds[music_index].play()
    music_index = (music_index + 1) % len(music_sounds)

def on_music_release():
    music_led.off()

music_button.when_pressed = on_music_press
music_button.when_released = on_music_release


# ---------------------------------------------------------------------------
# 4. ALARM (blue) -- toggle: sound loops until pressed again
# ---------------------------------------------------------------------------

def set_alarm(state):
    if state:
        alarm_active.set()
        alarm_sound.play(loops=-1)
        alarm_led.on()
    else:
        alarm_active.clear()
        alarm_sound.stop()
        alarm_led.off()
    if alarm_accessory is not None:
        alarm_accessory.sync(state)

def on_alarm_button_press():
    new_state = not alarm_active.is_set()
    print(f"[alarm] {'on' if new_state else 'off'}")
    set_alarm(new_state)

alarm_button.when_pressed = on_alarm_button_press


# ---------------------------------------------------------------------------
# 5. HEADLIGHTS -- toggle (shared by both physical buttons)
# ---------------------------------------------------------------------------

def set_headlights(state):
    if state:
        headlight_led.on()
    else:
        headlight_led.off()
    if headlight_accessory is not None:
        headlight_accessory.sync(state)

def on_headlight_button_press():
    new_state = not headlight_led.is_lit
    print(f"[headlights] {'on' if new_state else 'off'}")
    set_headlights(new_state)

headlight_button.when_pressed = on_headlight_button_press


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
    "alarm": (on_alarm_button_press, None, None),
    "headlights": (on_headlight_button_press, None, None),
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


@app.route("/trigger/<name>", methods=["POST"])
def trigger(name):
    if name not in TRIGGERS:
        return jsonify(error=f"unknown trigger '{name}'", available=list(TRIGGERS.keys())), 404

    press_fn, release_fn, hold_seconds = TRIGGERS[name]
    press_fn()
    if release_fn is not None:
        threading.Thread(target=_auto_release, args=(release_fn, hold_seconds), daemon=True).start()

    return jsonify(status="triggered", action=name)


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
    """HomeKit switch that reflects a real on/off state (alarm, headlights)."""
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
        alarm_active.clear()
        for led in (engine_led, horn_led, music_led, alarm_led, headlight_led):
            led.off()
        pygame.mixer.quit()
        super().stop()


def build_homekit_bridge():
    global engine_accessory, horn_accessory, music_accessory
    global alarm_accessory, headlight_accessory

    persist_file = os.path.join(BASE_DIR, "homekit.state")
    driver = AccessoryDriver(port=51826, persist_file=persist_file)

    bridge = JeepBridge(driver, "Jeep Bed")

    engine_accessory = MomentarySwitch(driver, "Engine", on_engine_press, on_engine_release, ENGINE_HOLD_SECONDS)
    horn_accessory = MomentarySwitch(driver, "Horn", on_horn_press, on_horn_release, HORN_HOLD_SECONDS)
    music_accessory = MomentarySwitch(driver, "Music", on_music_press, on_music_release, MUSIC_HOLD_SECONDS)
    alarm_accessory = ToggleSwitch(driver, "Alarm", lambda value: set_alarm(bool(value)))
    headlight_accessory = ToggleSwitch(driver, "Headlights", lambda value: set_headlights(bool(value)))

    for accessory in (engine_accessory, horn_accessory, music_accessory, alarm_accessory, headlight_accessory):
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

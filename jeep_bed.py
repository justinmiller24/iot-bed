#!/usr/bin/env python3
"""
Jeep Bed Interactive Controller
--------------------------------
Raspberry Pi 4B script: 4 main buttons + combined headlight toggle +
WS2812B side LED strip + sound effects out the 3.5mm jack.

INSTALL (on the Pi):
    sudo apt-get update
    sudo apt-get install -y git python3-pip python3-pygame
    sudo pip3 install --break-system-packages gpiozero adafruit-blinka \
        adafruit-circuitpython-neopixel rpi_ws281x

CLONE GIT REPO
    cd ~
    git clone https://github.com/justinmiller24/iot-bed.git
    cd iot-bed

FORCE AUDIO OUT THE 3.5MM JACK:
    sudo raspi-config  ->  System Options -> Audio -> Headphones
    (or: amixer cset numid=3 1)

WIRING SUMMARY:
    - Buttons: one leg to GPIO, other to GND. gpiozero uses the internal
      pull-up, no external resistor needed.
    - Headlight buttons: wire BOTH switches in parallel to the SAME GPIO
      pin (HEADLIGHT_BUTTON_PIN below) since they do the same thing --
      one Button object covers both physically.
    - Headlight LEDs: GPIO -> MOSFET/transistor driver -> LEDs -> supply.
      Don't drive LEDs directly off GPIO.
    - Each main button's built-in white LED: same rule -- GPIO -> MOSFET/
      transistor driver -> LED -> supply. The colored cap doesn't change
      the wiring, it's still a plain white LED underneath.
    - WS2812B strips (x2, daisy-chained): GPIO18 (hardware PWM) -> level
      shifter (74AHCT125 recommended) -> DIN of strip A -> DOUT of strip A
      -> DIN of strip B. Both strips get 5V/GND injected separately from
      their OWN supply (not relayed through the data chain) -- tie all
      grounds together. Software treats both as one continuous pixel
      array; see STRIP_LEFT_COUNT / STRIP_RIGHT_COUNT below.

Run manually to test:
    sudo python3 jeep_bed.py
(neopixel needs root/gpio-group access on most Pi OS setups)
"""

import time
import threading
import signal
import sys

from gpiozero import Button, LED
import pygame
import board
import neopixel

# ---------------------------------------------------------------------------
# CONFIG -- edit pins and sound filenames to match your build
# ---------------------------------------------------------------------------

HORN_BUTTON_PIN = 17
HORN_LED_PIN = 4            # red button's built-in white LED
HORN_SOUND = "sounds/horn.wav"

SIREN_BUTTON_PIN = 27
SIREN_LED_PIN = 12          # blue button's built-in white LED
SIREN_SOUND = "sounds/siren.wav"

ENGINE_BUTTON_PIN = 22
ENGINE_LED_PIN = 16         # green button's built-in white LED
ENGINE_SOUND = "sounds/engine_start.wav"

RADIO_BUTTON_PIN = 23
RADIO_LED_PIN = 20          # white button's built-in white LED
RADIO_SOUNDS = [
    "sounds/cb_radio.wav",
    "sounds/gravel.wav",
    "sounds/turbo_boost.wav",
]

HEADLIGHT_BUTTON_PIN = 24   # both physical headlight buttons wired here
HEADLIGHT_LED_PIN = 25      # drives MOSFET gate for the headlight LEDs

STRIP_PIXEL_PIN = board.D18
STRIP_LEFT_COUNT = 50          # pixels in the left-side strip
STRIP_RIGHT_COUNT = 50         # pixels in the right-side strip (daisy-chained after left)
STRIP_NUM_PIXELS = STRIP_LEFT_COUNT + STRIP_RIGHT_COUNT
STRIP_BRIGHTNESS = 1.0

RED = (255, 0, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------

pygame.mixer.init()

horn_sound = pygame.mixer.Sound(HORN_SOUND)
siren_sound = pygame.mixer.Sound(SIREN_SOUND)
engine_sound = pygame.mixer.Sound(ENGINE_SOUND)
radio_sounds = [pygame.mixer.Sound(f) for f in RADIO_SOUNDS]
radio_index = 0

horn_button = Button(HORN_BUTTON_PIN, bounce_time=0.05)
siren_button = Button(SIREN_BUTTON_PIN, bounce_time=0.05)
engine_button = Button(ENGINE_BUTTON_PIN, bounce_time=0.05)
radio_button = Button(RADIO_BUTTON_PIN, bounce_time=0.05)
headlight_button = Button(HEADLIGHT_BUTTON_PIN, bounce_time=0.05)

headlight_led = LED(HEADLIGHT_LED_PIN)
horn_led = LED(HORN_LED_PIN)
siren_led = LED(SIREN_LED_PIN)
engine_led = LED(ENGINE_LED_PIN)
radio_led = LED(RADIO_LED_PIN)

strip = neopixel.NeoPixel(
    STRIP_PIXEL_PIN,
    STRIP_NUM_PIXELS,
    brightness=STRIP_BRIGHTNESS,
    auto_write=False,
)

siren_active = threading.Event()

# ---------------------------------------------------------------------------
# BUTTON 1: HORN -- honk + quick headlight flash
# ---------------------------------------------------------------------------

def on_horn():
    print("[horn] pressed")
    horn_led.on()
    horn_sound.play()
    if not headlight_led.is_lit:
        headlight_led.on()
        time.sleep(0.15)
        headlight_led.off()


def on_horn_release():
    horn_led.off()


horn_button.when_pressed = on_horn
horn_button.when_released = on_horn_release

# ---------------------------------------------------------------------------
# BUTTON 2: SIREN / EMERGENCY LIGHTS -- toggle on/off, runs in background
# ---------------------------------------------------------------------------

def siren_loop():
    while True:
        if siren_active.is_set():
            strip.fill(RED)
            strip.show()
            time.sleep(0.25)
            if siren_active.is_set():
                strip.fill(BLUE)
                strip.show()
                time.sleep(0.25)
        else:
            time.sleep(0.05)


def on_siren_toggle():
    if siren_active.is_set():
        print("[siren] off")
        siren_active.clear()
        siren_sound.stop()
        siren_led.off()
        strip.fill(BLACK)
        strip.show()
    else:
        print("[siren] on")
        siren_active.set()
        siren_sound.play(loops=-1)
        siren_led.on()


siren_button.when_pressed = on_siren_toggle
threading.Thread(target=siren_loop, daemon=True).start()

# ---------------------------------------------------------------------------
# BUTTON 3: ENGINE START -- rev sound + one-shot strip chase
# ---------------------------------------------------------------------------

def on_engine_start():
    print("[engine] start")
    engine_led.on()
    engine_sound.play()
    if not siren_active.is_set():
        for i in range(STRIP_NUM_PIXELS):
            strip[i] = WHITE
            strip.show()
            time.sleep(0.02)
        time.sleep(0.3)
        strip.fill(BLACK)
        strip.show()


def on_engine_release():
    engine_led.off()


engine_button.when_pressed = on_engine_start
engine_button.when_released = on_engine_release

# ---------------------------------------------------------------------------
# BUTTON 4: RADIO / FX SHUFFLE -- cycles a new clip each press
# ---------------------------------------------------------------------------

def on_radio_press():
    global radio_index
    print(f"[radio] playing clip {radio_index}")
    radio_led.on()
    radio_sounds[radio_index].play()
    radio_index = (radio_index + 1) % len(radio_sounds)


def on_radio_release():
    radio_led.off()


radio_button.when_pressed = on_radio_press
radio_button.when_released = on_radio_release

# ---------------------------------------------------------------------------
# HEADLIGHTS -- simple on/off toggle (shared by both physical buttons)
# ---------------------------------------------------------------------------

def on_headlight_toggle():
    headlight_led.toggle()
    print(f"[headlights] {'on' if headlight_led.is_lit else 'off'}")


headlight_button.when_pressed = on_headlight_toggle

# ---------------------------------------------------------------------------
# CLEAN SHUTDOWN
# ---------------------------------------------------------------------------

def shutdown(*_args):
    print("Shutting down cleanly...")
    siren_active.clear()
    headlight_led.off()
    horn_led.off()
    siren_led.off()
    engine_led.off()
    radio_led.off()
    strip.fill(BLACK)
    strip.show()
    pygame.mixer.quit()
    sys.exit(0)


signal.signal(signal.SIGINT, shutdown)
signal.signal(signal.SIGTERM, shutdown)

print("Jeep bed controller running. Press Ctrl+C to stop.")
signal.pause()

# ---------------------------------------------------------------------------
# AUTOSTART ON BOOT (systemd) -- save as /etc/systemd/system/jeepbed.service
# ---------------------------------------------------------------------------
# [Unit]
# Description=Jeep Bed Interactive Controller
# After=sound.target
#
# [Service]
# ExecStart=/usr/bin/python3 /home/pi/jeep_bed.py
# WorkingDirectory=/home/pi
# Restart=always
# User=root
#
# [Install]
# WantedBy=multi-user.target
#
# Then: sudo systemctl enable jeepbed.service && sudo systemctl start jeepbed.service
# Note: neopixel typically needs root, hence User=root here (vs pi in v1).

# Python script to run IoT fire truck bed
# Created by Justin Miller on 12.21.2023

# Install packages (bash)
# sudo apt-get update
# sudo apt-get upgrade
# sudo apt-get install python3-pip
# sudo pip3 install --upgrade adafruit-blinka adafruit-circuitpython-neopixel adafruit-circuitpython-seesaw rpi_ws281x

# Import Libraries (python)
import time
import board
#import digitalio
#import pygame
import neopixel
#from adafruit_seesaw.seesaw import Seesaw
#from adafruit_seesaw.digitalio import DigitalIO
#from adafruit_seesaw.pwmout import PWMOut

# Initialize I2C
#import busio
#i2c = busio.I2C(board.SCL, board.SDA)
#qt = Seesaw(i2c, addr=0x3A)

# Initialize Audio
#pygame.init()

# Buttons
#BUTTON_COLORS = ("RED", "WHITE", "GREEN", "BLUE")
#BUTTON_PINS = (18, 19, 20, 2)
#btns = []
#for btnPin in BUTTON_PINS:
#    btn = DigitalIO(qt, btnPin)
#    btn.direction = digitalio.Direction.INPUT
#    btn.pull = digitalio.Pull.UP
#    btns.append(btn)

# Button LEDs
#LED_PINS = (12, 13, 0, 1)
#leds = []
#for ledPin in LED_PINS:
#    led = PWMOut(qt, ledPin)
#    leds.append(led)

# Configure LED setup
PIXEL_PIN = board.D18   # Pin that pixel LEDs are connected to
ORDER = neopixel.RGB    # Pixel color channel order
NUM_PIXELS = 1          # Number of LEDs
DELAY = 0.25            # Blink rate (seconds)
pixel = neopixel.NeoPixel(PIXEL_PIN, NUM_PIXELS, pixel_order=ORDER)

RED = (255,0,0)
YELLOW = (255,255,0)
GREEN = (0,255,0)
BLUE = (0,0,255)
WHITE = (255,255,255)
BLACK = (0,0,0)

# Loop forever and blink the color
whilte True:
    pixel[0] = RED
    time.sleep(DELAY)
    pixel[0] = BLACK
    time.sleep(DELAY)



# Main Loop
#try:
#    while True:
#        for ledNumber, button in enumerate(btns):
#            if not button.value:
#                print("Button Pressed:", BUTTON_COLORS[ledNumber])
#                leds[ledNumber].duty_cycle = 65535
#
#                # RED
#                if BUTTON_COLORS[ledNumber] == "RED":
#                    print('RED button action')
#                    fireTruck = pygame.mixer.Sound("/home/fpp/audio/fireTruck.wav")
#                    fireTruck.play
#                    pixels.fill((0, 255, 0))
#                    time.sleep(1)
#                    pixels.fill((0, 0, 0))
#
#                # GREEN
#                elif BUTTON_COLORS[ledNumber] == "GREEN":
#                    print('GREEN button action')
#
#                # WHITE
#                elif BUTTON_COLORS[ledNumber] == "WHITE":
#                    print('WHITE button action')
#
#                # BLUE
#                elif BUTTON_COLORS[ledNumber] == "BLUE":
#                    print('BLUE button action')
#
#            # Turn off LEDs when button is not pressed
#            else:
#                leds[ledNumber].duty_cycle = 0
#
#
#            qt.digital_write(LED_PINS[ledNumber], True)
#
#except KeyboardInterrupt:
#    pass
#
#print("Stopping...")
#exit()


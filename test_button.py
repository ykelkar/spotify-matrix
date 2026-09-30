from gpiozero import Button
from signal import pause

button = Button(19)

button.when_pressed = lambda: print("Button pressed!")
button.when_released = lambda: print("Button released!")

pause()

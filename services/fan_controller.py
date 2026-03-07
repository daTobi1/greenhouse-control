"""
Fan controller using RPi.GPIO PWM on a MOSFET-driven fan.

Fan orientation: EXHAUST – the fan pushes stale air OUT of the greenhouse.
Fresh outside air enters passively through vents/gaps. This means ventilating
only makes sense when outside conditions are actually better than inside:
  - Temperature:  outside must be cooler than inside
  - Humidity:     outside must be drier than inside

Proportional control algorithm:
  - Computes a 0..1 speed from the temperature/humidity error vs. target.
  - Only runs the fan when outside air would improve the inside condition.
  - Scales the result into the configured [fan_min, fan_max] range.
  - When raw_speed <= 0 the fan is switched off completely.
"""

import logging

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("RPi.GPIO not available – running in mock mode")


class FanController:
    def __init__(self):
        self._gpio_pin: int = 18
        self._frequency: int = 25_000   # 25 kHz – inaudible for most fans
        self._pwm = None
        self._current_speed: float = 0.0
        self._mock = not GPIO_AVAILABLE

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def setup(self, gpio_pin: int = 18, frequency: int = 25_000):
        if self._pwm is not None:
            self._pwm.stop()
            if not self._mock:
                GPIO.cleanup(self._gpio_pin)

        self._gpio_pin = gpio_pin
        self._frequency = frequency

        if self._mock:
            logger.info(f"[Mock] Fan on GPIO{gpio_pin} @ {frequency} Hz")
            return

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(gpio_pin, GPIO.OUT)
        self._pwm = GPIO.PWM(gpio_pin, frequency)
        self._pwm.start(0)
        logger.info(f"Fan PWM initialized: GPIO{gpio_pin} @ {frequency} Hz")

    def stop(self):
        self.set_speed(0.0)
        if self._pwm and not self._mock:
            self._pwm.stop()
            GPIO.cleanup(self._gpio_pin)
        self._pwm = None

    # ------------------------------------------------------------------
    # Speed control
    # ------------------------------------------------------------------

    def set_speed(self, speed: float):
        """Set fan speed 0.0 … 1.0."""
        speed = max(0.0, min(1.0, speed))
        self._current_speed = speed
        duty = speed * 100.0
        if not self._mock and self._pwm:
            self._pwm.ChangeDutyCycle(duty)
        logger.debug(f"Fan speed → {speed:.1%}  ({duty:.1f}% duty)")

    @property
    def current_speed(self) -> float:
        return self._current_speed

    # ------------------------------------------------------------------
    # Control algorithm
    # ------------------------------------------------------------------

    def calculate_speed(
        self,
        inside: dict,
        outside: dict | None,
        settings: dict,
    ) -> float:
        """
        Calculate the desired fan speed (0.0 … 1.0) based on sensor data.

        Parameters
        ----------
        inside   : dict with keys temperature, humidity
        outside  : dict with keys temperature, humidity  (may be None)
        settings : settings dict from the database
        """
        target_temp      = settings.get("target_temperature", 25.0)
        target_humidity  = settings.get("target_humidity", 65.0)
        temp_range       = settings.get("temp_control_range", 5.0)
        humidity_range   = settings.get("humidity_control_range", 20.0)
        fan_min          = settings.get("fan_min_speed", 0.2)
        fan_max          = settings.get("fan_max_speed", 1.0)
        mode             = settings.get("control_mode", "combined")

        if not inside:
            return 0.0

        i_temp = inside.get("temperature", 0.0)
        i_hum  = inside.get("humidity", 0.0)
        o_temp = outside.get("temperature", 9999.0) if outside else 9999.0
        o_hum  = outside.get("humidity", 9999.0)   if outside else 9999.0

        speed_temp = 0.0
        speed_hum  = 0.0

        if mode in ("temperature", "combined"):
            err = i_temp - target_temp
            # Exhaust fan cools only if outside is cooler than inside
            if err > 0 and o_temp < i_temp:
                speed_temp = min(1.0, err / temp_range)

        if mode in ("humidity", "combined"):
            err = i_hum - target_humidity
            # Exhaust fan reduces humidity only if outside is drier than inside
            if err > 0 and o_hum < i_hum:
                speed_hum = min(1.0, err / humidity_range)

        raw = max(speed_temp, speed_hum)

        if raw <= 0:
            return 0.0

        # Scale into [fan_min, fan_max]
        return fan_min + raw * (fan_max - fan_min)

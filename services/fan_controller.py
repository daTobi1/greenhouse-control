"""
Fan controller using RPi.GPIO PWM on a MOSFET-driven fan.

Fan orientation: EXHAUST – the fan pushes stale air OUT of the greenhouse.
Fresh outside air enters passively through vents/gaps. This means ventilating
only makes sense when outside conditions are actually better than inside:
  - Temperature:  outside must be cooler than inside
  - Humidity:     outside air must contain less water in absolute terms
                  (g/m³, Magnus). Comparing relative humidity is wrong:
                  cold saturated air holds far less water than warm air at
                  70 % RH and dries the greenhouse once it warms up.

Proportional control algorithm:
  - Computes a 0..1 speed from the temperature/humidity error vs. target.
  - Only runs the fan when outside air would improve the inside condition.
  - Scales the result into the configured [fan_min, fan_max] range.
  - When raw_speed <= 0 the fan is switched off completely.
"""

import logging
import threading
import time
from dataclasses import dataclass

from services import psychrometrics

logger = logging.getLogger(__name__)

try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.warning("RPi.GPIO not available – running in mock mode")


@dataclass(frozen=True)
class FanDecision:
    """Ergebnis der Regelentscheidung.

    Die Begründung wird bis ins Dashboard durchgereicht, damit sichtbar ist,
    warum der Lüfter steht – ein stiller Stillstand war bisher nicht von einem
    Defekt zu unterscheiden.
    """

    speed: float
    reason: str


class FanController:
    # Die Frostgrenze braucht ein Band, sonst schaltet der Lüfter bei exakt
    # der Grenztemperatur genauso im Minutentakt wie ohne Drehzahl-Hysterese.
    FROST_HYSTERESIS = 1.0

    def __init__(self):
        self._gpio_pin: int = 18
        self._frequency: int = 25_000   # 25 kHz – inaudible for most fans
        self._pwm = None
        self._current_speed: float = 0.0
        self._mock = not GPIO_AVAILABLE
        self._is_active: bool = False  # hysteresis state
        self._last_change: float | None = None   # time.monotonic() des letzten Zustandswechsels
        self._frost_blocked: bool = False
        self.kickstart_duration: float = 0.6
        self.last_reason: str = "idle"   # letzte Begründung, für die API

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
        """Set fan speed 0.0 … 1.0.

        Beim Anlaufen aus dem Stillstand wird kurz auf 100 % gefahren: viele
        DC-Lüfter kommen bei 20 % PWM nicht aus dem Stand und brummen nur.
        """
        speed = max(0.0, min(1.0, speed))
        kickstart = self._needs_kickstart(speed)
        self._current_speed = speed

        if self._mock or not self._pwm:
            logger.debug(f"Fan speed → {speed:.1%}")
            return

        if kickstart:
            self._pwm.ChangeDutyCycle(100.0)
            timer = threading.Timer(self.kickstart_duration, self._apply_duty, args=(speed,))
            timer.daemon = True
            timer.start()
            logger.debug(f"Fan kickstart {self.kickstart_duration:.1f}s → {speed:.1%}")
            return

        self._pwm.ChangeDutyCycle(speed * 100.0)
        logger.debug(f"Fan speed → {speed:.1%}")

    def _needs_kickstart(self, new_speed: float) -> bool:
        return (
            self.kickstart_duration > 0
            and new_speed > 0
            and self._current_speed == 0.0
        )

    def _apply_duty(self, speed: float):
        """Nach dem Kickstart auf die Zieldrehzahl gehen.

        Hat sich die Zielgeschwindigkeit inzwischen geändert, hat der neuere
        set_speed-Aufruf Vorrang.
        """
        if self._current_speed != speed:
            return
        if not self._mock and self._pwm:
            self._pwm.ChangeDutyCycle(speed * 100.0)

    @property
    def current_speed(self) -> float:
        return self._current_speed

    # ------------------------------------------------------------------
    # Control algorithm
    # ------------------------------------------------------------------

    def calculate_speed(
        self,
        inside: dict | None,
        outside: dict | None,
        settings: dict,
        now: float | None = None,
    ) -> FanDecision:
        """Gewünschte Lüfterdrehzahl und Begründung ermitteln.

        `now` ist eine monotone Zeit in Sekunden und wird nur für
        Mindestlaufzeit und Mindestpause gebraucht. Als Parameter, damit die
        Zeitlogik ohne Warten testbar ist.
        """
        now = time.monotonic() if now is None else now

        target_temp     = settings.get("target_temperature", 25.0)
        target_humidity = settings.get("target_humidity", 65.0)
        temp_range      = settings.get("temp_control_range", 5.0)
        humidity_range  = settings.get("humidity_control_range", 20.0)
        fan_min         = settings.get("fan_min_speed", 0.2)
        fan_max         = settings.get("fan_max_speed", 1.0)
        mode            = settings.get("control_mode", "combined_or")
        min_temp        = settings.get("fan_min_temperature", 5.0)
        abs_margin      = settings.get("humidity_abs_margin", 0.5)
        temp_guard      = settings.get("humidity_temp_guard", 3.0)
        hum_metric      = settings.get("humidity_metric", "relative")
        target_vpd      = settings.get("target_vpd", 0.95)
        vpd_range       = settings.get("vpd_control_range", 0.40)
        start_threshold = settings.get("fan_start_threshold", 0.10)
        stop_threshold  = settings.get("fan_stop_threshold", 0.03)
        min_runtime     = settings.get("fan_min_runtime", 120.0)
        min_pause       = settings.get("fan_min_pause", 60.0)

        if not inside:
            return self._force_off("no_inside_data", now)

        i_temp = inside.get("temperature", 0.0)
        i_hum  = inside.get("humidity", 0.0)

        if self._frost_blocked:
            if i_temp >= min_temp + self.FROST_HYSTERESIS:
                self._frost_blocked = False
        elif i_temp < min_temp:
            self._frost_blocked = True

        if self._frost_blocked:
            return self._force_off("frost", now)

        if not outside:
            return self._force_off("no_outside_data", now)

        o_temp = outside.get("temperature", 0.0)
        o_hum  = outside.get("humidity", 0.0)

        speed_temp = 0.0
        speed_hum  = 0.0

        if mode in ("temperature", "combined_or", "combined_and"):
            err = i_temp - target_temp
            # Abluftlüfter kühlt nur, wenn die Außenluft kälter ist.
            if err > 0 and o_temp < i_temp:
                speed_temp = min(1.0, err / temp_range)

        if mode in ("humidity", "combined_or", "combined_and"):
            if hum_metric == "vpd":
                # Nach Dampfdruckdefizit: zu feucht heißt VPD zu niedrig. Ein
                # zu hohes VPD (zu trockene Luft) ergibt einen negativen
                # Fehler – der Abluftlüfter kann nicht befeuchten.
                err = target_vpd - psychrometrics.vpd(i_temp, i_hum)
                span = vpd_range
            else:
                err = i_hum - target_humidity
                span = humidity_range
            # Entfeuchten kühlt zwangsläufig mit. Unterhalb des Schutzabstands
            # zum Temperatur-Sollwert wird deshalb nicht mehr entfeuchtet.
            warm_enough = i_temp > target_temp - temp_guard
            drier_outside = (
                psychrometrics.abs_humidity(o_temp, o_hum)
                < psychrometrics.abs_humidity(i_temp, i_hum) - abs_margin
            )
            if err > 0 and warm_enough and drier_outside and span > 0:
                speed_hum = min(1.0, err / span)

        if mode == "combined_and":
            raw = min(speed_temp, speed_hum)
        else:
            raw = max(speed_temp, speed_hum)

        # Zwei getrennte Schwellen: einschalten erst deutlich über dem
        # Sollwert, ausschalten erst deutlich darunter. Eine einzelne Schwelle
        # lässt den Lüfter am Sollwert im Minutentakt takten.
        should_run = raw > stop_threshold if self._is_active else raw >= start_threshold

        if should_run != self._is_active:
            elapsed = float("inf") if self._last_change is None else now - self._last_change
            required = min_runtime if self._is_active else min_pause
            if elapsed < required:
                if self._is_active:
                    return FanDecision(fan_min, "min_runtime")
                return FanDecision(0.0, "min_pause")
            self._is_active = should_run
            self._last_change = now

        if not self._is_active:
            return FanDecision(0.0, "idle")
        return FanDecision(fan_min + raw * (fan_max - fan_min), "auto")

    def _force_off(self, reason: str, now: float) -> FanDecision:
        """Sofort abschalten, ohne Rücksicht auf die Mindestlaufzeit.

        Frostschutz und fehlende Sensordaten sind Schutzabschaltungen – sie
        dürfen nicht durch eine laufende Mindestlaufzeit verzögert werden.
        """
        if self._is_active:
            self._is_active = False
            self._last_change = now
        return FanDecision(0.0, reason)

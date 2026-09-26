import math
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 44100
DURATION_SECONDS = 0.9
NOTES = ((523.25, 0.00), (659.25, 0.12), (783.99, 0.24))


def envelope(t: float, start: float) -> float:
    age = t - start
    if age < 0 or age > 0.62:
        return 0.0
    attack = min(1.0, age / 0.018)
    decay = math.exp(-5.2 * age)
    return attack * decay


def write_sound(destination: Path, notes: tuple[tuple[float, float], ...]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    for index in range(round(SAMPLE_RATE * DURATION_SECONDS)):
        t = index / SAMPLE_RATE
        sample = sum(
            math.sin(2 * math.pi * frequency * (t - start)) * envelope(t, start)
            for frequency, start in notes
        )
        sample += 0.18 * sum(
            math.sin(4 * math.pi * frequency * (t - start)) * envelope(t, start)
            for frequency, start in notes
        )
        value = max(-1.0, min(1.0, sample * 0.19))
        frames.extend(struct.pack("<h", round(value * 32767)))

    with wave.open(str(destination), "wb") as sound:
        sound.setnchannels(1)
        sound.setsampwidth(2)
        sound.setframerate(SAMPLE_RATE)
        sound.writeframes(frames)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    write_sound(root / "chrome-extension/sounds/chime.wav", NOTES)
    write_sound(
        root / "codex-notifier/sounds/finished.wav",
        ((587.33, 0.00), (880.00, 0.22)),
    )


if __name__ == "__main__":
    main()

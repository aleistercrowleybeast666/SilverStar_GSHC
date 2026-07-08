# processing

Offline flight-log processing module for the current AIR / GSP-MIN protocol only.

This version intentionally removes old AGP compatibility.

## Dependencies

```bash
pip install matplotlib pillow numpy
```

## Generate a 5 Hz fake log

```bash
python -m processing.fake_log_generator --output logs/fake_flight_log.jsonl --duration 45 --seed 42
```

The fake log uses the current parsed format:

- `layer = AIR_PARSED`
- `kind = FLIGHT_STATE`
- `kind = STATUS`
- 5 Hz `FLIGHT_STATE`

It also includes simplified current GSP link-quality records for RSSI/SNR plot testing.

## Process a log

```bash
python -m processing.flight_log_processor logs/fake_flight_log.jsonl --output-root data
```

Output directory:

```text
data/yyyy-mm-dd-n/
├─ processed_data.txt
├─ summary.txt
├─ accel.png
├─ gyro.png
├─ euler.png
├─ velocity.png
├─ position.png
├─ link_quality.png
├─ attitude_motion.gif
├─ gif_frames/
└─ manifest.json
```

## GIF rule

- If telemetry is approximately 5 Hz or faster, GIF frames use original sample timestamps.
- If telemetry is clearly below 5 Hz, frames are generated at the configured target FPS and data is interpolated.
- In large gaps/disconnections, interpolation holds the last valid sample instead of extrapolating.

Default target GIF FPS is 5.
